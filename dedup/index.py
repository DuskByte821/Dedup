"""Root-scoped SQLite index.

Uniqueness is scoped to a root_id: an entry present in two roots produces
two rows, and neither is ever considered a duplicate of the other.

Schema
------
    roots(id, name UNIQUE, path)
    files(id, root_id, rel_path, size, mtime_ns, UNIQUE(root_id, rel_path))
    unique_entries(
        root_id, hash, content, owner_file_id, owner_line_no,
        PRIMARY KEY (root_id, hash, content)
    ) WITHOUT ROWID

The composite PK means:
  * an entry in root A never collides with the same entry in root B
  * a SHA-256 collision is handled naturally: both rows exist
  * equality is always decided on bytes, never on the hash alone

Pending inserts are buffered in memory up to a byte budget and flushed
in a single transaction. Lookups consult the pending buffer first.
"""

import hashlib
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple


SCHEMA_VERSION = 1


class IndexError_(Exception):
    """Raised for index-level errors (schema mismatch, conflicts, corruption)."""


class Index:
    DEFAULT_PENDING_BYTES = 16 * 1024 * 1024   # ~16 MiB of pending content
    PAGE_CACHE_KIB = 200 * 1024                # ~200 MiB sqlite page cache

    def __init__(self, path, pending_flush_bytes: Optional[int] = None):
        self.path = Path(path)
        self._pending_limit = pending_flush_bytes or self.DEFAULT_PENDING_BYTES

        self.conn = sqlite3.connect(str(self.path))
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA temp_store=MEMORY")
        self.conn.execute(f"PRAGMA cache_size=-{self.PAGE_CACHE_KIB}")

        # Caches (populated lazily; safe to leave stale within one process).
        self._root_ids: Dict[str, int] = {}
        self._root_paths: Dict[str, str] = {}
        self._file_ids: Dict[Tuple[int, str], int] = {}
        self._file_meta: Dict[int, Tuple[int, str, int, int]] = {}

        # Pending inserts.
        # Key: (root_id, hash). Value: list of (content, owner_file_id, owner_line_no).
        self._pending: Dict[Tuple[int, bytes], List[Tuple[bytes, int, int]]] = {}
        self._pending_bytes = 0

        self._init_schema()

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _init_schema(self) -> None:
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS meta (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS roots (
                id   INTEGER PRIMARY KEY,
                name TEXT UNIQUE NOT NULL,
                path TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS files (
                id       INTEGER PRIMARY KEY,
                root_id  INTEGER NOT NULL REFERENCES roots(id),
                rel_path TEXT NOT NULL,
                size     INTEGER NOT NULL,
                mtime_ns INTEGER NOT NULL,
                UNIQUE (root_id, rel_path)
            );

            CREATE TABLE IF NOT EXISTS unique_entries (
                root_id       INTEGER NOT NULL REFERENCES roots(id),
                hash          BLOB NOT NULL,
                content       BLOB NOT NULL,
                owner_file_id INTEGER NOT NULL REFERENCES files(id),
                owner_line_no INTEGER NOT NULL,
                PRIMARY KEY (root_id, hash, content)
            ) WITHOUT ROWID;
        """)
        row = self.conn.execute(
            "SELECT value FROM meta WHERE key='schema_version'").fetchone()
        if row is None:
            self.conn.execute(
                "INSERT INTO meta (key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),))
            self.conn.commit()
        elif int(row[0]) != SCHEMA_VERSION:
            raise IndexError_(
                f"index schema_version mismatch: file has {row[0]}, "
                f"expected {SCHEMA_VERSION}")

    # ------------------------------------------------------------------
    # Roots
    # ------------------------------------------------------------------

    def ensure_root(self, name: str, path: str) -> int:
        rid = self._root_ids.get(name)
        if rid is not None:
            if self._root_paths.get(name) != path:
                raise IndexError_(
                    f"root {name!r} already registered with a different path "
                    f"({self._root_paths.get(name)!r} vs {path!r})")
            return rid
        row = self.conn.execute(
            "SELECT id, path FROM roots WHERE name = ?", (name,)).fetchone()
        if row is None:
            cur = self.conn.execute(
                "INSERT INTO roots (name, path) VALUES (?, ?)", (name, path))
            rid = cur.lastrowid
            self.conn.commit()
        else:
            rid, db_path = row
            if db_path != path:
                raise IndexError_(
                    f"root {name!r} already registered with a different path "
                    f"({db_path!r} vs {path!r})")
        self._root_ids[name] = rid
        self._root_paths[name] = path
        return rid

    def root_id(self, name: str) -> int:
        rid = self._root_ids.get(name)
        if rid is not None:
            return rid
        row = self.conn.execute(
            "SELECT id FROM roots WHERE name = ?", (name,)).fetchone()
        if row is None:
            raise IndexError_(f"unknown root: {name!r}")
        self._root_ids[name] = row[0]
        return row[0]

    # ------------------------------------------------------------------
    # Files
    # ------------------------------------------------------------------

    def register_file(self, root_id: int, rel_path: str,
                      size: int, mtime_ns: int) -> int:
        key = (root_id, rel_path)
        fid = self._file_ids.get(key)
        if fid is not None:
            _, _, db_size, db_mtime = self._file_meta[fid]
            if db_size != size or db_mtime != mtime_ns:
                raise IndexError_(
                    f"file {rel_path!r} re-registered with different "
                    f"size/mtime ({db_size}/{db_mtime} vs {size}/{mtime_ns})")
            return fid

        row = self.conn.execute(
            "SELECT id, size, mtime_ns FROM files "
            "WHERE root_id = ? AND rel_path = ?",
            (root_id, rel_path)).fetchone()
        if row is None:
            cur = self.conn.execute(
                "INSERT INTO files (root_id, rel_path, size, mtime_ns) "
                "VALUES (?, ?, ?, ?)",
                (root_id, rel_path, size, mtime_ns))
            fid = cur.lastrowid
            self.conn.commit()
        else:
            fid, db_size, db_mtime = row
            if db_size != size or db_mtime != mtime_ns:
                raise IndexError_(
                    f"file {rel_path!r} exists in db with different "
                    f"size/mtime ({db_size}/{db_mtime} vs {size}/{mtime_ns})")

        self._file_ids[key] = fid
        self._file_meta[fid] = (root_id, rel_path, size, mtime_ns)
        return fid

    def file_meta(self, file_id: int) -> Tuple[int, str, int, int]:
        meta = self._file_meta.get(file_id)
        if meta is not None:
            return meta
        row = self.conn.execute(
            "SELECT root_id, rel_path, size, mtime_ns FROM files WHERE id = ?",
            (file_id,)).fetchone()
        if row is None:
            raise IndexError_(f"unknown file_id: {file_id}")
        self._file_meta[file_id] = row
        return row

    def iter_files(self, root_id: int) -> Iterator[Tuple[int, str]]:
        cur = self.conn.execute(
            "SELECT id, rel_path FROM files WHERE root_id = ? "
            "ORDER BY rel_path",
            (root_id,))
        yield from cur

    # ------------------------------------------------------------------
    # Entry lookup / add
    # ------------------------------------------------------------------

    def lookup(self, root_id: int, h: bytes, content: bytes
               ) -> Optional[Tuple[int, int]]:
        """Return (owner_file_id, owner_line_no) or None.

        Equality is decided on content bytes. Two entries sharing a hash but
        differing in content are NOT treated as equal.
        """
        pending = self._pending.get((root_id, h))
        if pending is not None:
            for pc, pfid, plno in pending:
                if pc == content:
                    return (pfid, plno)

        row = self.conn.execute(
            "SELECT owner_file_id, owner_line_no FROM unique_entries "
            "WHERE root_id = ? AND hash = ? AND content = ?",
            (root_id, h, content)).fetchone()
        if row is None:
            return None
        return (row[0], row[1])

    def add(self, root_id: int, h: bytes, content: bytes,
            owner_file_id: int, owner_line_no: int) -> None:
        self._pending.setdefault((root_id, h), []).append(
            (content, owner_file_id, owner_line_no))
        # Python object overhead per pending row (tuple, list, hash bytes,
        # dict slot, content bytes header) is roughly 300 bytes, so count
        # that, not just the payload, to keep the budget honest.
        self._pending_bytes += len(content) + 300
        if self._pending_bytes >= self._pending_limit:
            self.flush()

    def flush(self) -> None:
        if not self._pending:
            return

        def rows():
            for (root_id, h), items in self._pending.items():
                for content, fid, lno in items:
                    yield (root_id, h, content, fid, lno)

        self.conn.executemany(
            "INSERT OR IGNORE INTO unique_entries "
            "(root_id, hash, content, owner_file_id, owner_line_no) "
            "VALUES (?, ?, ?, ?, ?)",
            rows())
        self.conn.commit()
        self._pending.clear()
        self._pending_bytes = 0

    # ------------------------------------------------------------------
    # Introspection (used by reporter / verifier)
    # ------------------------------------------------------------------

    def count_unique(self, root_id: int) -> int:
        self.flush()
        row = self.conn.execute(
            "SELECT COUNT(*) FROM unique_entries WHERE root_id = ?",
            (root_id,)).fetchone()
        return row[0]

    def iter_unique(self, root_id: int
                    ) -> Iterator[Tuple[bytes, bytes, int, int]]:
        self.flush()
        cur = self.conn.execute(
            "SELECT hash, content, owner_file_id, owner_line_no "
            "FROM unique_entries WHERE root_id = ?",
            (root_id,))
        yield from cur

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        try:
            self.flush()
        finally:
            self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()