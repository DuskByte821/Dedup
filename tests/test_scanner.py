"""Tests for dedup.scanner."""

import os
import stat as stat_mod

from dedup import scanner


def _mk(root, files):
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)


def _rel_set(result):
    return {f.rel_path for f in result.eligible}


def _kinds(result):
    return {i.kind for i in result.issues}


def test_basic_discovery(tmp_path):
    root = tmp_path / "wl"
    _mk(root, {
        "a.txt": b"x\n",
        "b.lst": b"y\n",
        "nested/c.txt": b"z\n",
        "nested/deep/d.wordlist": b"w\n",
        "README.md": b"ignored",
        "LICENSE": b"ignored",
    })
    result = scanner.scan("main", root)
    assert _rel_set(result) == {"a.txt", "b.lst", "nested/c.txt", "nested/deep/d.wordlist"}


def test_deterministic_order(tmp_path):
    root = tmp_path / "wl"
    _mk(root, {"z.txt": b"", "a.txt": b"", "nested/c.txt": b"", "nested/a.txt": b""})
    result = scanner.scan("r", root)
    rels = [f.rel_path for f in result.eligible]
    assert rels == sorted(rels)


def test_symlink_file_skipped(tmp_path):
    root = tmp_path / "wl"
    root.mkdir()
    (root / "real.txt").write_bytes(b"x\n")
    (root / "link.txt").symlink_to(root / "real.txt")
    result = scanner.scan("r", root)
    assert "real.txt" in _rel_set(result)
    assert "link.txt" not in _rel_set(result)
    assert "symlink_file" in _kinds(result)


def test_symlink_dir_skipped(tmp_path):
    root = tmp_path / "wl"
    real = tmp_path / "real"
    real.mkdir()
    (real / "x.txt").write_bytes(b"x\n")
    root.mkdir()
    (root / "linkdir").symlink_to(real)
    result = scanner.scan("r", root)
    assert _rel_set(result) == set()
    assert "symlink_dir" in _kinds(result)


def test_excluded_dir(tmp_path):
    root = tmp_path / "wl"
    _mk(root, {"a.txt": b"", ".dedup-backups/old/x.txt": b""})
    result = scanner.scan("r", root)
    assert _rel_set(result) == {"a.txt"}
    assert "excluded_dir" in _kinds(result)


def test_unreadable_file(tmp_path):
    if os.geteuid() == 0:
        return  # root bypasses permission bits
    root = tmp_path / "wl"
    root.mkdir()
    f = root / "secret.txt"
    f.write_bytes(b"x\n")
    f.chmod(0)
    try:
        result = scanner.scan("r", root)
        assert "secret.txt" not in _rel_set(result)
        assert "unreadable_file" in _kinds(result)
    finally:
        f.chmod(0o644)


def test_extension_case_insensitive(tmp_path):
    root = tmp_path / "wl"
    _mk(root, {"A.TXT": b"", "b.Lst": b"", "c.WordList": b""})
    result = scanner.scan("r", root)
    assert _rel_set(result) == {"A.TXT", "b.Lst", "c.WordList"}


def test_no_extension_skipped(tmp_path):
    root = tmp_path / "wl"
    _mk(root, {"noext": b"", "a.txt": b""})
    result = scanner.scan("r", root)
    assert _rel_set(result) == {"a.txt"}
    assert "skipped_extension" in _kinds(result)


def test_directories_counted(tmp_path):
    root = tmp_path / "wl"
    _mk(root, {"a.txt": b"", "x/b.txt": b"", "x/y/c.txt": b""})
    result = scanner.scan("r", root)
    # root + x + x/y = 3
    assert result.directories_discovered == 3


def test_metadata_captured(tmp_path):
    root = tmp_path / "wl"
    root.mkdir()
    f = root / "a.txt"
    f.write_bytes(b"hello\n")
    st = f.stat()
    result = scanner.scan("r", root)
    assert len(result.eligible) == 1
    ef = result.eligible[0]
    assert ef.size == st.st_size
    assert ef.mtime_ns == st.st_mtime_ns


def test_empty_tree(tmp_path):
    root = tmp_path / "wl"
    root.mkdir()
    result = scanner.scan("r", root)
    assert result.eligible == []
    assert result.directories_discovered == 1


def test_unusual_filenames(tmp_path):
    root = tmp_path / "wl"
    root.mkdir()
    names = [
        "with space.txt",
        "with-dash.txt",
        "with_underscore.txt",
        "UPPER.TXT",
        "emoji-☕.txt",
    ]
    for n in names:
        (root / n).write_bytes(b"x\n")
    result = scanner.scan("r", root)
    assert _rel_set(result) == set(names)


def test_deep_nesting(tmp_path):
    root = tmp_path / "wl"
    deep = "a/b/c/d/e/f/g/h/i/j/k/l/m/n/o/p/q/r/s/t/u/v/w/x/y/z.txt"
    _mk(root, {deep: b"deep\n"})
    result = scanner.scan("r", root)
    assert _rel_set(result) == {deep}


def test_custom_extensions(tmp_path):
    root = tmp_path / "wl"
    _mk(root, {"a.txt": b"", "b.custom": b""})
    result = scanner.scan("r", root, extensions={".custom"})
    assert _rel_set(result) == {"b.custom"}


def test_root_name_propagated(tmp_path):
    root = tmp_path / "wl"
    _mk(root, {"a.txt": b""})
    result = scanner.scan("myroot", root)
    assert result.eligible[0].root_name == "myroot"


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