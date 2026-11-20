"""Verified backup creation and validation.

The backup lives in a timestamped directory OUTSIDE every root. It mirrors
the relative structure of each root that had affected files, one top-level
subdirectory per root name.

Layout::

    <backup_home>/<timestamp>/
        username/
            <rel_path_a>
            <rel_path_b>
        password/
            <rel_path_c>
        operation.json        # written only after full verification
        operation.complete.json   # written by manifest.py after the run succeeds

Only files that will actually be modified are backed up (spec §11:
"Copy every file that will be modified."). Untouched files are not copied.

Completion marker
-----------------
``operation.json`` is written only after every backed-up file has been
copied and its SHA-256 verified against the source. Its absence marks the
backup as incomplete and it MUST NOT be used for recovery.

``operation.json`` records the roots and the configuration so that a
later ``--resume`` can verify compatibility.
"""

import datetime
import hashlib
import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .analyzer import AnalysisResult
from .scanner import ScanResult


class BackupError(Exception):
    """Raised when a backup step fails. Callers must abort."""


_COPY_CHUNK = 1 << 20
_BACKUP_DIR_MODE = 0o700


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class BackedUpFile:
    root_name: str
    rel_path: str
    source: Path
    dest: Path
    size: int
    mtime_ns: int
    sha256: bytes = b""


@dataclass
class BackupPlan:
    home: Path
    stamp: str
    root: Path
    roots: List[Tuple[str, Path]] = field(default_factory=list)
    config: Dict[str, Any] = field(default_factory=dict)
    files: List[BackedUpFile] = field(default_factory=list)

    @property
    def total_bytes(self) -> int:
        return sum(f.size for f in self.files)

    @property
    def max_file_bytes(self) -> int:
        return max((f.size for f in self.files), default=0)


ProgressFn = Callable[["BackupPlan", int, int, BackedUpFile], None]


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------

def _now_stamp() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")


def _unique_dir(home: Path, base_stamp: str) -> Path:
    candidate = home / base_stamp
    if not candidate.exists():
        return candidate
    n = 1
    while True:
        candidate = home / f"{base_stamp}_{n}"
        if not candidate.exists():
            return candidate
        n += 1


def plan_backup(
    backup_home: Path,
    scan_results: List[ScanResult],
    analysis: AnalysisResult,
    config: Optional[Dict[str, Any]] = None,
) -> BackupPlan:
    """Build a BackupPlan. Touches no filesystem state."""
    stamp = _now_stamp()
    root = _unique_dir(backup_home, stamp)

    plan = BackupPlan(
        home=backup_home,
        stamp=stamp,
        root=root,
        roots=[(sr.root_name, sr.root_path) for sr in scan_results],
        config=dict(config or {}),
    )

    for sr in scan_results:
        ra = analysis.roots[sr.root_name]
        if not ra.files_affected:
            continue
        by_rel = {ef.rel_path: ef for ef in sr.eligible}
        for rel in sorted(ra.files_affected):
            ef = by_rel[rel]
            plan.files.append(BackedUpFile(
                root_name=sr.root_name,
                rel_path=rel,
                source=ef.abs_path,
                dest=root / sr.root_name / rel,
                size=ef.size,
                mtime_ns=ef.mtime_ns,
            ))
    return plan


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------

def execute_backup(plan: BackupPlan,
                   progress: Optional[ProgressFn] = None) -> BackupPlan:
    if not plan.files:
        raise BackupError("Backup plan is empty; nothing to back up.")

    try:
        plan.root.mkdir(mode=_BACKUP_DIR_MODE, parents=True, exist_ok=False)
    except FileExistsError:
        raise BackupError(f"Backup directory already exists: {plan.root}")
    except OSError as e:
        raise BackupError(f"Cannot create backup directory {plan.root}: {e}")

    total = len(plan.files)
    for i, bf in enumerate(plan.files, start=1):
        _check_source_unchanged(bf)

        try:
            bf.dest.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise BackupError(f"Cannot create {bf.dest.parent}: {e}")

        try:
            bf.sha256 = _copy_and_hash(bf.source, bf.dest)
        except OSError as e:
            raise BackupError(
                f"Failed to back up {bf.source} -> {bf.dest}: {e}")

        if progress is not None:
            progress(plan, i, total, bf)

    return plan


