"""Recursive deterministic file discovery.

Does not read file contents; only stats and tests readability by opening.
Symlinks are not followed unless explicitly requested.
Directory counts, eligibility, and issues are all reported for the caller
to decide whether to proceed or abort.
"""

import os
import stat as stat_mod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from .safety import EXCLUDED_DIR_NAMES
from .safety import EXCLUDED_DIR_NAMES, TOOL_TEMP_MARKER

DEFAULT_EXTENSIONS = frozenset(
    {".txt", ".lst", ".wordlist", ".words", ".dict", ".dic"})


@dataclass(frozen=True)
class EligibleFile:
    root_name: str
    rel_path: str          # forward-slash separated, relative to root
    abs_path: Path
    size: int
    mtime_ns: int


@dataclass(frozen=True)
class ScanIssue:
    root_name: str
    rel_path: str
    abs_path: str
    kind: str
    detail: str = ""


@dataclass
class ScanResult:
    root_name: str
    root_path: Path
    directories_discovered: int = 0
    eligible: List[EligibleFile] = field(default_factory=list)
    issues: List[ScanIssue] = field(default_factory=list)

    @property
    def skipped(self) -> List[ScanIssue]:
        return [i for i in self.issues if i.kind.startswith("skipped") or i.kind == "excluded_dir"]

    @property
    def unreadable(self) -> List[ScanIssue]:
        return [i for i in self.issues
                if i.kind in ("unreadable_file", "unreadable_dir", "stat_error", "scandir_error")]

    @property
    def symlinks(self) -> List[ScanIssue]:
        return [i for i in self.issues if i.kind.startswith("symlink")]


def _extension_of(name: str) -> str:
    """Lowercase extension including the dot, or '' if none.

    A leading dot alone ('.bashrc') is treated as a hidden file with no
    extension. 'archive.tar.gz' has extension '.gz'.
    """
    if not name:
        return ""
    i = name.rfind(".")
    if i <= 0:
        return ""
    return name[i:].lower()


def scan(
    root_name: str,
    root_path,
    extensions: Optional[Iterable[str]] = None,
    follow_symlinks: bool = False,
    exclude_dir_names: Optional[Iterable[str]] = None,
) -> ScanResult:
    root_path = Path(root_path)
    ext_set = {e.lower() for e in (extensions or DEFAULT_EXTENSIONS)}
    if not all(e.startswith(".") for e in ext_set):
        raise ValueError("Extensions must include the leading dot, e.g. '.txt'")
    exclude = set(exclude_dir_names or EXCLUDED_DIR_NAMES)

    result = ScanResult(root_name=root_name, root_path=root_path)

    # Iterative DFS with a stack so deeply nested trees do not hit the
    # Python recursion limit. Subdirectories are pushed reversed so that
    # they pop in lexicographic order.
    stack: List[Tuple[Path, str]] = [(root_path, "")]

    while stack:
        dir_path, rel_prefix = stack.pop()
        result.directories_discovered += 1

        try:
            with os.scandir(dir_path) as it:
                entries = list(it)
        except PermissionError as e:
            result.issues.append(ScanIssue(
                root_name, rel_prefix or ".", str(dir_path),
                "unreadable_dir", str(e)))
            continue
        except OSError as e:
            result.issues.append(ScanIssue(
                root_name, rel_prefix or ".", str(dir_path),
                "scandir_error", str(e)))
            continue

        entries.sort(key=lambda e: os.fsencode(e.name))

        subdirs: List[Tuple[Path, str]] = []

        for entry in entries:
            rel = entry.name if not rel_prefix else f"{rel_prefix}/{entry.name}"

            try:
                is_link = entry.is_symlink()
            except OSError as e:
                result.issues.append(ScanIssue(
                    root_name, rel, entry.path, "stat_error", str(e)))
                continue

            if is_link and not follow_symlinks:
                try:
                    target_is_dir = entry.is_dir(follow_symlinks=True)
                    kind = "symlink_dir" if target_is_dir else "symlink_file"
                except OSError:
                    kind = "symlink"
                try:
                    target = os.readlink(entry.path)
                except OSError:
                    target = "?"
                result.issues.append(ScanIssue(
                    root_name, rel, entry.path, kind, f"-> {target}"))
                continue

            try:
                if entry.is_dir(follow_symlinks=follow_symlinks):
                    if entry.name in exclude:
                        result.issues.append(ScanIssue(
                            root_name, rel, entry.path, "excluded_dir"))
                        continue
                    subdirs.append((Path(entry.path), rel))
                    continue

                if entry.is_file(follow_symlinks=follow_symlinks):
                    _consider_file(root_name, rel, entry, ext_set, result)
                    continue

                result.issues.append(ScanIssue(
                    root_name, rel, entry.path, "non_regular",
                    "not a regular file or directory"))
            except OSError as e:
                result.issues.append(ScanIssue(
                    root_name, rel, entry.path, "stat_error", str(e)))

        for sd in reversed(subdirs):
            stack.append(sd)

    # Final deterministic ordering by raw filesystem bytes.
    result.eligible.sort(key=lambda f: os.fsencode(f.rel_path))
    result.issues.sort(key=lambda i: (os.fsencode(i.rel_path), i.kind))
    return result


def _consider_file(root_name: str, rel: str, entry: os.DirEntry,
                   ext_set, result: ScanResult) -> None:
    if TOOL_TEMP_MARKER in entry.name:
        result.issues.append(ScanIssue(
            root_name, rel, entry.path, "tool_temp_file",
            "leftover from a previous run"))
        return
    ext = _extension_of(entry.name)
    if ext not in ext_set:
        result.issues.append(ScanIssue(
            root_name, rel, entry.path, "skipped_extension",
            ext or "no extension"))
        return

    try:
        st = entry.stat(follow_symlinks=False)
    except OSError as e:
        result.issues.append(ScanIssue(
            root_name, rel, entry.path, "stat_error", str(e)))
        return

    if not stat_mod.S_ISREG(st.st_mode):
        result.issues.append(ScanIssue(
            root_name, rel, entry.path, "non_regular",
            "not a regular file"))
        return

    # Readability test by actually opening; more reliable than os.access on NFS.
    try:
        with open(entry.path, "rb"):
            pass
    except OSError as e:
        result.issues.append(ScanIssue(
            root_name, rel, entry.path, "unreadable_file", str(e)))
        return

    result.eligible.append(EligibleFile(
        root_name=root_name,
        rel_path=rel,
        abs_path=Path(entry.path),
        size=st.st_size,
        mtime_ns=st.st_mtime_ns,
    ))