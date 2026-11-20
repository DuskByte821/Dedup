"""Tests for dedup.safety."""

from dedup import safety
from dedup.safety import SafetyError


def _expect_safety_error(fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except SafetyError:
        return
    raise AssertionError("Expected SafetyError")


def test_validate_root_name_ok():
    for ok in ("username", "user-name_2", "A", "a" * 64):
        safety.validate_root_name(ok)


def test_validate_root_name_rejects_empty():
    _expect_safety_error(safety.validate_root_name, "")


def test_validate_root_name_rejects_specials():
    for bad in ("user name", "user/name", "user.name", "üser", "../x", "a.b"):
        _expect_safety_error(safety.validate_root_name, bad)


def test_validate_root_name_rejects_too_long():
    _expect_safety_error(safety.validate_root_name, "a" * 65)


def test_validate_root_name_rejects_reserved():
    for r in ("total", "operation", "manifest", "dedup", "index", "backup"):
        _expect_safety_error(safety.validate_root_name, r)


def test_derive_root_name_plain(tmp_path):
    assert safety.derive_root_name(str(tmp_path / "wordlists")) == "wordlists"


def test_derive_root_name_sanitized(tmp_path):
    assert safety.derive_root_name(str(tmp_path / "my dir")) == "my_dir"
    assert safety.derive_root_name(str(tmp_path / "wordlists (2024)")) == "wordlists_2024"


def test_derive_root_name_hidden(tmp_path):
    assert safety.derive_root_name(str(tmp_path / ".hidden")) == "hidden"


def test_derive_root_name_reserved_collision(tmp_path):
    assert safety.derive_root_name(str(tmp_path / "backup")) == "backup_root"


def test_resolve_root_path_missing():
    _expect_safety_error(safety.resolve_root_path, "/definitely/not/here", "x")


def test_resolve_root_path_not_dir(tmp_path):
    f = tmp_path / "file.txt"
    f.write_bytes(b"x")
    _expect_safety_error(safety.resolve_root_path, str(f), "x")


def test_resolve_root_path_dangerous():
    _expect_safety_error(safety.resolve_root_path, "/etc", "x")


def test_resolve_root_path_inside_excluded(tmp_path):
    p = tmp_path / ".dedup-backups" / "old"
    p.mkdir(parents=True)
    _expect_safety_error(safety.resolve_root_path, str(p), "x")


def test_resolve_root_path_normal(tmp_path):
    p = tmp_path / "wl"
    p.mkdir()
    resolved = safety.resolve_root_path(str(p), "wl")
    assert resolved == p.resolve()


def test_overlap_equal(tmp_path):
    p = tmp_path / "a"
    p.mkdir()
    _expect_safety_error(safety.check_root_overlap, [("x", p), ("y", p)])


def test_overlap_ancestor(tmp_path):
    a = tmp_path / "a"
    b = a / "b"
    b.mkdir(parents=True)
    _expect_safety_error(safety.check_root_overlap, [("x", a), ("y", b)])
    _expect_safety_error(safety.check_root_overlap, [("x", b), ("y", a)])


def test_overlap_siblings_ok(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    safety.check_root_overlap([("x", a), ("y", b)])  # no raise


def test_overlap_three_roots(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    c = tmp_path / "c"
    for d in (a, b, c):
        d.mkdir()
    safety.check_root_overlap([("x", a), ("y", b), ("z", c)])  # no raise


def test_disk_space_ok(tmp_path):
    safety.check_disk_space(tmp_path, 0)


def test_disk_space_insufficient(tmp_path):
    _expect_safety_error(safety.check_disk_space, tmp_path, 10 ** 30)


def test_format_bytes():
    assert safety.format_bytes(0) == "0 B"
    assert safety.format_bytes(1023) == "1023 B"
    assert safety.format_bytes(1024) == "1.00 KB"
    assert safety.format_bytes(1536) == "1.50 KB"
    assert safety.format_bytes(1024 ** 3) == "1.00 GB"


if __name__ == "__main__":
    import sys
    mod = sys.modules[__name__]
    failed = 0
    for name in sorted(dir(mod)):
        if name.startswith("test_"):
            fn = getattr(mod, name)
            try:
                if fn.__code__.co_argcount == 0:
                    fn()
                else:
                    import tempfile
                    from pathlib import Path
                    with tempfile.TemporaryDirectory() as td:
                        fn(Path(td))
            except Exception as e:
                print(f"FAIL {name}: {e!r}")
                failed += 1
            else:
                print(f"ok   {name}")
    sys.exit(1 if failed else 0)