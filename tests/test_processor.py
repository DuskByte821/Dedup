"""Tests for dedup.processor."""

import os

from dedup import scanner, analyzer, processor
from dedup.analyzer import analyze_roots
from dedup.index import Index


def _mk(root, files):
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)


def _prep(tmp_path, roots, empty_mode="keep"):
    idx = Index(tmp_path / "idx.db")
    scans = [scanner.scan(name, path) for name, path in roots.items()]
    analysis = analyze_roots(scans, idx, empty_mode=empty_mode)
    return idx, scans, analysis


# ---------------------------------------------------------------------------
# Core behavior
# ---------------------------------------------------------------------------

def test_internal_duplicate_removed_order_preserved(tmp_path):
    root = tmp_path / "u"
    _mk(root, {"a.txt": b"zebra\napple\nbanana\napple\norange\n"})
    idx, scans, analysis = _prep(tmp_path, {"u": root})
    processor.process_roots(scans, analysis, idx)
    idx.close()
    assert (root / "a.txt").read_bytes() == b"zebra\napple\nbanana\norange\n"


def test_cross_file_dedup_keeps_owner(tmp_path):
    root = tmp_path / "u"
    _mk(root, {
        "a.txt": b"apple\nbanana\norange\n",
        "b.txt": b"banana\ngrape\norange\n",
    })
    idx, scans, analysis = _prep(tmp_path, {"u": root})
    processor.process_roots(scans, analysis, idx)
    idx.close()
    assert (root / "a.txt").read_bytes() == b"apple\nbanana\norange\n"
    assert (root / "b.txt").read_bytes() == b"grape\n"


def test_no_duplicates_leaves_file_untouched(tmp_path):
    root = tmp_path / "u"
    _mk(root, {"a.txt": b"x\ny\nz\n"})
    idx, scans, analysis = _prep(tmp_path, {"u": root})
    processor.process_roots(scans, analysis, idx)
    idx.close()
    assert (root / "a.txt").read_bytes() == b"x\ny\nz\n"


def test_lf_normalization(tmp_path):
    root = tmp_path / "u"
    _mk(root, {"a.txt": b"one\r\ntwo\r\none\r\nthree\r"})
    idx, scans, analysis = _prep(tmp_path, {"u": root})
    processor.process_roots(scans, analysis, idx)
    idx.close()
    assert (root / "a.txt").read_bytes() == b"one\ntwo\nthree\n"


def test_final_newline_preserved_when_present(tmp_path):
    root = tmp_path / "u"
    _mk(root, {"a.txt": b"one\none\ntwo\n"})
    idx, scans, analysis = _prep(tmp_path, {"u": root})
    processor.process_roots(scans, analysis, idx)
    idx.close()
    assert (root / "a.txt").read_bytes() == b"one\ntwo\n"


def test_final_newline_absent_is_preserved(tmp_path):
    root = tmp_path / "u"
    _mk(root, {"a.txt": b"one\none\ntwo"})
    idx, scans, analysis = _prep(tmp_path, {"u": root})
    processor.process_roots(scans, analysis, idx)
    idx.close()
    assert (root / "a.txt").read_bytes() == b"one\ntwo"


def test_final_newline_absent_with_last_line_dropped(tmp_path):
    root = tmp_path / "u"
    _mk(root, {
        "a.txt": b"one\ntwo\n",
        "b.txt": b"one\ntwo\none",   # no trailing newline
    })
    idx, scans, analysis = _prep(tmp_path, {"u": root})
    processor.process_roots(scans, analysis, idx)
    idx.close()
    assert (root / "a.txt").read_bytes() == b"one\ntwo\n"
    assert (root / "b.txt").read_bytes() == b""


# ---------------------------------------------------------------------------
# Empty-line policies
# ---------------------------------------------------------------------------

def test_empty_keep(tmp_path):
    root = tmp_path / "u"
    _mk(root, {"a.txt": b"one\n\none\n\ntwo\n"})
    idx, scans, analysis = _prep(tmp_path, {"u": root}, empty_mode="keep")
    processor.process_roots(scans, analysis, idx, empty_mode="keep")
    idx.close()
    assert (root / "a.txt").read_bytes() == b"one\n\n\ntwo\n"


def test_empty_drop(tmp_path):
    root = tmp_path / "u"
    _mk(root, {"a.txt": b"one\n\none\n\ntwo\n"})
    idx, scans, analysis = _prep(tmp_path, {"u": root}, empty_mode="drop")
    processor.process_roots(scans, analysis, idx, empty_mode="drop")
    idx.close()
    assert (root / "a.txt").read_bytes() == b"one\ntwo\n"


def test_empty_dedup(tmp_path):
    root = tmp_path / "u"
    _mk(root, {"a.txt": b"one\n\n\ntwo\n"})
    idx, scans, analysis = _prep(tmp_path, {"u": root}, empty_mode="dedup")
    processor.process_roots(scans, analysis, idx, empty_mode="dedup")
    idx.close()
    # first empty kept, second dropped
    assert (root / "a.txt").read_bytes() == b"one\n\ntwo\n"


# ---------------------------------------------------------------------------
# File becomes empty
# ---------------------------------------------------------------------------

def test_file_becomes_empty(tmp_path):
    root = tmp_path / "u"
    _mk(root, {
        "a.txt": b"shared\n",
        "b.txt": b"shared\n",
    })
    idx, scans, analysis = _prep(tmp_path, {"u": root})
    processor.process_roots(scans, analysis, idx)
    idx.close()
    assert (root / "a.txt").read_bytes() == b"shared\n"
    assert (root / "b.txt").read_bytes() == b""


