"""Tests for dedup.backup and the backup disk-space logic in dedup.safety."""

import json
import os
from pathlib import Path

from dedup import backup, scanner, analyzer, safety
from dedup.analyzer import analyze_roots
from dedup.index import Index


def _mk(root, files):
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)


def _scan_and_analyze(tmp_path, roots):
    idx = Index(tmp_path / "idx.db")
    scans = [scanner.scan(name, path) for name, path in roots.items()]
    result = analyze_roots(scans, idx)
    idx.close()
    return scans, result


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------

def test_plan_lists_only_affected_files(tmp_path):
    u = tmp_path / "u"
    _mk(u, {"a.txt": b"x\nx\n", "b.txt": b"y\nz\n"})
    scans, analysis = _scan_and_analyze(tmp_path, {"u": u})
    home = tmp_path / ".dedup-backups"
    plan = backup.plan_backup(home, scans, analysis)
    assert {bf.rel_path for bf in plan.files} == {"a.txt"}
    assert plan.total_bytes == (u / "a.txt").stat().st_size


def test_plan_preserves_root_structure(tmp_path):
    u = tmp_path / "u"
    p = tmp_path / "p"
    _mk(u, {"a.txt": b"x\nx\n"})
    _mk(p, {"sub/b.txt": b"y\ny\n"})
    scans, analysis = _scan_and_analyze(
        tmp_path, {"username": u, "password": p})
    plan = backup.plan_backup(tmp_path / ".dedup-backups", scans, analysis)
    roots = sorted(bf.root_name for bf in plan.files)
    assert roots == ["password", "username"]
    rels = sorted((bf.root_name, bf.rel_path) for bf in plan.files)
    assert rels == [("password", "sub/b.txt"), ("username", "a.txt")]


def test_plan_deterministic_order(tmp_path):
    u = tmp_path / "u"
    _mk(u, {"c.txt": b"1\n1\n", "a.txt": b"2\n2\n", "b.txt": b"3\n3\n"})
    scans, analysis = _scan_and_analyze(tmp_path, {"u": u})
    plan = backup.plan_backup(tmp_path / ".dedup-backups", scans, analysis)
    assert [bf.rel_path for bf in plan.files] == ["a.txt", "b.txt", "c.txt"]


def test_plan_unique_dir_on_collision(tmp_path):
    u = tmp_path / "u"
    _mk(u, {"a.txt": b"x\nx\n"})
    scans, analysis = _scan_and_analyze(tmp_path, {"u": u})
    home = tmp_path / ".dedup-backups"

    plan1 = backup.plan_backup(home, scans, analysis)
    plan1.root.mkdir(parents=True)
    plan2 = backup.plan_backup(home, scans, analysis)
    assert plan2.root != plan1.root
    assert plan2.root.name.startswith(plan1.stamp)


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------

def test_execute_copies_files(tmp_path):
    u = tmp_path / "u"
    _mk(u, {"a.txt": b"x\nx\n", "nested/b.txt": b"y\ny\n"})
    scans, analysis = _scan_and_analyze(tmp_path, {"u": u})
    home = tmp_path / ".dedup-backups"
    plan = backup.plan_backup(home, scans, analysis)
    backup.execute_backup(plan)

    assert (plan.root / "u" / "a.txt").read_bytes() == b"x\nx\n"
    assert (plan.root / "u" / "nested" / "b.txt").read_bytes() == b"y\ny\n"


def test_execute_stops_on_existing_dir(tmp_path):
    u = tmp_path / "u"
    _mk(u, {"a.txt": b"x\nx\n"})
    scans, analysis = _scan_and_analyze(tmp_path, {"u": u})
    plan = backup.plan_backup(tmp_path / ".dedup-backups", scans, analysis)
    plan.root.mkdir(parents=True)
    try:
        backup.execute_backup(plan)
    except backup.BackupError as e:
        assert "already exists" in str(e)
        return
    raise AssertionError("expected BackupError")


