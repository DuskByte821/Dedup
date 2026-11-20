"""Post-modification verification. Read-only.

Runs after the processor has rewritten files. Re-derives the entire state
from scratch and compares against what the original analysis said should
happen. Any mismatch is a hard failure.

Checks
------
Per root:
  * re-analyze the modified tree -> zero internal duplicates
  * re-analyze the modified tree -> zero cross-file duplicates
  * modified unique-entry count == original unique-entry count
  * modified unique-entry set == original unique-entry set
  * every recorded backup file hashes to the value in operation.json
  * every untouched file is unchanged (size, mtime_ns)

Across roots:
  * entries present in >=2 roots originally remain present in each of
    those roots
"""

import hashlib
import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set

from . import backup as backup_mod
from . import scanner
from .analyzer import AnalysisResult, analyze_roots
from .index import Index
from .scanner import ScanResult


class VerificationError(Exception):
    """Raised when verification fails. Caller must report failure."""


_READ_CHUNK = 1 << 20


@dataclass
class VerificationReport:
    passed: bool = False
    failures: List[str] = field(default_factory=list)
    per_root_notes: Dict[str, List[str]] = field(default_factory=dict)


ProgressFn = Callable[[str], None]


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def verify_operation(
    scan_results: List[ScanResult],
    analysis: AnalysisResult,
    original_index: Index,
    modified: Dict[str, List[str]],
    plan: backup_mod.BackupPlan,
    empty_mode: str,
    max_line_bytes: int,
    extensions: Optional[Set[str]] = None,
    progress: Optional[ProgressFn] = None,
    work_dir: Optional[Path] = None,
) -> VerificationReport:
    report = VerificationReport()
    _note = lambda msg: (progress(msg) if progress else None)

    # ---- 1. Backup intact -------------------------------------------------
    _note("Verifying backup integrity (re-hashing each backed-up file) ...")
    _verify_backup_intact(plan, report)

    # ---- 2. Re-analyze modified tree -------------------------------------
    _note("Re-scanning and re-analyzing the modified tree ...")
    fresh_scans = []
    for sr in scan_results:
        nsr = scanner.scan(
            sr.root_name, sr.root_path,
            extensions=extensions or {".txt", ".lst", ".wordlist",
                                       ".words", ".dict", ".dic"},
        )
        fresh_scans.append(nsr)

    with tempfile.TemporaryDirectory(
            prefix="dedup-verify-",
            dir=str(work_dir) if work_dir else None) as td:
        fresh_index = Index(Path(td) / "fresh.db")
        attached = False
        try:
            fresh_analysis = analyze_roots(
                fresh_scans, fresh_index,
                empty_mode=empty_mode,
                max_line_bytes=max_line_bytes,
            )

            # Entry-set comparisons are done inside SQLite (streaming), never
            # by loading every entry into Python sets.
            original_index.flush()
            fresh_index.flush()
            original_index.conn.execute(
                "ATTACH DATABASE ? AS fresh", (str(fresh_index.path),))
            attached = True

            # ---- 3. Per-root comparisons ---------------------------------
            _note("Comparing per-root entry sets ...")
            _compare_roots(
                scan_results, analysis, original_index,
                fresh_scans, fresh_analysis, fresh_index,
                report,
            )

            # ---- 4. Cross-root preservation ------------------------------
            _note("Checking cross-root preservation ...")
            _check_cross_root_preservation(
                scan_results, analysis, original_index,
                fresh_scans, fresh_analysis, fresh_index,
                report,
            )
        finally:
            if attached:
                try:
                    original_index.conn.execute("DETACH DATABASE fresh")
                except Exception:
                    pass
            fresh_index.close()

    # ---- 5. Untouched files unchanged ------------------------------------
    _note("Verifying untouched files are unchanged ...")
    _check_untouched_files(scan_results, analysis, modified, report)

    report.passed = not report.failures
    return report


# ---------------------------------------------------------------------------
# 1. Backup integrity
# ---------------------------------------------------------------------------

