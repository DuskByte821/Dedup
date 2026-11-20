"""Streaming duplicate analysis. Read-only. Root-scoped.

For each root, files are processed in sorted relative-path order and lines
are read top to bottom. The first occurrence of an entry within a root
becomes its owner; later occurrences in that root are duplicates.

Classification
--------------
    same file as owner         -> internal duplicate
    different file, same root  -> cross-file duplicate

Across roots there is no classification: the two occurrences are unrelated.
"""

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Set

from .lineio import iter_lines
from .scanner import ScanResult


DEFAULT_MAX_LINE_BYTES = 16 * 1024 * 1024
VALID_EMPTY_MODES = ("keep", "dedup", "drop")

# Scanner issue kinds that describe a file (not a directory).
_FILE_ISSUE_KINDS = frozenset({
    "skipped_extension", "symlink_file", "unreadable_file", "non_regular",
    "stat_error",
})


class AnalysisError(Exception):
    """Raised when the analyzer cannot proceed (read error, invalid state)."""


@dataclass
class FileAnalysis:
    rel_path: str
    entries: int = 0            # non-empty lines counted as entries
    duplicates: int = 0
    internal_duplicates: int = 0
    cross_file_duplicates: int = 0
    bytes_before: int = 0       # st_size
    bytes_after: int = 0        # expected bytes if the file is rewritten
    empty_lines_kept: int = 0
    empty_lines_dropped: int = 0


@dataclass
class RootAnalysis:
    root_name: str
    root_path: Path

    directories_discovered: int = 0
    files_discovered: int = 0
    files_eligible: int = 0
    files_skipped: int = 0
    files_unreadable: int = 0
    symlinks_skipped: int = 0

    total_entries: int = 0
    unique_entries: int = 0
    internal_duplicates: int = 0
    cross_file_duplicates: int = 0

    bytes_before: int = 0
    bytes_after: int = 0

    empty_lines_kept: int = 0
    empty_lines_dropped: int = 0

    files_affected: Set[str] = field(default_factory=set)
    files_emptied: Set[str] = field(default_factory=set)
    per_file: Dict[str, FileAnalysis] = field(default_factory=dict)

    @property
    def duplicates(self) -> int:
        return self.internal_duplicates + self.cross_file_duplicates


@dataclass
class AnalysisResult:
    roots: Dict[str, RootAnalysis] = field(default_factory=dict)

    @property
    def total_files_affected(self) -> int:
        return sum(len(r.files_affected) for r in self.roots.values())

    @property
    def total_entries(self) -> int:
        return sum(r.total_entries for r in self.roots.values())

    @property
    def total_unique(self) -> int:
        return sum(r.unique_entries for r in self.roots.values())

    @property
    def total_internal_duplicates(self) -> int:
        return sum(r.internal_duplicates for r in self.roots.values())

    @property
    def total_cross_file_duplicates(self) -> int:
        return sum(r.cross_file_duplicates for r in self.roots.values())

    @property
    def total_duplicates(self) -> int:
        return self.total_internal_duplicates + self.total_cross_file_duplicates

    @property
    def total_bytes_before(self) -> int:
        return sum(r.bytes_before for r in self.roots.values())

    @property
    def total_bytes_after(self) -> int:
        return sum(r.bytes_after for r in self.roots.values())

    def any_duplicates(self) -> bool:
        return any(r.files_affected for r in self.roots.values())


ProgressCallback = Callable[[str, str, int, int], None]
# (root_name, rel_path, index_within_root_1based, total_in_root)


def analyze_roots(
    scan_results: Iterable[ScanResult],
    index,
    empty_mode: str = "keep",
    max_line_bytes: int = DEFAULT_MAX_LINE_BYTES,
    progress: Optional[ProgressCallback] = None,
) -> AnalysisResult:
    if empty_mode not in VALID_EMPTY_MODES:
        raise ValueError(
            f"invalid empty_mode: {empty_mode!r}; "
            f"expected one of {VALID_EMPTY_MODES}")

    result = AnalysisResult()
    for scan in scan_results:
        root = _analyze_one_root(
            scan, index, empty_mode, max_line_bytes, progress)
        result.roots[scan.root_name] = root
    return result


def _analyze_one_root(scan_result, index, empty_mode, max_line_bytes, progress):
    files_discovered = len(scan_result.eligible) + sum(
        1 for i in scan_result.issues if i.kind in _FILE_ISSUE_KINDS)

    root = RootAnalysis(
        root_name=scan_result.root_name,
        root_path=scan_result.root_path,
        directories_discovered=scan_result.directories_discovered,
        files_discovered=files_discovered,
        files_eligible=len(scan_result.eligible),
        files_skipped=len(scan_result.skipped),
        files_unreadable=len(scan_result.unreadable),
        symlinks_skipped=len(scan_result.symlinks),
    )

    root_id = index.ensure_root(scan_result.root_name, str(scan_result.root_path))

    total = len(scan_result.eligible)
    for i, ef in enumerate(scan_result.eligible):
        file_id = index.register_file(
            root_id, ef.rel_path, ef.size, ef.mtime_ns)
        fa = FileAnalysis(rel_path=ef.rel_path, bytes_before=ef.size)

        try:
            f = open(ef.abs_path, "rb")
        except OSError as e:
            raise AnalysisError(
                f"root {scan_result.root_name!r}: cannot open "
                f"{ef.rel_path!r}: {e}")

        try:
            with f:
                line_no = 0
                for content in iter_lines(f, max_line_bytes):
                    line_no += 1

                    # Empty lines are only "entries" in dedup mode.
                    if len(content) == 0:
                        if empty_mode == "keep":
                            root.empty_lines_kept += 1
                            fa.empty_lines_kept += 1
                            fa.bytes_after += 1
                            continue
                        if empty_mode == "drop":
                            root.empty_lines_dropped += 1
                            fa.empty_lines_dropped += 1
                            continue
                        # empty_mode == "dedup": fall through as an entry

                    h = hashlib.sha256(content).digest()
                    owner = index.lookup(root_id, h, content)
                    if owner is None:
                        index.add(root_id, h, content, file_id, line_no)
                        root.unique_entries += 1
                        fa.bytes_after += len(content) + 1
                    else:
                        owner_file_id, _ = owner
                        if owner_file_id == file_id:
                            root.internal_duplicates += 1
                            fa.internal_duplicates += 1
                        else:
                            root.cross_file_duplicates += 1
                            fa.cross_file_duplicates += 1
                        fa.duplicates += 1
                        root.files_affected.add(ef.rel_path)

                    root.total_entries += 1
                    fa.entries += 1
        except OSError as e:
            raise AnalysisError(
                f"root {scan_result.root_name!r}: error reading "
                f"{ef.rel_path!r}: {e}")

        root.bytes_before += fa.bytes_before
        root.bytes_after += fa.bytes_after
        root.per_file[ef.rel_path] = fa

        if progress is not None:
            progress(scan_result.root_name, ef.rel_path, i + 1, total)

    # A file is "emptied" only if it once had content and now has none.
    for rel in root.files_affected:
        fa = root.per_file[rel]
        if fa.bytes_after == 0 and fa.bytes_before > 0:
            root.files_emptied.add(rel)

    index.flush()
    return root