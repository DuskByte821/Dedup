"""Locate, load, and validate incomplete backups for --resume.

An incomplete backup is a directory containing ``operation.json`` (backup
completed and verified) but NOT ``operation.complete.json`` (the full
operation did not finish).

This module only reads. It never modifies the tree or the backup.
"""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Tuple


_CHUNK = 1 << 20
CONFIG_KEYS = ("empty_mode", "extensions", "follow_symlinks", "max_line_bytes")


class ResumeError(Exception):
    """Raised when --resume cannot proceed safely."""


@dataclass
class IncompleteBackup:
    path: Path
    operation: dict

    @property
    def stamp(self) -> str:
        return self.path.name

    @property
    def config(self) -> dict:
        return self.operation.get("config") or {}

    @property
    def roots(self) -> List[Tuple[str, Path]]:
        out: List[Tuple[str, Path]] = []
        for r in self.operation.get("roots") or []:
            name = r.get("name")
            path = r.get("path")
            if name and path:
                out.append((str(name), Path(str(path))))
        return out

    @property
    def backed_up_files(self) -> List[dict]:
        return self.operation.get("files") or []

    @property
    def backed_up_rel_paths(self):
        """Set of (root_name, rel_path) that are present in the backup."""
        out = set()
        for rec in self.backed_up_files:
            try:
                out.add((rec["root"], rec["rel_path"]))
            except (KeyError, TypeError):
                continue
        return out


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def _load_operation(op_path: Path) -> dict:
    try:
        return json.loads(op_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise ResumeError(f"Cannot read {op_path}: {e}")


def _is_incomplete(dir_path: Path) -> bool:
    op = dir_path / "operation.json"
    done = dir_path / "operation.complete.json"
    return op.is_file() and not done.is_file()


def find_incomplete_backups(home: Path) -> List[IncompleteBackup]:
    if not home.is_dir():
        return []
    out: List[IncompleteBackup] = []
    for child in home.iterdir():
        if not child.is_dir() or not _is_incomplete(child):
            continue
        try:
            op = _load_operation(child / "operation.json")
        except ResumeError:
            continue
        if op.get("phase") != "backup_complete":
            continue
        if not op.get("backup_verified"):
            continue
        out.append(IncompleteBackup(path=child, operation=op))
    out.sort(key=lambda b: b.stamp, reverse=True)
    return out


def select_backup(
    home: Path,
    explicit_path: Optional[str],
    roots: List[Tuple[str, Path]],
) -> IncompleteBackup:
    if explicit_path:
        p = Path(explicit_path).expanduser().resolve()
        if not p.is_dir():
            raise ResumeError(f"--resume-from is not a directory: {p}")
        if (p / "operation.complete.json").is_file():
            raise ResumeError(
                f"Backup at {p} is already complete; nothing to resume.")
        if not (p / "operation.json").is_file():
            raise ResumeError(f"No operation.json at {p}")
        op = _load_operation(p / "operation.json")
        if op.get("phase") != "backup_complete":
            raise ResumeError(
                f"Backup at {p} is not resumable (phase={op.get('phase')!r})")
        if not op.get("backup_verified"):
            raise ResumeError(f"Backup at {p} is not marked as verified.")
        return IncompleteBackup(path=p, operation=op)

    candidates = find_incomplete_backups(home)
    if not candidates:
        raise ResumeError(f"No incomplete backup found under {home}.")
    if len(candidates) > 1:
        lines = "\n".join(f"  {b.path}" for b in candidates)
        raise ResumeError(
            f"Multiple incomplete backups under {home}:\n{lines}\n"
            f"Select one with --resume-from PATH.")
    return candidates[0]


# ---------------------------------------------------------------------------
# Compatibility
# ---------------------------------------------------------------------------

def check_roots_match(
    recorded: List[Tuple[str, Path]],
    requested: List[Tuple[str, Path]],
) -> None:
    if len(recorded) != len(requested):
        raise ResumeError(
            f"Root count mismatch: backup has {len(recorded)}, "
            f"request has {len(requested)}")
    rec = {name: path.resolve() for name, path in recorded}
    req = {name: path.resolve() for name, path in requested}
    if rec == req:
        return
    only_rec = sorted(set(rec) - set(req))
    only_req = sorted(set(req) - set(rec))
    path_diff = [
        f"{name}: {rec[name]} vs {req[name]}"
        for name in sorted(set(rec) & set(req)) if rec[name] != req[name]
    ]
    parts = []
    if only_rec:
        parts.append(f"only in backup: {only_rec}")
    if only_req:
        parts.append(f"only in request: {only_req}")
    if path_diff:
        parts.append(f"path differs: {path_diff}")
    raise ResumeError("Roots do not match backup: " + "; ".join(parts))


def check_config_match(recorded: dict, current: dict) -> None:
    mismatches = []
    for k in CONFIG_KEYS:
        rv = recorded.get(k)
        cv = current.get(k)
        if k == "extensions":
            rv = sorted(rv or [])
            cv = sorted(cv or [])
        if k == "follow_symlinks":
            rv = bool(rv)
            cv = bool(cv)
        if k == "max_line_bytes":
            rv = int(rv or 0)
            cv = int(cv or 0)
        if rv != cv:
            mismatches.append(f"  {k}: backup={rv!r}, current={cv!r}")
    if mismatches:
        raise ResumeError(
            "Configuration does not match the backup:\n"
            + "\n".join(mismatches))


# ---------------------------------------------------------------------------
# Backup integrity
# ---------------------------------------------------------------------------

def validate_backup_files(
    backup: IncompleteBackup,
    progress: Optional[Callable[[int, int, str], None]] = None,
) -> None:
    files = backup.backed_up_files
    total = len(files)
    for i, rec in enumerate(files, start=1):
        try:
            root_name = rec["root"]
            rel_path = rec["rel_path"]
            want_hex = rec["sha256"]
        except (KeyError, TypeError) as e:
            raise ResumeError(f"Malformed file record: {rec!r}: {e}")

        dest = backup.path / root_name / rel_path
        if not dest.is_file():
            raise ResumeError(f"Backup file missing: {dest}")

        try:
            actual = _hash_file(dest).hex()
        except OSError as e:
            raise ResumeError(f"Cannot re-hash {dest}: {e}")

        if actual != want_hex:
            raise ResumeError(
                f"Backup file corrupted: {root_name}/{rel_path}\n"
                f"  manifest: {want_hex}\n"
                f"  actual:   {actual}")
        if progress is not None:
            progress(i, total, f"{root_name}/{rel_path}")


def _hash_file(path: Path) -> bytes:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(_CHUNK)
            if not chunk:
                break
            h.update(chunk)
    return h.digest()