"""Tests for dedup.manifest."""

import json

from dedup import backup, scanner, analyzer, processor, verifier, manifest
from dedup.analyzer import analyze_roots
from dedup.index import Index


def _mk(root, files):
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)


def _full_run(tmp_path, roots, empty_mode="keep"):
    idx = Index(tmp_path / "idx.db")
    scans = [scanner.scan(name, path) for name, path in roots.items()]
    analysis = analyze_roots(scans, idx, empty_mode=empty_mode)
    plan = backup.plan_backup(tmp_path / ".dedup-backups", scans, analysis)
    backup.execute_backup(plan)
    backup.verify_backup(plan)
    backup.write_operation_json(plan)
    modified = processor.process_roots(scans, analysis, idx,
                                       empty_mode=empty_mode)
    vreport = verifier.verify_operation(
        scans, analysis, idx, modified, plan,
        empty_mode=empty_mode, max_line_bytes=1 << 20)
    config = {
        "empty_mode": empty_mode,
        "extensions": {".txt", ".lst"},
        "follow_symlinks": False,
        "max_line_bytes": 1 << 20,
    }
    payload = manifest.build_payload(
        scope_roots=[(n, p) for n, p in roots.items()],
        config=config,
        scan_results=scans,
        analysis=analysis,
        modified=modified,
        plan=plan,
        verification_report=vreport,
        warnings=manifest.collect_warnings(scans),
    )
    path = manifest.write_manifest(plan, payload)
    return idx, plan, payload, path


# ---------------------------------------------------------------------------

def test_manifest_written_to_backup_root(tmp_path):
    u = tmp_path / "u"
    _mk(u, {"a.txt": b"x\nx\n"})
    idx, plan, payload, path = _full_run(tmp_path, {"u": u})
    idx.close()
    assert path == plan.root / "operation.complete.json"
    assert path.is_file()


def test_manifest_phase_and_schema(tmp_path):
    u = tmp_path / "u"
    _mk(u, {"a.txt": b"x\nx\n"})
    idx, plan, payload, path = _full_run(tmp_path, {"u": u})
    idx.close()
    data = json.loads(path.read_text())
    assert data["phase"] == "operation_complete"
    assert data["schema_version"] == manifest.SCHEMA_VERSION
    assert data["verification_status"] == "passed"
    assert data["backup_location"] == str(plan.root)


def test_manifest_per_root_stats(tmp_path):
    u = tmp_path / "u"
    _mk(u, {"a.txt": b"apple\napple\nbanana\n"})
    idx, plan, payload, path = _full_run(tmp_path, {"username": u})
    idx.close()
    data = json.loads(path.read_text())
    root = data["roots"][0]
    assert root["name"] == "username"
    assert root["entries_before"] == 3
    assert root["entries_after"] == 2
    assert root["internal_duplicates"] == 1
    assert root["files_modified"] == 1


def test_manifest_totals_are_sums(tmp_path):
    u = tmp_path / "u"
    p = tmp_path / "p"
    _mk(u, {"a.txt": b"x\nx\n"})
    _mk(p, {"b.txt": b"y\ny\n"})
    idx, plan, payload, path = _full_run(
        tmp_path, {"username": u, "password": p})
    idx.close()
    data = json.loads(path.read_text())
    assert data["totals"]["roots"] == 2
    assert data["totals"]["files_modified"] == 2
    assert data["totals"]["internal_duplicates"] == 2
    assert data["totals"]["duplicates_removed"] == 2


def test_manifest_carries_cross_root_note(tmp_path):
    u = tmp_path / "u"
    p = tmp_path / "p"
    _mk(u, {"a.txt": b"x\nx\n"})
    _mk(p, {"b.txt": b"y\ny\n"})
    idx, plan, payload, path = _full_run(
        tmp_path, {"username": u, "password": p})
    idx.close()
    data = json.loads(path.read_text())
    assert "NOT a duplicate" in data["note"]


def test_manifest_config_block(tmp_path):
    u = tmp_path / "u"
    _mk(u, {"a.txt": b"x\nx\n"})
    idx, plan, payload, path = _full_run(tmp_path, {"u": u},
                                         empty_mode="drop")
    idx.close()
    data = json.loads(path.read_text())
    assert data["config"]["empty_mode"] == "drop"
    assert data["config"]["extensions"] == [".lst", ".txt"]
    assert data["config"]["follow_symlinks"] is False
    assert data["config"]["max_line_bytes"] == (1 << 20)


def test_collect_warnings_for_unreadable(tmp_path):
    import os
    if os.geteuid() == 0:
        return
    u = tmp_path / "u"
    u.mkdir()
    f = u / "locked.txt"
    f.write_bytes(b"x\n")
    f.chmod(0)
    try:
        sr = scanner.scan("u", u)
        warns = manifest.collect_warnings([sr])
        assert any("unreadable" in w for w in warns)
    finally:
        f.chmod(0o644)


def test_collect_warnings_for_symlinks(tmp_path):
    u = tmp_path / "u"
    u.mkdir()
    (u / "real.txt").write_bytes(b"x\n")
    (u / "link.txt").symlink_to(u / "real.txt")
    sr = scanner.scan("u", u)
    warns = manifest.collect_warnings([sr])
    assert any("symlink" in w for w in warns)


def test_manifest_overwrite_on_second_write(tmp_path):
    u = tmp_path / "u"
    _mk(u, {"a.txt": b"x\nx\n"})
    idx, plan, payload, path = _full_run(tmp_path, {"u": u})
    idx.close()
    # Change a value and rewrite; the file is replaced in place.
    payload["config"]["empty_mode"] = "keep"
    payload["warnings"] = ["rewritten"]
    path2 = manifest.write_manifest(plan, payload)
    assert path2 == path
    data = json.loads(path.read_text())
    assert data["warnings"] == ["rewritten"]


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