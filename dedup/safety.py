"""Scope validation, root-name validation, overlap detection, disk checks."""

import os
import re
import shutil
from pathlib import Path
from typing import Iterable, List, Optional, Tuple


EXCLUDED_DIR_NAMES = frozenset({".dedup-backups", ".dedup-work", ".dedup-tmp"})
# Marker embedded in every temporary file the tool creates. Used by the
# scanner to skip leftovers from a crashed run, and by the processor to
# name its temp files.
TOOL_TEMP_MARKER = ".dedup-tmp-"

RESERVED_ROOT_NAMES = frozenset(
    {"total", "operation", "manifest", "dedup", "index", "backup"})

_ROOT_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")
MAX_ROOT_NAME_LEN = 64

DANGEROUS_PATHS = (
    "/", "/etc", "/usr", "/var", "/bin", "/sbin", "/boot", "/sys", "/proc",
    "/dev", "/lib", "/lib64", "/opt", "/root", "/home", "/mnt", "/media",
    "/srv", "/run",
)

_BACKUP_HOME_NAME = ".dedup-backups"


class SafetyError(Exception):
    """Raised when a safety precondition fails. Callers must abort."""


# ---------------------------------------------------------------------------
# Root names
# ---------------------------------------------------------------------------

def validate_root_name(name: str) -> str:
    if not isinstance(name, str) or not name:
        raise SafetyError("Root name must be a non-empty string")
    if len(name) > MAX_ROOT_NAME_LEN:
        raise SafetyError(
            f"Root name too long (max {MAX_ROOT_NAME_LEN}): {name!r}")
    if not _ROOT_NAME_RE.match(name):
        raise SafetyError(
            f"Invalid root name {name!r}: must match [A-Za-z0-9_-]+")
    if name in RESERVED_ROOT_NAMES:
        raise SafetyError(
            f"Root name {name!r} is reserved (used by report/manifest keywords)")
    return name


def derive_root_name(path) -> str:
    resolved = Path(path).expanduser().resolve()
    base = resolved.name
    if not base:
        raise SafetyError(
            f"Cannot derive a root name from {path!r} (resolves to {resolved})")
    sanitized = re.sub(r"[^A-Za-z0-9_-]+", "_", base).strip("_")
    if not sanitized:
        sanitized = "root"
    if sanitized in RESERVED_ROOT_NAMES:
        sanitized = f"{sanitized}_root"
    if len(sanitized) > MAX_ROOT_NAME_LEN:
        sanitized = sanitized[:MAX_ROOT_NAME_LEN]
    return sanitized


# ---------------------------------------------------------------------------
# Root paths
# ---------------------------------------------------------------------------

def resolve_root_path(path, name: str, allow_dangerous: bool = False) -> Path:
    p = Path(path).expanduser()
    try:
        p = p.resolve(strict=True)
    except FileNotFoundError:
        raise SafetyError(f"Root {name!r}: path does not exist: {path}")
    except OSError as e:
        raise SafetyError(f"Root {name!r}: cannot resolve {path!r}: {e}")

    if not p.is_dir():
        raise SafetyError(f"Root {name!r}: not a directory: {p}")

    for part in p.parts:
        if part in EXCLUDED_DIR_NAMES:
            raise SafetyError(
                f"Root {name!r}: path lies inside a tool work/backup "
                f"directory ({part!r}): {p}")

    if not allow_dangerous:
        home = Path.home().resolve()
        if p == home:
            raise SafetyError(
                f"Root {name!r}: refusing home directory {p}. "
                f"Pass --i-know-what-im-doing to override.")
        for danger_str in DANGEROUS_PATHS:
            try:
                danger = Path(danger_str).resolve()
            except OSError:
                continue
            if p == danger or p in danger.parents:
                raise SafetyError(
                    f"Root {name!r}: refusing dangerous scope {p} "
                    f"(inside {danger}). "
                    f"Pass --i-know-what-im-doing to override.")

    return p


def check_root_overlap(roots: Iterable[Tuple[str, Path]]) -> None:
    items = list(roots)
    for i, (name_a, path_a) in enumerate(items):
        for name_b, path_b in items[i + 1:]:
            if path_a == path_b:
                raise SafetyError(
                    f"Roots {name_a!r} and {name_b!r} resolve to the same "
                    f"path: {path_a}")
            if path_a in path_b.parents:
                raise SafetyError(
                    f"Roots overlap: {name_a!r} ({path_a}) is an ancestor of "
                    f"{name_b!r} ({path_b})")
            if path_b in path_a.parents:
                raise SafetyError(
                    f"Roots overlap: {name_b!r} ({path_b}) is an ancestor of "
                    f"{name_a!r} ({path_a})")


