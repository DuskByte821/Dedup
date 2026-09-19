"""Recursive deterministic file discovery."""

import os
from dataclasses import dataclass, field
from pathlib import Path


DEFAULT_EXTENSIONS = {'.txt', '.lst', '.wordlist', '.words', '.dict', '.dic', '.log', '.csv'}
EXCLUDED_DIR_NAMES = {'.dedup-backups', '.dedup-work', '.dedup-tmp',
                      '.git', '.svn', '.hg'}


@dataclass
class ScanResult:
    scope: Path
    eligible: list = field(default_factory=list)     # [(rel_path_str, abs_path)]
    skipped: list = field(default_factory=list)      # [(rel_path_str, reason)]
    unreadable: list = field(default_factory=list)   # [(rel_path_str, reason)]


def scan(scope, extensions=None, follow_symlinks=False, exclude_dirs=None):
    extensions = {e.lower() for e in (extensions or DEFAULT_EXTENSIONS)}
    exclude_dirs = set(exclude_dirs or EXCLUDED_DIR_NAMES)
    result = ScanResult(scope=scope)

    for dirpath, dirnames, filenames in os.walk(scope, followlinks=follow_symlinks):
        dirnames.sort()
        filenames.sort()

        keep = []
        for d in dirnames:
            if d in exclude_dirs:
                continue
            full = Path(dirpath) / d
            if full.is_symlink() and not follow_symlinks:
                result.skipped.append((str(full.relative_to(scope)), 'symlinked directory'))
                continue
            keep.append(d)
        dirnames[:] = keep

        for fn in filenames:
            full = Path(dirpath) / fn
            rel_str = str(full.relative_to(scope))

            if full.is_symlink() and not follow_symlinks:
                result.skipped.append((rel_str, 'symlink'))
                continue
            if not full.is_file():
                result.skipped.append((rel_str, 'not a regular file'))
                continue
            if full.suffix.lower() not in extensions:
                result.skipped.append((rel_str, 'extension not eligible'))
                continue
            if not os.access(full, os.R_OK):
                result.unreadable.append((rel_str, 'not readable'))
                continue

            result.eligible.append((rel_str, full))

    result.eligible.sort(key=lambda x: x[0])
    result.skipped.sort(key=lambda x: x[0])
    result.unreadable.sort(key=lambda x: x[0])
    return result
