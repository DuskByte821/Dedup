"""Scope validation and disk-space checks."""

import shutil
from pathlib import Path


DANGEROUS_PATHS = {
    '/', '/etc', '/usr', '/var', '/bin', '/sbin', '/boot', '/sys', '/proc',
    '/dev', '/lib', '/lib64', '/opt', '/root', '/home',
}
EXCLUDED_DIR_NAMES = {'.dedup-backups', '.dedup-work', '.dedup-tmp'}


class SafetyError(Exception):
    pass


def resolve_scope(path, allow_dangerous=False):
    p = Path(path).expanduser()
    try:
        p = p.resolve(strict=True)
    except FileNotFoundError:
        raise SafetyError(f"Path does not exist: {path}")
    if not p.is_dir():
        raise SafetyError(f"Not a directory: {p}")

    for part in p.parts:
        if part in EXCLUDED_DIR_NAMES:
            raise SafetyError(
                f"Refusing to operate inside a tool work/backup directory: {p}")

    if not allow_dangerous:
        dangerous = {Path(d).resolve() for d in DANGEROUS_PATHS}
        home = Path.home().resolve()
        dangerous.add(home)
        for d in dangerous:
            if p == d or p in d.parents:
                raise SafetyError(
                    f"Refusing to operate on dangerous scope: {p}\n"
                    f"  (would include {d}). Use --i-know-what-im-doing to override.")
    return p


def check_disk_space(target, required_bytes):
    try:
        usage = shutil.disk_usage(target)
    except OSError as e:
        raise SafetyError(f"Cannot determine disk space for {target}: {e}")
    if usage.free < required_bytes:
        raise SafetyError(
            f"Insufficient disk space at {target}\n"
            f"  Required:  {format_bytes(required_bytes)}\n"
            f"  Available: {format_bytes(usage.free)}")


def format_bytes(n):
    sign = '-' if n < 0 else ''
    n = abs(n)
    for unit in ('B', 'KB', 'MB', 'GB', 'TB', 'PB'):
        if n < 1024:
            return f"{sign}{n:.2f} {unit}"
        n /= 1024
    return f"{sign}{n:.2f} EB"
