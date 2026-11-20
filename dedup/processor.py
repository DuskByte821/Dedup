"""Atomic per-file replacement.

For each affected file:

    read original -> write temp in same dir -> flush -> fsync
    -> copystat -> os.replace -> fsync parent dir

The original is never truncated in place. A crash leaves either the
original or the complete replacement, never a partial file. Any failure
removes the temp file and leaves the original intact.

Concurrent-modification guard: the file's (size, mtime_ns) must match
what the scanner recorded, and the file must still be registered in the
index with the same metadata. Any mismatch aborts the run.

Final-newline state: the output ends with a terminator iff the original
ended with one (\\n, \\r\\n, or \\r). Terminators are otherwise normalized
to LF.
"""

import hashlib
import os
import shutil
import secrets
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .analyzer import AnalysisResult
from .lineio import iter_lines
from .safety import TOOL_TEMP_MARKER
from .scanner import ScanResult


DEFAULT_MAX_LINE_BYTES = 16 * 1024 * 1024


class ProcessingError(Exception):
    """Raised when a file cannot be safely rewritten. Caller must abort."""


ProgressFn = Callable[[str, str, int, int], None]


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def process_roots(
    scan_results: List[ScanResult],
    analysis: AnalysisResult,
    index,
    empty_mode: str = "keep",
    max_line_bytes: int = DEFAULT_MAX_LINE_BYTES,
    progress: Optional[ProgressFn] = None,
) -> Dict[str, List[str]]:
    """Rewrite every affected file. Returns {root_name: [modified rel paths]}."""
    if empty_mode not in ("keep", "dedup", "drop"):
        raise ProcessingError(f"invalid empty_mode: {empty_mode!r}")

    modified: Dict[str, List[str]] = {}

    for sr in scan_results:
        ra = analysis.roots[sr.root_name]
        by_rel = {ef.rel_path: ef for ef in sr.eligible}
        root_id = index.root_id(sr.root_name)

        affected = sorted(ra.files_affected)
        total = len(affected)
        root_modified: List[str] = []

        for i, rel in enumerate(affected, start=1):
            ef = by_rel[rel]
            file_id = index.register_file(
                root_id, ef.rel_path, ef.size, ef.mtime_ns)
            _rewrite_one(
                ef, file_id, root_id, index,
                empty_mode, max_line_bytes,
            )
            root_modified.append(rel)
            if progress is not None:
                progress(sr.root_name, rel, i, total)

        modified[sr.root_name] = root_modified

    return modified


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _temp_name(orig_name: str) -> str:
    return f".{orig_name}{TOOL_TEMP_MARKER}{os.getpid()}-{secrets.token_hex(6)}"


def _has_final_newline(f) -> bool:
    """True iff the last byte of f is LF or CR.

    Reads exactly one byte at EOF. Position is restored on exit.
    """
    try:
        size = os.fstat(f.fileno()).st_size
    except OSError:
        return False
    if size == 0:
        return False
    pos = f.tell()
    try:
        f.seek(size - 1)
        last = f.read(1)
    finally:
        f.seek(pos)
    return last in (b"\n", b"\r")


def _fsync_dir(path: Path) -> None:
    """POSIX: make the rename durable. Best-effort; never raises."""
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

def _rewrite_one(ef, file_id, root_id, index, empty_mode, max_line_bytes):
    src: Path = ef.abs_path

    # ---- Concurrent-modification guard -----------------------------------
    try:
        st = src.stat()
    except OSError as e:
        raise ProcessingError(
            f"Cannot stat {src}: {type(e).__name__}: {e}")
    if st.st_size != ef.size or st.st_mtime_ns != ef.mtime_ns:
        raise ProcessingError(
            f"Source changed since scan: {src}\n"
            f"  expected size={ef.size} mtime_ns={ef.mtime_ns}\n"
            f"  actual   size={st.st_size} mtime_ns={st.st_mtime_ns}")

    tmp_path = src.parent / _temp_name(src.name)

    # ---- Write the temp file. Cleanup ONLY on failure. -------------------
    try:
        _write_temp_file(src, tmp_path, file_id, root_id, index,
                         empty_mode, max_line_bytes)
    except BaseException:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass
        raise

    # ---- Metadata (best-effort, never blocks the swap) -------------------
    try:
        shutil.copystat(src, tmp_path, follow_symlinks=False)
    except OSError:
        pass
    try:
        st2 = src.stat()
        os.chown(tmp_path, st2.st_uid, st2.st_gid)
    except (OSError, AttributeError):
        pass

    # ---- Atomic swap ----------------------------------------------------
    try:
        os.replace(str(tmp_path), str(src))
    except OSError as e:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass
        raise ProcessingError(
            f"Cannot replace {src}: {type(e).__name__}: {e}")

    _fsync_dir(src.parent)


def _write_temp_file(src, tmp_path, file_id, root_id, index,
                     empty_mode, max_line_bytes):
    """Write the deduplicated content of src into tmp_path.

    Every handle is opened in a context manager. On any failure, the
    caller is responsible for removing tmp_path; this function only
    writes and raises.
    """
    with open(src, "rb") as f_in:
        try:
            had_final_newline = _has_final_newline(f_in)
        except OSError as e:
            raise ProcessingError(
                f"Cannot inspect {src}: {type(e).__name__}: {e}")
        f_in.seek(0)

        try:
            f_out = open(tmp_path, "xb")
        except OSError as e:
            raise ProcessingError(
                f"Cannot create temp file {tmp_path}: "
                f"{type(e).__name__}: {e}")

        with f_out:
            try:
                any_output = False
                for line_no, content in enumerate(
                        iter_lines(f_in, max_line_bytes), start=1):

                    if len(content) == 0:
                        if empty_mode == "keep":
                            f_out.write(b"\n")
                            any_output = True
                            continue
                        if empty_mode == "drop":
                            continue
                        # empty_mode == "dedup": fall through as entry

                    h = hashlib.sha256(content).digest()
                    owner = index.lookup(root_id, h, content)
                    if owner is None:
                        raise ProcessingError(
                            f"index has no owner for line {line_no} of "
                            f"{src}; analysis and processing are out of "
                            f"sync")
                    if owner == (file_id, line_no):
                        f_out.write(content)
                        f_out.write(b"\n")
                        any_output = True

                # Preserve per-file final-newline state. The last byte
                # written is always b"\n"; the write-only handle cannot
                # read it back, so use tell() + truncate().
                if any_output and not had_final_newline:
                    size = f_out.tell()
                    if size > 0:
                        f_out.truncate(size - 1)
                        f_out.seek(size - 1)

                f_out.flush()
                os.fsync(f_out.fileno())
            except ProcessingError:
                raise
            except OSError as e:
                raise ProcessingError(
                    f"Error writing temp for {src}: "
                    f"{type(e).__name__}: {e}")