# ---------------------------------------------------------------------------
# Concurrent-modification guard
# ---------------------------------------------------------------------------

def test_aborts_on_size_change(tmp_path):
    root = tmp_path / "u"
    _mk(root, {"a.txt": b"x\nx\n"})
    idx, scans, analysis = _prep(tmp_path, {"u": root})
    (root / "a.txt").write_bytes(b"changed after scan\n")
    try:
        processor.process_roots(scans, analysis, idx)
    except processor.ProcessingError as e:
        assert "changed since scan" in str(e).lower()
        idx.close()
        return
    idx.close()
    raise AssertionError("expected ProcessingError")


def test_aborts_on_mtime_change(tmp_path):
    root = tmp_path / "u"
    _mk(root, {"a.txt": b"x\nx\n"})
    idx, scans, analysis = _prep(tmp_path, {"u": root})
    p = root / "a.txt"
    st = p.stat()
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    try:
        processor.process_roots(scans, analysis, idx)
    except processor.ProcessingError as e:
        assert "changed since scan" in str(e).lower()
        idx.close()
        return
    idx.close()
    raise AssertionError("expected ProcessingError")


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------

def test_preserves_mode(tmp_path):
    root = tmp_path / "u"
    root.mkdir()
    f = root / "a.txt"
    f.write_bytes(b"x\nx\n")
    f.chmod(0o640)
    try:
        idx, scans, analysis = _prep(tmp_path, {"u": root})
        processor.process_roots(scans, analysis, idx)
        idx.close()
        assert (f.stat().st_mode & 0o777) == 0o640
    finally:
        f.chmod(0o644)


def test_no_temp_files_left_behind(tmp_path):
    root = tmp_path / "u"
    _mk(root, {"a.txt": b"x\nx\n", "sub/b.txt": b"y\ny\n"})
    idx, scans, analysis = _prep(tmp_path, {"u": root})
    processor.process_roots(scans, analysis, idx)
    idx.close()
    leftovers = [p for p in root.rglob("*") if ".dedup-tmp-" in p.name]
    assert leftovers == []


# ---------------------------------------------------------------------------
# Root isolation
# ---------------------------------------------------------------------------

def test_shared_entry_preserved_in_both_roots(tmp_path):
    u = tmp_path / "u"
    p = tmp_path / "p"
    _mk(u, {"users.txt": b"admin\nalice\njohn\n"})
    _mk(p, {"common.txt": b"admin\nalice\npassword123\n"})
    idx, scans, analysis = _prep(
        tmp_path, {"username": u, "password": p})
    processor.process_roots(scans, analysis, idx)
    idx.close()
    assert (u / "users.txt").read_bytes() == b"admin\nalice\njohn\n"
    assert (p / "common.txt").read_bytes() == b"admin\nalice\npassword123\n"


def test_shared_entry_duplicated_only_within_root(tmp_path):
    u = tmp_path / "u"
    p = tmp_path / "p"
    _mk(u, {"a.txt": b"admin\n", "b.txt": b"admin\n"})
    _mk(p, {"a.txt": b"admin\n", "b.txt": b"admin\n"})
    idx, scans, analysis = _prep(
        tmp_path, {"username": u, "password": p})
    processor.process_roots(scans, analysis, idx)
    idx.close()
    assert (u / "a.txt").read_bytes() == b"admin\n"
    assert (u / "b.txt").read_bytes() == b""
    assert (p / "a.txt").read_bytes() == b"admin\n"
    assert (p / "b.txt").read_bytes() == b""


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

def test_deterministic_ownership_within_root(tmp_path):
    root = tmp_path / "u"
    _mk(root, {
        "z.txt": b"shared\n",
        "a.txt": b"shared\n",
        "m/n.txt": b"shared\n",
    })
    idx, scans, analysis = _prep(tmp_path, {"u": root})
    processor.process_roots(scans, analysis, idx)
    idx.close()
    # a.txt sorts first, owns the entry.
    assert (root / "a.txt").read_bytes() == b"shared\n"
    assert (root / "z.txt").read_bytes() == b""
    assert (root / "m" / "n.txt").read_bytes() == b""

def test_no_final_newline_after_modification(tmp_path):
    """Files that end without a newline must survive deduplication.

    Regression: earlier versions called .read() on a write-only temp
    handle and raised io.UnsupportedOperation("read").
    """
    root = tmp_path / "u"
    _mk(root, {"a.txt": b"one\none\ntwo"})   # no trailing newline
    idx, scans, analysis = _prep(tmp_path, {"u": root})
    processor.process_roots(scans, analysis, idx)
    idx.close()
    assert (root / "a.txt").read_bytes() == b"one\ntwo"


def test_no_final_newline_cross_file(tmp_path):
    root = tmp_path / "u"
    _mk(root, {
        "a.txt": b"shared\nonly-a\n",
        "b.txt": b"shared\nonly-b",   # no trailing newline
    })
    idx, scans, analysis = _prep(tmp_path, {"u": root})
    processor.process_roots(scans, analysis, idx)
    idx.close()
    assert (root / "a.txt").read_bytes() == b"shared\nonly-a\n"
    assert (root / "b.txt").read_bytes() == b"only-b"


def test_no_final_newline_all_lines_deduped(tmp_path):
    root = tmp_path / "u"
    _mk(root, {
        "a.txt": b"shared\n",
        "b.txt": b"shared",           # no trailing newline, all deduped
    })
    idx, scans, analysis = _prep(tmp_path, {"u": root})
    processor.process_roots(scans, analysis, idx)
    idx.close()
    assert (root / "a.txt").read_bytes() == b"shared\n"
    assert (root / "b.txt").read_bytes() == b""


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