"""Tests for dedup.index."""

import hashlib

from dedup.index import Index, IndexError_


def _mk_index(tmp_path, **kw):
    return Index(tmp_path / "idx.db", **kw)


def test_ensure_root_idempotent(tmp_path):
    idx = _mk_index(tmp_path)
    a = idx.ensure_root("username", "/data/u")
    b = idx.ensure_root("username", "/data/u")
    assert a == b
    idx.close()


def test_ensure_root_path_conflict(tmp_path):
    idx = _mk_index(tmp_path)
    idx.ensure_root("username", "/data/u")
    try:
        idx.ensure_root("username", "/different/path")
    except IndexError_:
        idx.close()
        return
    raise AssertionError("Expected IndexError_")


def test_register_file_idempotent(tmp_path):
    idx = _mk_index(tmp_path)
    rid = idx.ensure_root("r", "/p")
    a = idx.register_file(rid, "a.txt", 10, 123)
    b = idx.register_file(rid, "a.txt", 10, 123)
    assert a == b
    idx.close()


def test_register_file_conflict(tmp_path):
    idx = _mk_index(tmp_path)
    rid = idx.ensure_root("r", "/p")
    idx.register_file(rid, "a.txt", 10, 123)
    try:
        idx.register_file(rid, "a.txt", 20, 123)
    except IndexError_:
        idx.close()
        return
    raise AssertionError("Expected IndexError_")


def test_lookup_add_same_root(tmp_path):
    idx = _mk_index(tmp_path)
    rid = idx.ensure_root("r", "/p")
    fid = idx.register_file(rid, "a.txt", 0, 0)
    h = hashlib.sha256(b"admin").digest()
    assert idx.lookup(rid, h, b"admin") is None
    idx.add(rid, h, b"admin", fid, 1)
    assert idx.lookup(rid, h, b"admin") == (fid, 1)
    idx.close()


def test_root_isolation(tmp_path):
    """Same entry in two roots is not a duplicate."""
    idx = _mk_index(tmp_path)
    r1 = idx.ensure_root("username", "/u")
    r2 = idx.ensure_root("password", "/p")
    f1 = idx.register_file(r1, "a.txt", 0, 0)
    f2 = idx.register_file(r2, "b.txt", 0, 0)
    h = hashlib.sha256(b"admin").digest()
    idx.add(r1, h, b"admin", f1, 1)
    assert idx.lookup(r2, h, b"admin") is None
    idx.add(r2, h, b"admin", f2, 1)
    assert idx.lookup(r1, h, b"admin") == (f1, 1)
    assert idx.lookup(r2, h, b"admin") == (f2, 1)
    assert idx.count_unique(r1) == 1
    assert idx.count_unique(r2) == 1
    idx.close()


def test_hash_collision_safety(tmp_path):
    """Two different contents sharing a hash are not merged."""
    idx = _mk_index(tmp_path)
    rid = idx.ensure_root("r", "/p")
    f1 = idx.register_file(rid, "a.txt", 0, 0)
    f2 = idx.register_file(rid, "b.txt", 0, 0)
    fake_h = b"\x00" * 32
    idx.add(rid, fake_h, b"content-1", f1, 1)
    assert idx.lookup(rid, fake_h, b"content-2") is None  # different content
    idx.add(rid, fake_h, b"content-2", f2, 5)
    assert idx.lookup(rid, fake_h, b"content-1") == (f1, 1)
    assert idx.lookup(rid, fake_h, b"content-2") == (f2, 5)
    assert idx.count_unique(rid) == 2
    idx.close()


def test_flush_persistence(tmp_path):
    """Flushed entries survive reopening the db."""
    idx = _mk_index(tmp_path)
    rid = idx.ensure_root("r", "/p")
    fid = idx.register_file(rid, "a.txt", 0, 0)
    h = hashlib.sha256(b"x").digest()
    idx.add(rid, h, b"x", fid, 1)
    idx.flush()
    idx.close()

    idx2 = _mk_index(tmp_path)
    rid2 = idx2.root_id("r")
    assert idx2.lookup(rid2, h, b"x") == (fid, 1)
    idx2.close()


def test_pending_visible_before_flush(tmp_path):
    idx = _mk_index(tmp_path)
    rid = idx.ensure_root("r", "/p")
    fid = idx.register_file(rid, "a.txt", 0, 0)
    h = hashlib.sha256(b"x").digest()
    idx.add(rid, h, b"x", fid, 1)
    # No flush() call here.
    assert idx.lookup(rid, h, b"x") == (fid, 1)
    idx.close()


def test_iter_unique(tmp_path):
    idx = _mk_index(tmp_path)
    rid = idx.ensure_root("r", "/p")
    fid = idx.register_file(rid, "a.txt", 0, 0)
    for i, c in enumerate([b"a", b"b", b"c"], start=1):
        idx.add(rid, hashlib.sha256(c).digest(), c, fid, i)
    items = sorted(idx.iter_unique(rid), key=lambda r: r[1])
    assert [c for _, c, _, _ in items] == [b"a", b"b", b"c"]
    idx.close()


if __name__ == "__main__":
    import sys
    import tempfile
    from pathlib import Path

    mod = sys.modules[__name__]
    failed = 0
    for name in sorted(dir(mod)):
        if not name.startswith("test_"):
            continue
        fn = getattr(mod, name)
        with tempfile.TemporaryDirectory() as td:
            try:
                fn(Path(td))
            except Exception as e:
                print(f"FAIL {name}: {e!r}")
                failed += 1
            else:
                print(f"ok   {name}")
    sys.exit(1 if failed else 0)