def _verify_backup_intact(plan: backup_mod.BackupPlan,
                          report: VerificationReport) -> None:
    op_path = plan.root / "operation.json"
    if not op_path.is_file():
        report.failures.append(
            f"operation.json missing from backup directory: {plan.root}")
        return

    try:
        op = json.loads(op_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        report.failures.append(f"Cannot read operation.json: {e}")
        return

    if op.get("phase") != "backup_complete":
        report.failures.append(
            f"operation.json phase is {op.get('phase')!r}, expected "
            f"'backup_complete'")

    entries = op.get("files")
    if not isinstance(entries, list):
        report.failures.append("operation.json has no 'files' array")
        return

    for rec in entries:
        try:
            root_name = rec["root"]
            rel_path = rec["rel_path"]
            want_hex = rec["sha256"]
        except (KeyError, TypeError):
            report.failures.append(f"operation.json file record malformed: {rec!r}")
            continue

        dest = plan.root / root_name / rel_path
        if not dest.is_file():
            report.failures.append(
                f"Backup file missing: {root_name}/{rel_path}")
            continue

        try:
            actual = _hash_file(dest).hex()
        except OSError as e:
            report.failures.append(
                f"Cannot re-hash backup file {dest}: {e}")
            continue

        if actual != want_hex:
            report.failures.append(
                f"Backup hash mismatch for {root_name}/{rel_path}: "
                f"manifest says {want_hex}, actual is {actual}")


# ---------------------------------------------------------------------------
# 3. Per-root comparison
# ---------------------------------------------------------------------------

def _compare_roots(
    orig_scans, orig_analysis, orig_index,
    fresh_scans, fresh_analysis, fresh_index,
    report: VerificationReport,
) -> None:
    for orig_sr in orig_scans:
        name = orig_sr.root_name
        orig_ra = orig_analysis.roots[name]
        fresh_ra = fresh_analysis.roots.get(name)
        if fresh_ra is None:
            report.failures.append(f"Root {name!r} missing from re-scan")
            continue

        notes = report.per_root_notes.setdefault(name, [])

        if fresh_ra.internal_duplicates or fresh_ra.cross_file_duplicates:
            report.failures.append(
                f"Root {name!r}: duplicates remain after modification "
                f"(internal={fresh_ra.internal_duplicates}, "
                f"cross_file={fresh_ra.cross_file_duplicates})")

        if fresh_ra.unique_entries != orig_ra.unique_entries:
            report.failures.append(
                f"Root {name!r}: unique entry count changed "
                f"{orig_ra.unique_entries} -> {fresh_ra.unique_entries}")

        if fresh_ra.total_entries != fresh_ra.unique_entries:
            report.failures.append(
                f"Root {name!r}: total entries != unique entries "
                f"after modification ({fresh_ra.total_entries} vs "
                f"{fresh_ra.unique_entries})")

        orig_rid = orig_index.root_id(name)
        fresh_rid = _fresh_root_id(fresh_index, name)

        n_missing, missing = _count_missing(orig_index, "main", orig_rid,
                                            "fresh", fresh_rid)
        n_extra, extra = _count_missing(orig_index, "fresh", fresh_rid,
                                        "main", orig_rid)

        if n_missing:
            sample = ", ".join(repr(c[:32]) for c in missing[:3])
            report.failures.append(
                f"Root {name!r}: {n_missing} original entries missing "
                f"from output (sample: {sample})")
        if n_extra:
            sample = ", ".join(repr(c[:32]) for c in extra[:3])
            report.failures.append(
                f"Root {name!r}: {n_extra} entries in output that were "
                f"not in the original (sample: {sample})")

        if not any(f"Root {name!r}" in f for f in report.failures):
            notes.append(f"entry set preserved ({fresh_ra.unique_entries} entries)")


def _fresh_root_id(fresh_index, name):
    try:
        return fresh_index.root_id(name)
    except Exception:
        return -1   # root absent from the re-scan: matches no rows


def _count_missing(conn_index, src_schema, src_rid, other_schema, other_rid,
                   sample_size=3):
    """Count entries of (src_schema, src_rid) absent from (other_schema,
    other_rid). Streams inside SQLite; memory use stays constant.

    Returns (count, [sample contents]).
    """
    cur = conn_index.conn.execute(
        f"SELECT s.content FROM {src_schema}.unique_entries AS s "
        f"WHERE s.root_id = ? AND NOT EXISTS ("
        f"  SELECT 1 FROM {other_schema}.unique_entries AS o "
        f"  WHERE o.root_id = ? AND o.hash = s.hash AND o.content = s.content)",
        (src_rid, other_rid))
    count = 0
    samples = []
    for (content,) in cur:
        count += 1
        if len(samples) < sample_size:
            samples.append(bytes(content))
    return count, samples


# ---------------------------------------------------------------------------
# 4. Cross-root preservation
# ---------------------------------------------------------------------------

def _count_shared_missing(orig_index, rid_a, rid_b, fresh_rid, sample_size=3):
    """Entries present in the original in both root A and root B but absent
    from the re-scanned root (fresh_rid)."""
    cur = orig_index.conn.execute(
        "SELECT oa.content FROM main.unique_entries AS oa "
        "JOIN main.unique_entries AS ob "
        "  ON ob.root_id = ? AND ob.hash = oa.hash AND ob.content = oa.content "
        "WHERE oa.root_id = ? AND NOT EXISTS ("
        "  SELECT 1 FROM fresh.unique_entries AS f "
        "  WHERE f.root_id = ? AND f.hash = oa.hash AND f.content = oa.content)",
        (rid_b, rid_a, fresh_rid))
    count = 0
    samples = []
    for (content,) in cur:
        count += 1
        if len(samples) < sample_size:
            samples.append(bytes(content))
    return count, samples


def _check_cross_root_preservation(
    orig_scans, orig_analysis, orig_index,
    fresh_scans, fresh_analysis, fresh_index,
    report: VerificationReport,
) -> None:
    names = [sr.root_name for sr in orig_scans]
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            rid_a = orig_index.root_id(a)
            rid_b = orig_index.root_id(b)
            n_a, sample_a = _count_shared_missing(
                orig_index, rid_a, rid_b, _fresh_root_id(fresh_index, a))
            n_b, sample_b = _count_shared_missing(
                orig_index, rid_a, rid_b, _fresh_root_id(fresh_index, b))
            if n_a:
                sample = ", ".join(repr(c[:32]) for c in sample_a)
                report.failures.append(
                    f"Cross-root preservation: {n_a} entries "
                    f"shared between {a!r} and {b!r} are missing from {a!r} "
                    f"(sample: {sample}). Cross-root deduplication is not "
                    f"permitted.")
            if n_b:
                sample = ", ".join(repr(c[:32]) for c in sample_b)
                report.failures.append(
                    f"Cross-root preservation: {n_b} entries "
                    f"shared between {a!r} and {b!r} are missing from {b!r} "
                    f"(sample: {sample})")


# ---------------------------------------------------------------------------
# 5. Untouched files
# ---------------------------------------------------------------------------

def _check_untouched_files(
    scan_results, analysis: AnalysisResult,
    modified: Dict[str, List[str]], report: VerificationReport,
) -> None:
    for sr in scan_results:
        modified_set = set(modified.get(sr.root_name, []))
        for ef in sr.eligible:
            if ef.rel_path in modified_set:
                continue
            try:
                st = ef.abs_path.stat()
            except OSError as e:
                report.failures.append(
                    f"Root {sr.root_name!r}: cannot stat untouched "
                    f"{ef.rel_path!r}: {e}")
                continue
            if st.st_size != ef.size or st.st_mtime_ns != ef.mtime_ns:
                report.failures.append(
                    f"Root {sr.root_name!r}: untouched file {ef.rel_path!r} "
                    f"changed since scan "
                    f"(size {ef.size}->{st.st_size}, "
                    f"mtime_ns {ef.mtime_ns}->{st.st_mtime_ns})")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _hash_file(path: Path) -> bytes:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(_READ_CHUNK)
            if not chunk:
                break
            h.update(chunk)
    return h.digest()