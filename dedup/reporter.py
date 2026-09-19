"""Read-only, human-readable reporting."""

from .safety import format_bytes


def print_report(scan_result, analysis, scope, dry_run=False):
    total_files = len(scan_result.eligible) + len(scan_result.skipped) + len(scan_result.unreadable)
    print()
    print("=" * 56)
    print("Deduplication Report")
    print("=" * 56)
    print()
    print(f"Scope: {scope}")
    print()
    print(f"Files discovered:      {total_files}")
    print(f"Files eligible:        {len(scan_result.eligible)}")
    print(f"Files skipped:         {len(scan_result.skipped)}")
    print(f"Unreadable files:      {len(scan_result.unreadable)}")
    print()
    print(f"Total entries:         {analysis.total_entries:,}")
    print(f"Unique entries:        {analysis.unique_entries:,}")
    print(f"Duplicate entries:     {analysis.internal_duplicates + analysis.cross_file_duplicates:,}")
    print()
    print(f"Internal duplicates:   {analysis.internal_duplicates:,}")
    print(f"Cross-file duplicates: {analysis.cross_file_duplicates:,}")
    print()
    print(f"Files affected:        {len(analysis.files_affected)}")
    print(f"Estimated entries removed: {analysis.total_entries - analysis.unique_entries:,}")
    delta = analysis.bytes_after - analysis.bytes_before
    print(f"Estimated space change: {format_bytes(delta)}")
    if analysis.empty_lines_kept:
        print(f"Empty lines preserved (not deduplicated): {analysis.empty_lines_kept:,}")
    if analysis.empty_lines_dropped:
        print(f"Empty lines dropped:   {analysis.empty_lines_dropped:,}")
    print()

    if scan_result.unreadable:
        print("Unreadable files:")
        for rel, reason in scan_result.unreadable:
            print(f"  - {rel} ({reason})")
        print()
    if scan_result.skipped:
        print(f"Skipped files ({len(scan_result.skipped)}):")
        for rel, reason in scan_result.skipped[:15]:
            print(f"  - {rel} ({reason})")
        if len(scan_result.skipped) > 15:
            print(f"  ... and {len(scan_result.skipped) - 15} more")
        print()

    if dry_run:
        print("[DRY RUN] No files will be modified.")
    else:
        print("No files have been modified yet.")
    print()
