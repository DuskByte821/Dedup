"""Disk-backed SQLite index for duplicate detection.

Layout:
  files(id, path)
  entries(hash, content, file_id, line_no)

Content is stored so that hash collisions cannot cause data loss: any candidate
match is confirmed by comparing the actual bytes.
"""

import sqlite3
from pathlib import Path


class Index:
    PENDING_LIMIT = 100_000

    def __init__(self, path):
        self.path = Path(path)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA temp_store=MEMORY")
        self.conn.execute("PRAGMA cache_size=-200000")
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY,
                path TEXT UNIQUE NOT NULL
            );
            CREATE TABLE IF NOT EXISTS entries (
                hash BLOB NOT NULL,
                content BLOB NOT NULL,
                file_id INTEGER NOT NULL,
                line_no INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_entries_hash ON entries(hash);
        """)
        self.conn.commit()
        self._file_ids = {}
        self._file_paths = {}
        self._pending = {}          # hash -> [(content, file_id, line_no), ...]
        self._pending_count = 0

    # -- file lookup -----------------------------------------------------

    def _file_id(self, path):
        fid = self._file_ids.get(path)
        if fid is not None:
            return fid
        cur = self.conn.execute("SELECT id FROM files WHERE path = ?", (path,))
        row = cur.fetchone()
        if row:
            fid = row[0]
        else:
            cur = self.conn.execute("INSERT INTO files (path) VALUES (?)", (path,))
            fid = cur.lastrowid
            self.conn.commit()
        self._file_ids[path] = fid
        self._file_paths[fid] = path
        return fid

    def _file_path(self, fid):
        p = self._file_paths.get(fid)
        if p is not None:
            return p
        cur = self.conn.execute("SELECT path FROM files WHERE id = ?", (fid,))
        row = cur.fetchone()
        if row is None:
            raise KeyError(f"Unknown file id: {fid}")
        p = row[0]
        self._file_paths[fid] = p
        return p

    # -- public API ------------------------------------------------------

    def lookup(self, hash_bytes):
        """Return [(content, file_path, line_no), ...] for hash_bytes."""
        results = []
        for content, fid, line_no in self._pending.get(hash_bytes, ()):
            results.append((content, self._file_path(fid), line_no))
        cur = self.conn.execute(
            "SELECT content, file_id, line_no FROM entries WHERE hash = ?",
            (hash_bytes,))
        for content, fid, line_no in cur:
            results.append((content, self._file_path(fid), line_no))
        return results

    def add(self, hash_bytes, content, file_path, line_no):
        fid = self._file_id(file_path)
        self._pending.setdefault(hash_bytes, []).append((content, fid, line_no))
        self._pending_count += 1
        if self._pending_count >= self.PENDING_LIMIT:
            self.flush()

    def flush(self):
        if not self._pending:
            return
        rows = [(h, c, fid, ln)
                for h, items in self._pending.items()
                for c, fid, ln in items]
        self.conn.executemany(
            "INSERT INTO entries (hash, content, file_id, line_no) VALUES (?, ?, ?, ?)",
            rows)
        self.conn.commit()
        self._pending.clear()
        self._pending_count = 0

    def close(self):
        try:
            self.flush()
        finally:
            self.conn.close()
