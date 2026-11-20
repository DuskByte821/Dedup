"""Tests for dedup.analyzer."""

import hashlib

from dedup import scanner
from dedup.analyzer import analyze_roots
from dedup.index import Index


def _mk(root, files):
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)


def _run(tmp_path, roots, empty_mode="keep"):
    """roots: dict name -> path. Returns (result, index)."""
    idx = Index(tmp_path / "idx.db")
    scans = []
    for name, path in roots.items():
        scans.append(scanner.scan(name, path))
    result = analyze_roots(scans, idx, empty_mode=empty_mode)
    return result, idx


# ---------------------------------------------------------------------------
# Basic classification
# ---------------------------------------------------------------------------

def test_internal_and_cross_file(tmp_path):
    root = tmp_path / "u"
    _mk(root, {
        "a.txt": b"apple\nbanana\napple\ncherry\n",
        "b.txt": b"banana\ndate\n",
        "nested/c.txt": b"apple\ndate\nelderberry\n",
    })
    result, idx = _run(tmp_path, {"u": root})
    r = result.roots["u"]
    assert r.total_entries == 9
    assert r.unique_entries == 5
    assert r.internal_duplicates == 1   # second "apple" in a.txt
    assert r.cross_file_duplicates == 3 # banana in b, apple in c, date in c
    assert r.files_affected == {"a.txt", "b.txt", "nested/c.txt"}
    idx.close()


def test_ownership_deterministic(tmp_path):
    root = tmp_path / "u"
    _mk(root, {
        "b.txt": b"shared\nb-only\n",
        "a.txt": b"shared\na-only\n",
        "nested/c.txt": b"shared\nc-only\n",
    })
    result, idx = _run(tmp_path, {"u": root})
    r = result.roots["u"]
    rid = idx.root_id("u")
    h = hashlib.sha256(b"shared").digest()
    owner = idx.lookup(rid, h, b"shared")
    a_meta = idx.file_meta(owner[0])
    assert a_meta[1] == "a.txt"
    assert owner[1] == 1
    # Two cross-file duplicates: from b.txt and from nested/c.txt.
    assert r.cross_file_duplicates == 2
    assert r.internal_duplicates == 0
    idx.close()


# ---------------------------------------------------------------------------
# Root isolation
# ---------------------------------------------------------------------------

def test_root_isolation(tmp_path):
    u = tmp_path / "u"
    p = tmp_path / "p"
    _mk(u, {"users.txt": b"admin\nalice\n"})
    _mk(p, {"common.txt": b"admin\nalice\npassword123\n"})
    result, idx = _run(tmp_path, {"username": u, "password": p})

    ru = result.roots["username"]
    rp = result.roots["password"]
    assert ru.unique_entries == 2
    assert rp.unique_entries == 3
    assert ru.internal_duplicates == 0
    assert ru.cross_file_duplicates == 0
    assert rp.internal_duplicates == 0
    assert rp.cross_file_duplicates == 0
    assert ru.files_affected == set()
    assert rp.files_affected == set()
    idx.close()


def test_root_isolation_with_duplicates_within_each(tmp_path):
    u = tmp_path / "u"
    p = tmp_path / "p"
    _mk(u, {"a.txt": b"admin\n", "b.txt": b"admin\n"})
    _mk(p, {"a.txt": b"admin\n", "b.txt": b"admin\n"})
    result, idx = _run(tmp_path, {"username": u, "password": p})

    ru = result.roots["username"]
    rp = result.roots["password"]
    assert ru.cross_file_duplicates == 1
    assert rp.cross_file_duplicates == 1
    assert ru.files_affected == {"b.txt"}
    assert rp.files_affected == {"b.txt"}
    idx.close()


# ---------------------------------------------------------------------------
# Empty-line policies
# ---------------------------------------------------------------------------

def test_empty_keep(tmp_path):
    root = tmp_path / "u"
    _mk(root, {"a.txt": b"one\n\none\n\ntwo\n"})
    result, idx = _run(tmp_path, {"u": root}, empty_mode="keep")
    r = result.roots["u"]
    assert r.total_entries == 3
    assert r.unique_entries == 2
    assert r.internal_duplicates == 1
    assert r.empty_lines_kept == 2
    assert r.empty_lines_dropped == 0
    idx.close()


