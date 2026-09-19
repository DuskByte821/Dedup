"""Verified backup creation."""

import datetime
import shutil
from pathlib import Path


class BackupError(Exception):
    pass


def create_backup(scope, files_to_backup):
    """files_to_backup: iterable of (rel_path, abs_path).
    Returns the backup root directory."""
    parent = scope.parent
    base = parent / '.dedup-backups'
    base.mkdir(exist_ok=True)

    timestamp = datetime.datetime.now().strftime('%Y-%m-%d_%H%M%S')
    backup_root = base / timestamp
    suffix = 0
    while backup_root.exists():
        suffix += 1
        backup_root = base / f"{timestamp}_{suffix}"
    backup_root.mkdir(parents=True, exist_ok=False)

    for rel_path, abs_path in files_to_backup:
        dest = backup_root / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(abs_path, dest)
        except Exception as e:
            raise BackupError(f"Failed to copy {rel_path} → {dest}: {e}")
    return backup_root


def verify_backup(backup_root, files_to_backup):
    """Byte-for-byte verification of each backed-up file."""
    for rel_path, abs_path in files_to_backup:
        dest = backup_root / rel_path
        if not dest.is_file():
            raise BackupError(f"Backup file missing: {rel_path}")
        if not _files_equal(abs_path, dest):
            raise BackupError(f"Backup content mismatch: {rel_path}")


def _files_equal(a, b):
    sa = a.stat()
    sb = b.stat()
    if sa.st_size != sb.st_size:
        return False
    with open(a, 'rb') as fa, open(b, 'rb') as fb:
        while True:
            ca = fa.read(1 << 20)
            cb = fb.read(1 << 20)
            if ca != cb:
                return False
            if not ca:
                return True
