"""Human-readable reporting. Read-only.

The report is per-root. The TOTAL block is arithmetic on already-computed
per-root numbers and explicitly disclaims cross-root uniqueness.
"""

import shutil
from pathlib import Path
from typing import Dict, List

from .analyzer import AnalysisResult, RootAnalysis
from .safety import format_bytes
from .scanner import ScanResult


_WIDTH = 78
_SKIP_SAMPLE_LIMIT = 12


def _sep(ch: str = "-") -> str:
    return ch * _WIDTH


def _hdr(title: str) -> None:
    print(_sep("="))
    print(title)
    print(_sep("="))


def _root_hdr(name: str) -> None:
    print(_sep("-"))
    print(f"ROOT: {name}")
    print(_sep("-"))


def _avail_bytes_for(path: Path) -> int:
    try:
        return shutil.disk_usage(str(path.parent)).free
    except OSError:
        return -1


def print_report(
    scan_results: List[ScanResult],
    analysis: AnalysisResult,
    config: Dict,
    dry_run: bool,
) -> None:
    print()
    _hdr("Deduplication Report")
    print()

    # -------------------------------------------------------------- Config
    print("Configuration")
    print("  Roots:")
    for sr in scan_results:
        print(f"    {sr.root_name} -> {sr.root_path}")
    exts = ", ".join(sorted(config.get("extensions") or []))
    print(f"  Eligible extensions: {exts}")
    print(f"  Empty-line policy:   {config.get('empty_mode', 'keep')}")
    print(f"  Follow symlinks:     "
          f"{'yes' if config.get('follow_symlinks') else 'no'}")
    if config.get("max_line_bytes"):
        print(f"  Max line bytes:      {config['max_line_bytes']:,}")
    print()

    # ------------------------------------------------------------- Per root
    for sr in scan_results:
        ra = analysis.roots[sr.root_name]
        _print_root(sr, ra, config)
        print()

    # ------------------------------------------------------------- Totals
    _print_totals(scan_results, analysis)
    print()

    # ------------------------------------------------------------- Issues
    _print_issues(scan_results)
    print()

    if dry_run:
        print("[DRY RUN] No files will be modified.")
    else:
        print("No files have been modified yet.")
    print()


# ---------------------------------------------------------------------------
# Per-root block
# ---------------------------------------------------------------------------

def _print_root(sr: ScanResult, ra: RootAnalysis, config: Dict) -> None:
    _root_hdr(sr.root_name)
    print(f"Scope:                   {sr.root_path}")
    print(f"Directories discovered:  {ra.directories_discovered:,}")
    print(f"Files discovered:        {ra.files_discovered:,}")
    print(f"Files eligible:          {ra.files_eligible:,}")
    print(f"Files skipped:           {ra.files_skipped:,}")
    print(f"Files unreadable:        {ra.files_unreadable:,}")
    print(f"Symlinks skipped:        {ra.symlinks_skipped:,}")
    print()

    print(f"Total entries:           {ra.total_entries:,}")
    print(f"Unique entries:          {ra.unique_entries:,}")
    print(f"Internal duplicates:     {ra.internal_duplicates:,}")
    print(f"Cross-file duplicates:   {ra.cross_file_duplicates:,}")
    print(f"Duplicate entries:       {ra.duplicates:,}")
    print()

    affected = len(ra.files_affected)
    print(f"Files affected:          {affected:,} of {ra.files_eligible:,}")
    if ra.files_emptied:
        print(f"Files emptied:           {len(ra.files_emptied):,}")

    removed = _removed_bytes(ra)
    print(f"Estimated bytes removed: {format_bytes(removed)}")

    avail = _avail_bytes_for(sr.root_path)
    if avail >= 0:
        print(f"Disk available:          {format_bytes(avail)} "
              f"(at {sr.root_path.parent})")

    if config.get("empty_mode") == "keep":
        if ra.empty_lines_kept:
            print(f"Empty lines preserved:   {ra.empty_lines_kept:,}")
    if config.get("empty_mode") == "drop":
        if ra.empty_lines_dropped:
            print(f"Empty lines dropped:     {ra.empty_lines_dropped:,}")


def _removed_bytes(ra: RootAnalysis) -> int:
    """Sum of (bytes_before - bytes_after) across affected files only."""
    total = 0
    for rel in ra.files_affected:
        fa = ra.per_file.get(rel)
        if fa is None:
            continue
        delta = fa.bytes_before - fa.bytes_after
        if delta > 0:
            total += delta
    return total


# ---------------------------------------------------------------------------
# Totals block
# ---------------------------------------------------------------------------

def _print_totals(scan_results: List[ScanResult],
                  analysis: AnalysisResult) -> None:
    _root_hdr("TOTAL (informational only)")

    files_eligible = sum(len(sr.eligible) for sr in scan_results)
    files_affected = analysis.total_files_affected
    files_emptied = sum(len(r.files_emptied) for r in analysis.roots.values())

    print(f"Roots:                   {len(scan_results):,}")
    print(f"Files eligible:          {files_eligible:,}")
    print(f"Files affected:          {files_affected:,}")
    if files_emptied:
        print(f"Files emptied:           {files_emptied:,}")
    print(f"Total entries:           {analysis.total_entries:,}")
    print(f"Unique entries:          {analysis.total_unique:,}")
    print(f"Duplicate entries:       {analysis.total_duplicates:,}")
    print(f"Estimated bytes removed: "
          f"{format_bytes(sum(_removed_bytes(r) for r in analysis.roots.values()))}")
    print()
    print("NOTE: The total is a sum of independent per-root statistics. An")
    print("      entry appearing in more than one root is NOT a duplicate")
    print("      and is NOT counted as one.")


# ---------------------------------------------------------------------------
# Issues block
# ---------------------------------------------------------------------------

def _print_issues(scan_results: List[ScanResult]) -> None:
    any_issues = any(sr.issues for sr in scan_results)
    print("Files with issues")
    if not any_issues:
        print("  (none)")
        return

    for sr in scan_results:
        if not sr.issues:
            continue
        print(f"  ROOT {sr.root_name}")

        by_kind: Dict[str, List] = {}
        for issue in sr.issues:
            by_kind.setdefault(issue.kind, []).append(issue)

        for kind in sorted(by_kind):
            items = by_kind[kind]
            print(f"    {kind} ({len(items)}):")
            for item in items[:_SKIP_SAMPLE_LIMIT]:
                detail = f" ({item.detail})" if item.detail else ""
                print(f"      - {item.rel_path}{detail}")
            if len(items) > _SKIP_SAMPLE_LIMIT:
                print(f"      ... and {len(items) - _SKIP_SAMPLE_LIMIT} more")