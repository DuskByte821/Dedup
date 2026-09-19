#!/usr/bin/env python3
"""Recursive Wordlist Deduplication Tool.

Priorities: data safety > accuracy > determinism > verifiability > performance > usability.

Nothing is modified until an explicit `y` confirmation. The original files
are backed up and the backup verified before any file is replaced.
"""

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

from dedup import safety, scanner, analyzer, reporter, backup, processor, verifier
from dedup.index import Index


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog='dedup.py',
        description='Safely detect and remove duplicate lines from a wordlist collection.',
    )
    parser.add_argument('path', help='Root directory to deduplicate')
    parser.add_argument('--dry-run', action='store_true',
                        help='Scan, analyze, report. Never modify anything.')
    parser.add_argument('--extensions', default=None,
                        help='Comma-separated file extensions to include. '
                             f'Default: {",".join(sorted(scanner.DEFAULT_EXTENSIONS))}')
    parser.add_argument('--empty-lines', choices=['keep', 'dedup', 'drop'], default='keep',
                        help='How to treat empty lines. Default: keep (preserve, do not dedup).')
    parser.add_argument('--follow-symlinks', action='store_true',
                        help='Follow symlinks (default: no).')
    parser.add_argument('--i-know-what-im-doing', action='store_true',
                        help='Allow dangerous scopes like / or ~.')
    parser.add_argument('--yes', '-y', action='store_true',
                        help='Skip interactive confirmation (DANGEROUS).')
    parser.add_argument('--work-dir', default=None,
                        help='Directory for the SQLite index (default: system temp).')
    parser.add_argument('--verbose', '-v', action='store_true')
    return parser.parse_args(argv)


def _run_scan(scope, args):
    ext = None
    if args.extensions:
        ext = {e.strip() if e.strip().startswith('.') else '.' + e.strip()
               for e in args.extensions.split(',') if e.strip()}
    return scanner.scan(scope, extensions=ext, follow_symlinks=args.follow_symlinks)


def main(argv=None):
    args = parse_args(argv)

    try:
        scope = safety.resolve_scope(args.path, allow_dangerous=args.i_know_what_im_doing)
    except safety.SafetyError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2

    print(f"Scanning {scope} ...")
    scan_result = _run_scan(scope, args)
    print(f"  {len(scan_result.eligible)} eligible, "
          f"{len(scan_result.skipped)} skipped, "
          f"{len(scan_result.unreadable)} unreadable")

    if args.work_dir:
        work_dir = Path(args.work_dir)
        work_dir.mkdir(parents=True, exist_ok=True)
    else:
        work_dir = Path(tempfile.mkdtemp(prefix='wordlist-dedup-'))
    index_path = work_dir / 'index.db'

    print(f"Analyzing (index: {index_path}) ...")
    index = Index(index_path)
    try:
        analysis = analyzer.analyze(
            scope, scan_result.eligible, index,
            empty_mode=args.empty_lines,
            verbose_callback=(lambda r, n, d: print(f"  {r}: {n} entries, {d} dupes"))
                             if args.verbose else None,
        )
    except Exception as e:
        index.close()
        print(f"Error during analysis: {e}", file=sys.stderr)
        return 1

    reporter.print_report(scan_result, analysis, scope, dry_run=args.dry_run)

    if args.dry_run:
        print("[DRY RUN] No files modified.")
        index.close()
        return 0

    if not analysis.files_affected:
        print("No duplicates found. Nothing to do.")
        index.close()
        return 0

    # ---- Confirmation ---------------------------------------------------
    if args.yes:
        proceed = True
    else:
        try:
            ans = input("Proceed with deduplication? [y/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            ans = ''
        proceed = ans in ('y', 'yes')

    if not proceed:
        print("Cancelled. No files have been modified.")
        index.close()
        return 0

    # ---- Disk-space check BEFORE any modification -----------------------
    files_to_backup = [(r, p) for r, p in scan_result.eligible
                       if r in analysis.files_affected]
    backup_bytes = sum(p.stat().st_size for _, p in files_to_backup)
    max_file_bytes = max((p.stat().st_size for _, p in files_to_backup), default=0)
    try:
        safety.check_disk_space(scope, backup_bytes + max_file_bytes)
    except safety.SafetyError as e:
        print(f"Error: {e}", file=sys.stderr)
        print("No original files were modified.")
        index.close()
        return 1

    # ---- Backup ---------------------------------------------------------
    print(f"Creating backup in {scope.parent / '.dedup-backups'} ...")
    try:
        backup_root = backup.create_backup(scope, files_to_backup)
        print(f"  Backup at: {backup_root}")
        print("Verifying backup ...")
        backup.verify_backup(backup_root, files_to_backup)
    except backup.BackupError as e:
        print("Backup failed.\nDeduplication cancelled.\n"
              "No original files were modified.", file=sys.stderr)
        print(f"  {e}", file=sys.stderr)
        index.close()
        return 1
    print("  Backup verified.")

    # ---- Atomic replacement --------------------------------------------
    print("Applying deduplication ...")
    t0 = time.time()
    try:
        modified = processor.process_all(
            scope, scan_result.eligible, index, analysis,
            empty_mode=args.empty_lines,
            progress=(lambda r: print(f"  rewriting {r}")) if args.verbose else None,
        )
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        print(f"Backup is at: {backup_root}", file=sys.stderr)
        index.close()
        return 1
    except Exception as e:
        print(f"Error during processing: {e}", file=sys.stderr)
        print(f"Backup is at: {backup_root}", file=sys.stderr)
        print("Restore from backup if needed.", file=sys.stderr)
        index.close()
        return 1
    print(f"  Modified {len(modified)} file(s) in {time.time() - t0:.1f}s")

    # ---- Independent verification --------------------------------------
    print("Verifying result ...")
    try:
        verifier.verify(
            scope, backup_root, scan_result.eligible, analysis.files_affected,
            empty_mode=args.empty_lines,
            expected_unique=analysis.unique_entries,
            extensions=None if not args.extensions else
                {e.strip() if e.strip().startswith('.') else '.' + e.strip()
                 for e in args.extensions.split(',') if e.strip()},
        )
    except Exception as e:
        print("VERIFICATION FAILED", file=sys.stderr)
        print(f"  {e}", file=sys.stderr)
        print(f"Backup is at: {backup_root}", file=sys.stderr)
        index.close()
        return 1
    print("  Verification passed.")

    # ---- Manifest -------------------------------------------------------
    manifest = {
        'timestamp': time.strftime('%Y-%m-%dT%H:%M:%S'),
        'scope': str(scope),
        'files_scanned': len(scan_result.eligible),
        'files_modified': len(modified),
        'entries_before': analysis.total_entries,
        'entries_after': analysis.unique_entries,
        'duplicates_removed': analysis.total_entries - analysis.unique_entries,
        'internal_duplicates': analysis.internal_duplicates,
        'cross_file_duplicates': analysis.cross_file_duplicates,
        'backup_location': str(backup_root),
        'verification_status': 'passed',
    }
    manifest_path = backup_root / 'operation.json'
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print(f"Manifest: {manifest_path}")

    index.close()
    print()
    print("SUCCESS")
    return 0


if __name__ == '__main__':
    sys.exit(main())