# ---------------------------------------------------------------------------
# Backup home
# ---------------------------------------------------------------------------

def resolve_backup_home(
    override: Optional[str],
    roots: List[Tuple[str, Path]],
    *,
    create: bool = True,
) -> Path:
    """Return a resolved backup home directory.

    Default: ``<parent of first root>/.dedup-backups``.
    The result must be outside every root, and no root may lie inside it.
    """
    if not roots:
        raise SafetyError("No roots provided; cannot choose a backup home.")

    if override:
        home = Path(override).expanduser()
    else:
        home = roots[0][1].parent / _BACKUP_HOME_NAME

    if create:
        try:
            home.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise SafetyError(f"Cannot create backup home {home}: {e}")

    try:
        home = home.resolve(strict=True)
    except FileNotFoundError:
        raise SafetyError(f"Backup home does not exist: {home}")
    except OSError as e:
        raise SafetyError(f"Cannot resolve backup home {home}: {e}")

    if not home.is_dir():
        raise SafetyError(f"Backup home is not a directory: {home}")

    for name, rp in roots:
        if home == rp:
            raise SafetyError(
                f"Backup home equals root {name!r} ({rp})")
        if rp in home.parents:
            raise SafetyError(
                f"Backup home {home} lies inside root {name!r} ({rp}). "
                f"Use --backup-dir to choose a different location.")
        if home in rp.parents:
            raise SafetyError(
                f"Root {name!r} ({rp}) lies inside backup home {home}. "
                f"Use --backup-dir to choose a different location.")
    return home


# ---------------------------------------------------------------------------
# Disk space
# ---------------------------------------------------------------------------

def check_disk_space(path, required_bytes: int) -> None:
    try:
        usage = shutil.disk_usage(str(path))
    except OSError as e:
        raise SafetyError(f"Cannot determine disk space at {path}: {e}")
    if usage.free < required_bytes:
        raise SafetyError(
            f"Insufficient disk space at {path}\n"
            f"  Required:  {format_bytes(required_bytes)}\n"
            f"  Available: {format_bytes(usage.free)}")


def check_backup_disk_space(
    backup_home: Path,
    roots: List[Tuple[str, Path]],
    backup_bytes: int,
    max_file_bytes: int,
) -> None:
    """Check free space on every filesystem involved, grouping by st_dev.

    Requirements, per filesystem:
      * backup fs:   backup_bytes * 1.05  (metadata slack)
      * scope fs:    max_file_bytes * 1.10 (one temp file at a time)

    If backup fs and scope fs are the same device, the two requirements are
    summed, so the check cannot pass on a filesystem that is only big enough
    for one of them.
    """
    def _dev(p: Path) -> int:
        try:
            return os.stat(str(p)).st_dev
        except OSError as e:
            raise SafetyError(f"Cannot stat {p}: {e}")

    needed: dict = {}

    def add(dev: int, n: int) -> None:
        needed[dev] = needed.get(dev, 0) + n

    add(_dev(backup_home), int(backup_bytes * 1.05))
    for name, rp in roots:
        add(_dev(rp), int(max_file_bytes * 1.10))

    probes = {_dev(backup_home): backup_home}
    for name, rp in roots:
        probes.setdefault(_dev(rp), rp)

    for dev, want in needed.items():
        probe = probes[dev]
        try:
            usage = shutil.disk_usage(str(probe))
        except OSError as e:
            raise SafetyError(f"Cannot determine disk space at {probe}: {e}")
        if usage.free < want:
            raise SafetyError(
                f"Insufficient disk space on device {dev} at {probe}\n"
                f"  Required:  {format_bytes(want)}\n"
                f"  Available: {format_bytes(usage.free)}\n"
                f"  (backup needs {format_bytes(backup_bytes)}; "
                f"largest file {format_bytes(max_file_bytes)})")


def format_bytes(n: int) -> str:
    sign = "-" if n < 0 else ""
    n = abs(int(n))
    if n < 1024:
        return f"{sign}{n} B"
    for unit in ("KB", "MB", "GB", "TB", "PB"):
        n /= 1024
        if n < 1024:
            return f"{sign}{n:.2f} {unit}"
    return f"{sign}{n:.2f} EB"