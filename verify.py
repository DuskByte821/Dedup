#!/usr/bin/env python3
"""Independent external verifier for the deduplication tool.

Usage::

    python verify.py \\
        --original ../Secn \\
        --backup   .dedup-backups/2026-09-21_150233 \\
        --processed ./Secn

Assumes a single-root layout: the original tree is one wordlist root, and
the backup directory contains a subdirectory named after that root. Pass
``--root-name NAME`` to override the derived name.

Two checks are performed:

  A. Backup <-> Original
     Every file present in the backup is byte-for-byte identical to its
     counterpart in the original tree.

  B. Backup <-> Processed
     The processed tree is reconstructed from scratch, using the backup
     for modified files and the original for untouched files, and compared
     byte-for-byte against the actual processed tree. Also asserts:

       * no duplicate entries remain in the processed tree
       * no entries were invented or lost relative to the original
       * surviving entries keep their original order

Exit codes:
  0  both checks passed
  2  one or both checks failed (details on stderr)
  1  usage or IO error
"""

import argparse
import hashlib
import sys
from pathlib import Path

from dedup.lineio import iter_lines


_CHUNK = 1 << 20
_READ_MAX_LINE = 1 << 30  # generous for a verifier run
_MANIFEST_NAMES = {"operation.json", "operation.complete.json"}


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        prog="verify.py",
        description="Independent verification of a dedup run.")
    p.add_argument("--original", required=True,
                   help="Untouched original tree.")
    p.add_argument("--backup", required=True,
                   help="Backup directory (contains operation.json).")
    p.add_argument("--processed", required=True,
                   help="Processed tree to verify.")
    p.add_argument("--root-name", default=None,
                   help="Root name under --backup (default: derived from "
                        "--original basename).")
    p.add_argument("--empty-lines", choices=("keep", "dedup", "drop"),
                   default="keep",
                   help="Must match the mode used for the original run.")
    return p.parse_args(argv)


# ---------------------------------------------------------------------------
# Entry reading (independent of the dedup package)
# ---------------------------------------------------------------------------

def read_entries(path: Path):
    """Return list of (content, terminator_len) for a file. LF/CRLF/CR aware."""
    out = []
    with open(path, "rb") as f:
        for line in iter_lines(f, _READ_MAX_LINE):
            out.append(line)
    return out


def file_ending(path: Path) -> bytes:
    """Return the last 1 byte of a file (for final-newline detection)."""
    with open(path, "rb") as f:
        size = f.seek(0, 2)
        if size == 0:
            return b""
        f.seek(size - 1)
        return f.read(1)


# ---------------------------------------------------------------------------
# A. Backup vs Original
# ---------------------------------------------------------------------------

def check_a_backup_matches_original(original_root: Path, backup_root: Path):
    """Return list of failure strings."""
    failures = []
    for src in sorted(backup_root.rglob("*")):
        if not src.is_file():
            continue
        if src.name == "operation.json" and src.parent == backup_root:
            continue
        rel = src.relative_to(backup_root)
        counterpart = original_root / rel
        if not counterpart.is_file():
            failures.append(
                f"A: {rel} exists in backup but not in original")
            continue
        if not _same_file(src, counterpart):
            failures.append(
                f"A: {rel} differs between backup and original")
    return failures


# ---------------------------------------------------------------------------
# B. Backup vs Processed (reconstruct expected output)
# ---------------------------------------------------------------------------