def test_execute_stops_on_changed_source(tmp_path):
    u = tmp_path / "u"
    _mk(u, {"a.txt": b"x\nx\n"})
    scans, analysis = _scan_and_analyze(tmp_path, {"u": u})
    plan = backup.plan_backup(tmp_path / ".dedup-backups", scans, analysis)

    # Simulate an edit between scan and backup.
    (u / "a.txt").write_bytes(b"changed after scan\n")

    try:
        backup.execute_backup(plan)
    except backup.BackupError as e:
        assert "changed since analysis" in str(e)
        return
    raise AssertionError("expected BackupError")


def test_execute_stops_on_missing_source(tmp_path):
    u = tmp_path / "u"
    _mk(u, {"a.txt": b"x\nx\n"})
    scans, analysis = _scan_and_analyze(tmp_path, {"u": u})
    plan = backup.plan_backup(tmp_path / ".dedup-backups", scans, analysis)

    (u / "a.txt").unlink()

    try:
        backup.execute_backup(plan)
    except backup.BackupError:
        return
    raise AssertionError("expected BackupError")


def test_execute_empty_plan_errors(tmp_path):
    class _Fake:  # minimal duck-typed plan
        files = []
    try:
        backup.execute_backup(_Fake())
    except backup.BackupError as e:
        assert "empty" in str(e)
        return
    raise AssertionError("expected BackupError")


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

def test_verify_passes_when_clean(tmp_path):
    u = tmp_path / "u"
    _mk(u, {"a.txt": b"x\nx\n"})
    scans, analysis = _scan_and_analyze(tmp_path, {"u": u})
    plan = backup.plan_backup(tmp_path / ".dedup-backups", scans, analysis)
    backup.execute_backup(plan)
    backup.verify_backup(plan)  # no raise


def test_verify_detects_tampered_backup(tmp_path):
    u = tmp_path / "u"
    _mk(u, {"a.txt": b"x\nx\n"})
    scans, analysis = _scan_and_analyze(tmp_path, {"u": u})
    plan = backup.plan_backup(tmp_path / ".dedup-backups", scans, analysis)
    backup.execute_backup(plan)

    (plan.root / "u" / "a.txt").write_bytes(b"tampered\n")

    try:
        backup.verify_backup(plan)
    except backup.BackupError as e:
        assert "hash mismatch" in str(e).lower()
        return
    raise AssertionError("expected BackupError")


def test_verify_detects_changed_source(tmp_path):
    u = tmp_path / "u"
    _mk(u, {"a.txt": b"x\nx\n"})
    scans, analysis = _scan_and_analyze(tmp_path, {"u": u})
    plan = backup.plan_backup(tmp_path / ".dedup-backups", scans, analysis)
    backup.execute_backup(plan)

    (u / "a.txt").write_bytes(b"changed\n")

    try:
        backup.verify_backup(plan)
    except backup.BackupError as e:
        assert "source changed" in str(e).lower()
        return
    raise AssertionError("expected BackupError")


def test_verify_detects_missing_backup_file(tmp_path):
    u = tmp_path / "u"
    _mk(u, {"a.txt": b"x\nx\n"})
    scans, analysis = _scan_and_analyze(tmp_path, {"u": u})
    plan = backup.plan_backup(tmp_path / ".dedup-backups", scans, analysis)
    backup.execute_backup(plan)

    (plan.root / "u" / "a.txt").unlink()

    try:
        backup.verify_backup(plan)
    except backup.BackupError as e:
        assert "missing" in str(e).lower()
        return
    raise AssertionError("expected BackupError")


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------

def test_write_operation_json_contents(tmp_path):
    u = tmp_path / "u"
    p = tmp_path / "p"
    _mk(u, {"a.txt": b"x\nx\n"})
    _mk(p, {"b.txt": b"y\ny\n"})
    scans, analysis = _scan_and_analyze(
        tmp_path, {"username": u, "password": p})
    plan = backup.plan_backup(tmp_path / ".dedup-backups", scans, analysis)
    backup.execute_backup(plan)
    path = backup.write_operation_json(plan)
    assert path.is_file()
    data = json.loads(path.read_text())
    assert data["phase"] == "backup_complete"
    assert data["total_files"] == 2
    assert data["backup_verified"] is True
    assert sorted(r["name"] for r in data["roots"]) == ["password", "username"]


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------

