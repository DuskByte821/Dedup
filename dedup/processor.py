"""Atomic file rewriting.

Original files are never truncated in place. A unique temporary file is
written, flushed, fsync'd, metadata is copied, and only then is the file
atomically replaced with os.replace().
"""

import hashlib
import os
import secrets
import stat as stat_mod

from .lineio import iter_lines


class ProcessingError(Exception):
    pass


def _rewrite_one(rel_path, abs_path, index, empty_mode):
    tmp_name = f".{abs_path.name}.dedup-tmp-{os.getpid()}-{secrets.token_hex(6)}"
    tmp_path = abs_path.parent / tmp_name
    try:
        st = abs_path.stat()
        with open(abs_path, 'rb') as f_in, open(tmp_path, 'wb') as f_out:
            line_no = 0
            for content, _term_len in iter_lines(f_in):
                line_no += 1

                if len(content) == 0:
                    if empty_mode == 'keep':
                        f_out.write(b'\n')
                        continue
                    if empty_mode == 'drop':
                        continue
                    # 'dedup': fall through

                h = hashlib.sha256(content).digest()
                keep = False
                for c, fpath, fline in index.lookup(h):
                    if c == content:
                        if fpath == rel_path and fline == line_no:
                            keep = True
                        break
                if keep:
                    f_out.write(content)
                    f_out.write(b'\n')

            f_out.flush()
            os.fsync(f_out.fileno())

        # Preserve permissions/ownership where possible.
        try:
            os.chmod(tmp_path, stat_mod.S_IMODE(st.st_mode))
        except OSError:
            pass
        try:
            os.chown(tmp_path, st.st_uid, st.st_gid)
        except (OSError, AttributeError):
            pass

        # Atomic replace.
        os.replace(tmp_path, abs_path)

        # Restore timestamps.
        try:
            os.utime(abs_path, (st.st_atime, st.st_mtime))
        except OSError:
            pass

    except BaseException:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
        raise


def process_all(scope, eligible_files, index, analysis, empty_mode='keep', progress=None):
    """Rewrite only the affected files. Returns the list of modified rel paths."""
    modified = []
    affected = analysis.files_affected
    for rel_path, abs_path in eligible_files:
        if rel_path not in affected:
            continue
        if progress:
            progress(rel_path)
        _rewrite_one(rel_path, abs_path, index, empty_mode)
        modified.append(rel_path)
    return modified