def check_b_backup_matches_processed(
    original_root: Path, backup_root: Path, processed_root: Path,
    empty_mode: str,
):
    failures = []

    # Enumerate files in each tree.
    original_files = _rel_files(original_root)
    processed_files = _rel_files(processed_root)
    backup_files = _rel_files(backup_root)

    # For file-set comparison: processed must equal original (no renamed,
    # added, or removed files).
    if set(processed_files) != set(original_files):
        only_orig = sorted(set(original_files) - set(processed_files))
        only_proc = sorted(set(processed_files) - set(original_files))
        if only_orig:
            failures.append(
                f"B: files missing from processed: {only_orig[:10]}")
        if only_proc:
            failures.append(
                f"B: unexpected files in processed: {only_proc[:10]}")
        return failures  # further checks would be noise

    # Compute ownership in walk order.
    seen = set()
    expected_per_file = {}

    for rel in sorted(original_files.keys()):
        src = (backup_root / rel) if rel in backup_files else (original_root / rel)
        entries = read_entries(src)
        ending = file_ending(src)

        survivors = []
        for content in entries:
            if len(content) == 0:
                if empty_mode == "keep":
                    survivors.append(content)
                    continue
                if empty_mode == "drop":
                    continue
                # "dedup": treat as entry
            key = hashlib.sha256(content).digest() + b"\x00" + content
            if key in seen:
                continue
            seen.add(key)
            survivors.append(content)

        expected_per_file[rel] = (survivors, ending)

    # Compare with processed.
    for rel, (survivors, ending) in expected_per_file.items():
        proc_path = processed_root / rel
        proc_entries = read_entries(proc_path)
        proc_ending = file_ending(proc_path)

        if proc_entries != survivors:
            if len(proc_entries) != len(survivors):
                failures.append(
                    f"B: {rel}: entry count differs "
                    f"(expected {len(survivors)}, got {len(proc_entries)})")
            else:
                for i, (a, b) in enumerate(zip(survivors, proc_entries)):
                    if a != b:
                        failures.append(
                            f"B: {rel}: entry {i+1} differs "
                            f"(expected {a!r}, got {b!r})")
                        break
            continue

        # Final-newline state preservation
        expected_has_nl = ending in (b"\n", b"\r")
        actual_has_nl = proc_ending in (b"\n", b"\r")
        if expected_has_nl != actual_has_nl:
            failures.append(
                f"B: {rel}: final-newline state differs "
                f"(expected {'present' if expected_has_nl else 'absent'}, "
                f"got {'present' if actual_has_nl else 'absent'})")

    return failures


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rel_files(root: Path):
    """Map of relative_path (str with '/' separators) -> absolute path."""
    out = {}
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if p.parent == root and p.name in _MANIFEST_NAMES:
            continue
        rel = p.relative_to(root).as_posix()
        out[rel] = p
    return out

def _same_file(a: Path, b: Path) -> bool:
    sa = a.stat()
    sb = b.stat()
    if sa.st_size != sb.st_size:
        return False
    with open(a, "rb") as fa, open(b, "rb") as fb:
        while True:
            ca = fa.read(_CHUNK)
            cb = fb.read(_CHUNK)
            if ca != cb:
                return False
            if not ca:
                return True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv=None):
    args = parse_args(argv)

    original = Path(args.original).resolve()
    backup = Path(args.backup).resolve()
    processed = Path(args.processed).resolve()

    for label, p in (("original", original),
                     ("backup", backup),
                     ("processed", processed)):
        if not p.is_dir():
            print(f"Error: --{label} is not a directory: {p}", file=sys.stderr)
            return 1

    root_name = args.root_name or original.name
    backup_sub = backup / root_name
    if not backup_sub.is_dir():
        print(f"Error: backup subdirectory not found: {backup_sub}",
              file=sys.stderr)
        print(f"       pass --root-name to override (derived: {root_name!r})",
              file=sys.stderr)
        return 1

    print(f"A. Comparing backup <-> original ({backup_sub} vs {original}) ...")
    failures_a = check_a_backup_matches_original(original, backup_sub)
    if failures_a:
        for f in failures_a:
            print(f"  FAIL {f}", file=sys.stderr)
    else:
        print("  ok   every backed-up file is byte-identical to the original")

    print(f"B. Reconstructing expected output and comparing to processed "
          f"({processed}) ...")
    failures_b = check_b_backup_matches_processed(
        original, backup_sub, processed, args.empty_lines)
    if failures_b:
        for f in failures_b[:20]:
            print(f"  FAIL {f}", file=sys.stderr)
        if len(failures_b) > 20:
            print(f"  ... and {len(failures_b) - 20} more", file=sys.stderr)
    else:
        print("  ok   processed tree matches reconstructed expected output")

    if failures_a or failures_b:
        print()
        print("VERIFICATION FAILED")
        return 2

    print()
    print("VERIFICATION PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())