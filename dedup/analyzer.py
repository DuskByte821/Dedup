"""Streaming duplicate analysis. Read-only.

Classification:
  * internal duplicate — the earlier occurrence is in the same file
  * cross-file duplicate — the earlier occurrence is in another file

Ownership is deterministic: files are processed in sorted rel-path order, and
within a file the first occurrence wins.
"""

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from .lineio import iter_lines


@dataclass
class AnalysisResult:
    total_entries: int = 0
    unique_entries: int = 0
    internal_duplicates: int = 0
    cross_file_duplicates: int = 0
    bytes_before: int = 0
    bytes_after: int = 0
    files_affected: set = field(default_factory=set)
    per_file: dict = field(default_factory=dict)
    empty_lines_kept: int = 0
    empty_lines_dropped: int = 0


def _sha256(data):
    return hashlib.sha256(data).digest()


def analyze(scope, eligible_files, index, empty_mode='keep', verbose_callback=None):
    result = AnalysisResult()

    for rel_path, abs_path in eligible_files:
        file_entries = 0
        file_dupes = 0
        file_bytes_before = 0
        file_bytes_after = 0
        line_no = 0

        with open(abs_path, 'rb') as f:
            for content, term_len in iter_lines(f):
                line_no += 1
                file_bytes_before += len(content) + term_len

                if len(content) == 0:
                    if empty_mode == 'keep':
                        result.empty_lines_kept += 1
                        file_bytes_after += 1
                        continue
                    if empty_mode == 'drop':
                        result.empty_lines_dropped += 1
                        continue
                    # empty_mode == 'dedup': fall through

                h = _sha256(content)
                rows = index.lookup(h)
                is_dup = False
                for c, fpath, _fline in rows:
                    if c == content:
                        is_dup = True
                        if fpath == rel_path:
                            result.internal_duplicates += 1
                        else:
                            result.cross_file_duplicates += 1
                        file_dupes += 1
                        break

                if not is_dup:
                    index.add(h, content, rel_path, line_no)
                    result.unique_entries += 1
                    file_bytes_after += len(content) + 1  # LF terminator

                result.total_entries += 1
                file_entries += 1

        result.bytes_before += file_bytes_before
        result.bytes_after += file_bytes_after
        if file_dupes > 0:
            result.files_affected.add(rel_path)
        result.per_file[rel_path] = {
            'entries': file_entries,
            'duplicates': file_dupes,
            'bytes_before': file_bytes_before,
            'bytes_after': file_bytes_after,
        }

        if verbose_callback:
            verbose_callback(rel_path, file_entries, file_dupes)

    index.flush()
    return result
