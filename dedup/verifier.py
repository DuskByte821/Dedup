"""Independent post-operation verification.

Re-scans the (modified) scope, re-runs the full analysis, checks that no
duplicates remain and that the entry count matches the expected unique count,
and finally verifies data preservation by comparing the unique entry set of
the output against the original dataset (backup for modified files, still-
original files for untouched ones).
"""

import hashlib
import tempfile
from pathlib import Path

from .scanner import scan
from .analyzer import analyze
from .index import Index
from .lineio import iter_lines


class VerificationError(Exception):
    pass


def verify(scope, backup_root, eligible_files, affected_files, empty_mode,
           expected_unique, extensions=None):
    # 1. Re-scan
    scan_result = scan(scope, extensions=extensions)
    original = {r for r, _ in eligible_files}
    current = {r for r, _ in scan_result.eligible}
    if original != current:
        extra = sorted(current - original)[:5]
        missing = sorted(original - current)[:5]
        parts = []
        if extra:
            parts.append(f"unexpected files {extra}")
        if missing:
            parts.append(f"missing files {missing}")
        raise VerificationError("Eligible file set changed: " + "; ".join(parts))

    # 2. Re-analyze output, check no duplicates remain and count matches.
    with tempfile.TemporaryDirectory() as tmp:
        out_idx = Index(Path(tmp) / 'out.db')
        orig_idx = Index(Path(tmp) / 'orig.db')
        try:
            out_analysis = analyze(scope, scan_result.eligible, out_idx,
                                   empty_mode=empty_mode)
            if out_analysis.internal_duplicates or out_analysis.cross_file_duplicates:
                raise VerificationError(
                    f"Duplicates remain: internal={out_analysis.internal_duplicates}, "
                    f"cross_file={out_analysis.cross_file_duplicates}")
            if out_analysis.total_entries != expected_unique:
                raise VerificationError(
                    f"Entry count mismatch: output has {out_analysis.total_entries}, "
                    f"expected {expected_unique}")

            # 3. Build the original unique entry set.
            _index_original(orig_idx, eligible_files, affected_files,
                            backup_root, empty_mode)

            # 4. Compare using SQL EXCEPT. Both sides store (hash, content).
            out_idx.conn.execute("ATTACH DATABASE ? AS orig",
                                 (str(orig_idx.path),))
            missing = out_idx.conn.execute(
                "SELECT COUNT(*) FROM ("
                "  SELECT hash, content FROM orig.entries "
                "  EXCEPT SELECT hash, content FROM main.entries)").fetchone()[0]
            if missing:
                raise VerificationError(
                    f"{missing} unique entries from the original are missing from output")
            extra = out_idx.conn.execute(
                "SELECT COUNT(*) FROM ("
                "  SELECT hash, content FROM main.entries "
                "  EXCEPT SELECT hash, content FROM orig.entries)").fetchone()[0]
            if extra:
                raise VerificationError(
                    f"{extra} entries appeared in output that were not in the original")
        finally:
            out_idx.close()
            orig_idx.close()

    return True


def _index_original(index, eligible_files, affected_files, backup_root, empty_mode):
    affected = set(affected_files)
    for rel_path, abs_path in eligible_files:
        source = (backup_root / rel_path) if rel_path in affected else abs_path
        line_no = 0
        with open(source, 'rb') as f:
            for content, _term_len in iter_lines(f):
                line_no += 1
                if len(content) == 0 and empty_mode in ('keep', 'drop'):
                    continue
                h = hashlib.sha256(content).digest()
                rows = index.lookup(h)
                if any(c == content for c, _, _ in rows):
                    continue
                index.add(h, content, rel_path, line_no)
    index.flush()