def test_empty_dedup(tmp_path):
    root = tmp_path / "u"
    _mk(root, {"a.txt": b"one\n\n\n"})
    result, idx = _run(tmp_path, {"u": root}, empty_mode="dedup")
    r = result.roots["u"]
    # Entries: "one", "", "" -> 3 entries, 2 unique (one and empty once)
    assert r.total_entries == 3
    assert r.unique_entries == 2
    assert r.internal_duplicates == 1
    idx.close()


def test_empty_drop(tmp_path):
    root = tmp_path / "u"
    _mk(root, {"a.txt": b"one\n\none\n\ntwo\n"})
    result, idx = _run(tmp_path, {"u": root}, empty_mode="drop")
    r = result.roots["u"]
    assert r.total_entries == 3
    assert r.unique_entries == 2
    assert r.internal_duplicates == 1
    assert r.empty_lines_dropped == 2
    idx.close()


# ---------------------------------------------------------------------------
# Line endings
# ---------------------------------------------------------------------------

def test_crlf_and_cr_equivalence(tmp_path):
    root = tmp_path / "u"
    _mk(root, {
        "a.txt": b"one\r\ntwo\r\n",   # CRLF
        "b.txt": b"two\rthree\r",     # CR
    })
    result, idx = _run(tmp_path, {"u": root})
    r = result.roots["u"]
    assert r.total_entries == 4    # one, two | two, three
    assert r.unique_entries == 3   # one, two, three
    assert r.cross_file_duplicates == 1
    idx.close()


def test_whitespace_distinct(tmp_path):
    root = tmp_path / "u"
    _mk(root, {"a.txt": b"password\nPassword\npassword \n password\npassword\t\n"})
    result, idx = _run(tmp_path, {"u": root})
    r = result.roots["u"]
    assert r.total_entries == 5
    assert r.unique_entries == 5
    assert r.internal_duplicates == 0
    idx.close()


# ---------------------------------------------------------------------------
# Emptied files
# ---------------------------------------------------------------------------

def test_file_becomes_empty(tmp_path):
    root = tmp_path / "u"
    _mk(root, {
        "a.txt": b"unique-1\n",
        "b.txt": b"unique-1\nunique-1\n",
    })
    result, idx = _run(tmp_path, {"u": root})
    r = result.roots["u"]
    # b.txt: line 1 is a cross-file dup, line 2 is internal dup -> all removed.
    assert "b.txt" in r.files_affected
    assert "b.txt" in r.files_emptied
    assert r.per_file["b.txt"].bytes_after == 0
    idx.close()


# ---------------------------------------------------------------------------
# Byte accounting
# ---------------------------------------------------------------------------

def test_bytes_after_accounting_lf(tmp_path):
    root = tmp_path / "u"
    _mk(root, {"a.txt": b"a\nb\na\n"})   # 6 bytes, one internal dup
    result, idx = _run(tmp_path, {"u": root})
    r = result.roots["u"]
    # retained: a\n b\n -> 4 bytes
    assert r.per_file["a.txt"].bytes_before == 6
    assert r.per_file["a.txt"].bytes_after == 4
    idx.close()


def test_no_duplicates_no_affected(tmp_path):
    root = tmp_path / "u"
    _mk(root, {"a.txt": b"x\ny\nz\n"})
    result, idx = _run(tmp_path, {"u": root})
    r = result.roots["u"]
    assert r.files_affected == set()
    assert r.unique_entries == 3
    idx.close()


# ---------------------------------------------------------------------------
# Progress callback
# ---------------------------------------------------------------------------

def test_progress_callback(tmp_path):
    root = tmp_path / "u"
    _mk(root, {"a.txt": b"x\n", "b.txt": b"y\n"})
    calls = []
    idx = Index(tmp_path / "idx.db")
    scans = [scanner.scan("u", root)]
    analyze_roots(scans, idx, progress=lambda *a: calls.append(a))
    idx.close()
    assert len(calls) == 2
    assert calls[0][0] == "u"
    assert calls[0][2] == 1 and calls[0][3] == 2
    assert calls[1][2] == 2 and calls[1][3] == 2


# ---------------------------------------------------------------------------
# Multi-root aggregate
# ---------------------------------------------------------------------------

def test_aggregate_totals(tmp_path):
    u = tmp_path / "u"
    p = tmp_path / "p"
    _mk(u, {"a.txt": b"x\nx\n"})
    _mk(p, {"b.txt": b"y\ny\n"})
    result, idx = _run(tmp_path, {"username": u, "password": p})
    assert result.total_files_affected == 2
    assert result.total_entries == 4
    assert result.total_unique == 2
    assert result.total_internal_duplicates == 2
    assert result.total_cross_file_duplicates == 0
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