def verify_backup(plan: BackupPlan,
                  progress: Optional[ProgressFn] = None) -> None:
    total = len(plan.files)
    for i, bf in enumerate(plan.files, start=1):
        if not bf.dest.is_file():
            raise BackupError(f"Backup file missing: {bf.dest}")

        if _hash_file(bf.dest) != bf.sha256:
            raise BackupError(
                f"Backup hash mismatch for {bf.rel_path!r} "
                f"(root {bf.root_name!r})\n"
                f"  source: {bf.source}\n"
                f"  backup: {bf.dest}\n"
                f"  The backup is corrupt. Aborting.")

        if _hash_file(bf.source) != bf.sha256:
            raise BackupError(
                f"Source changed between backup and verification: "
                f"{bf.source}\n"
                f"  Aborting to avoid acting on a file that is no longer "
                f"in the state we analyzed.")

        if progress is not None:
            progress(plan, i, total, bf)


def write_operation_json(plan: BackupPlan) -> Path:
    """Write operation.json, marking the backup as complete and verified."""
    per_root_stats: Dict[str, Dict[str, int]] = {}
    for bf in plan.files:
        r = per_root_stats.setdefault(bf.root_name, {"files": 0, "bytes": 0})
        r["files"] += 1
        r["bytes"] += bf.size

    roots_list = []
    for name, path in plan.roots:
        stats = per_root_stats.get(name, {"files": 0, "bytes": 0})
        roots_list.append({
            "name": name,
            "path": str(path),
            "files": stats["files"],
            "bytes": stats["bytes"],
        })

    payload = {
        "phase": "backup_complete",
        "backup_stamp": plan.stamp,
        "backup_root": str(plan.root),
        "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "config": {
            "empty_mode": plan.config.get("empty_mode"),
            "extensions": sorted(plan.config.get("extensions") or []),
            "follow_symlinks": bool(plan.config.get("follow_symlinks")),
            "max_line_bytes": int(plan.config.get("max_line_bytes") or 0),
        },
        "roots": roots_list,
        "files": [
            {
                "root": bf.root_name,
                "rel_path": bf.rel_path,
                "size": bf.size,
                "mtime_ns": bf.mtime_ns,
                "sha256": bf.sha256.hex(),
            }
            for bf in plan.files
        ],
        "total_files": len(plan.files),
        "total_bytes": plan.total_bytes,
        "backup_verified": True,
    }

    path = plan.root / "operation.json"
    try:
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        fd = os.open(str(path), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        _fsync_dir(plan.root)
    except OSError as e:
        raise BackupError(f"Cannot write operation.json at {path}: {e}")
    return path


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _check_source_unchanged(bf: BackedUpFile) -> None:
    try:
        st = bf.source.stat()
    except OSError as e:
        raise BackupError(f"Cannot stat source {bf.source}: {e}")
    if st.st_size != bf.size or st.st_mtime_ns != bf.mtime_ns:
        raise BackupError(
            f"Source changed since analysis: {bf.source}\n"
            f"  expected size={bf.size} mtime_ns={bf.mtime_ns}\n"
            f"  actual   size={st.st_size} mtime_ns={st.st_mtime_ns}")


def _copy_and_hash(src: Path, dest: Path) -> bytes:
    h = hashlib.sha256()
    try:
        with open(src, "rb") as fi, open(dest, "wb") as fo:
            while True:
                chunk = fi.read(_COPY_CHUNK)
                if not chunk:
                    break
                h.update(chunk)
                fo.write(chunk)
            fo.flush()
            os.fsync(fo.fileno())
    except OSError:
        try:
            if dest.exists():
                dest.unlink()
        except OSError:
            pass
        raise

    try:
        shutil.copystat(src, dest)
    except OSError:
        pass

    return h.digest()


def _hash_file(path: Path) -> bytes:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(_COPY_CHUNK)
            if not chunk:
                break
            h.update(chunk)
    return h.digest()


def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)