def test_backup_preserves_mode(tmp_path):
    u = tmp_path / "u"
    u.mkdir()
    f = u / "a.txt"
    f.write_bytes(b"x\nx\n")
    f.chmod(0o640)
    try:
        scans, analysis = _scan_and_analyze(tmp_path, {"u": u})
        plan = backup.plan_backup(tmp_path / ".dedup-backups", scans, analysis)
        backup.execute_backup(plan)
        mode = (plan.root / "u" / "a.txt").stat().st_mode & 0o777
        assert mode == 0o640
    finally:
        f.chmod(0o644)


def test_backup_dir_mode_is_private(tmp_path):
    u = tmp_path / "u"
    _mk(u, {"a.txt": b"x\nx\n"})
    scans, analysis = _scan_and_analyze(tmp_path, {"u": u})
    plan = backup.plan_backup(tmp_path / ".dedup-backups", scans, analysis)
    backup.execute_backup(plan)
    mode = plan.root.stat().st_mode & 0o777
    assert mode == 0o700


# ---------------------------------------------------------------------------
# safety.resolve_backup_home
# ---------------------------------------------------------------------------

def test_resolve_backup_home_default(tmp_path):
    a = tmp_path / "u"
    a.mkdir()
    home = safety.resolve_backup_home(None, [("u", a)])
    assert home.name == ".dedup-backups"
    assert home.parent == tmp_path


def test_resolve_backup_home_override(tmp_path):
    a = tmp_path / "u"
    b = tmp_path / "backups"
    a.mkdir()
    home = safety.resolve_backup_home(str(b), [("u", a)])
    assert home == b.resolve()


def test_resolve_backup_home_rejects_inside_root(tmp_path):
    a = tmp_path / "u"
    a.mkdir()
    inside = a / ".dedup-backups"
    try:
        safety.resolve_backup_home(str(inside), [("u", a)])
    except safety.SafetyError as e:
        assert "lies inside root" in str(e)
        return
    raise AssertionError("expected SafetyError")


def test_resolve_backup_home_rejects_equals_root(tmp_path):
    a = tmp_path / "u"
    a.mkdir()
    try:
        safety.resolve_backup_home(str(a), [("u", a)])
    except safety.SafetyError:
        return
    raise AssertionError("expected SafetyError")


def test_resolve_backup_home_rejects_root_inside_home(tmp_path):
    home = tmp_path / "backups"
    r = home / "u"
    r.mkdir(parents=True)
    try:
        safety.resolve_backup_home(str(home), [("u", r)])
    except safety.SafetyError as e:
        assert "lies inside backup home" in str(e)
        return
    raise AssertionError("expected SafetyError")


# ---------------------------------------------------------------------------
# safety.check_backup_disk_space
# ---------------------------------------------------------------------------

def test_check_backup_disk_space_ok(tmp_path):
    a = tmp_path / "u"
    a.mkdir()
    home = tmp_path / ".dedup-backups"
    home.mkdir()
    safety.check_backup_disk_space(home, [("u", a)], 1024, 1024)


def test_check_backup_disk_space_insufficient(tmp_path):
    a = tmp_path / "u"
    a.mkdir()
    home = tmp_path / ".dedup-backups"
    home.mkdir()
    try:
        safety.check_backup_disk_space(home, [("u", a)], 10**18, 10**18)
    except safety.SafetyError as e:
        assert "Insufficient" in str(e)
        return
    raise AssertionError("expected SafetyError")


if __name__ == "__main__":
    import sys
    import tempfile
    from pathlib import Path as P

    mod = sys.modules[__name__]
    failed = 0
    for name in sorted(dir(mod)):
        if not name.startswith("test_"):
            continue
        fn = getattr(mod, name)
        with tempfile.TemporaryDirectory() as td:
            try:
                fn(P(td))
            except Exception as e:
                print(f"FAIL {name}: {e!r}")
                failed += 1
            else:
                print(f"ok   {name}")
    sys.exit(1 if failed else 0)