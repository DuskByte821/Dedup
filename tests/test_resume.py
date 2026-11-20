"""Tests for dedup.resume and the CLI --resume flow."""

import importlib.util
import io
import json
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from dedup import backup, scanner, analyzer, processor, verifier, resume
from dedup.analyzer import analyze_roots
from dedup.index import Index


HERE = Path(__file__).resolve().parent
CLI_PATH = HERE.parent / "dedup.py"


def _load_cli():
    spec = importlib.util.spec_from_file_location("dedup_cli_resume", CLI_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


cli = _load_cli()


CONFIG = {
    "empty_mode": "keep",
    "extensions": sorted({".txt", ".lst", ".wordlist",
                          ".words", ".dict", ".dic"}),
    "follow_symlinks": False,
    "max_line_bytes": 16 * 1024 * 1024,
}


def _mk(root, files):
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)


def _make_backup(tmp_path, root, config=None):
    """Create a real, verified backup without running modification."""
    idx = Index(tmp_path / "idx.db")
    scans = [scanner.scan("u", root)]
    analysis = analyze_roots(scans, idx)
    plan = backup.plan_backup(
        tmp_path / ".dedup-backups", scans, analysis,
        config=config or CONFIG)
    backup.execute_backup(plan)
    backup.verify_backup(plan)
    backup.write_operation_json(plan)
    idx.close()
    return plan, analysis


def _run_cli(argv, input_value="yes", input_exc=None):
    buf_out, buf_err = io.StringIO(), io.StringIO()

    def input_fn():
        if input_exc is not None:
            raise input_exc
        return input_value

    with redirect_stdout(buf_out), redirect_stderr(buf_err):
        try:
            code = cli.run(argv, confirm_input_fn=input_fn, stderr=buf_err)
        except KeyboardInterrupt:
            code = cli.EXIT_INTERRUPTED
    return code, buf_out.getvalue(), buf_err.getvalue()


# ---------------------------------------------------------------------------
# resume.find_incomplete_backups
# ---------------------------------------------------------------------------

def test_find_returns_nothing_when_empty(tmp_path):
    assert resume.find_incomplete_backups(tmp_path / "nope") == []


def test_find_returns_nothing_when_complete(tmp_path):
    root = tmp_path / "u"
    _mk(root, {"a.txt": b"x\nx\n"})
    plan, _ = _make_backup(tmp_path, root)
    (plan.root / "operation.complete.json").write_text("{}")
    assert resume.find_incomplete_backups(tmp_path / ".dedup-backups") == []


def test_find_returns_incomplete(tmp_path):
    root = tmp_path / "u"
    _mk(root, {"a.txt": b"x\nx\n"})
    plan, _ = _make_backup(tmp_path, root)
    found = resume.find_incomplete_backups(tmp_path / ".dedup-backups")
    assert len(found) == 1
    assert found[0].path == plan.root


def test_find_sorts_newest_first(tmp_path):
    root = tmp_path / "u"
    _mk(root, {"a.txt": b"x\nx\n"})
    plan1, _ = _make_backup(tmp_path, root)
    # Rename plan1 to an older-looking name so ordering is deterministic.
    old = tmp_path / ".dedup-backups" / "2000-01-01_000000"
    plan1.root.rename(old)
    plan2, _ = _make_backup(tmp_path, root)
    found = resume.find_incomplete_backups(tmp_path / ".dedup-backups")
    assert len(found) == 2
    assert found[0].path == plan2.root


# ---------------------------------------------------------------------------
# resume.select_backup
# ---------------------------------------------------------------------------

def test_select_fails_when_none(tmp_path):
    root = tmp_path / "u"
    root.mkdir()
    try:
        resume.select_backup(tmp_path / ".dedup-backups", None,
                             [("u", root)])
    except resume.ResumeError as e:
        assert "No incomplete backup" in str(e)
        return
    raise AssertionError("expected ResumeError")


def test_select_fails_when_multiple(tmp_path):
    root = tmp_path / "u"
    _mk(root, {"a.txt": b"x\nx\n"})
    _make_backup(tmp_path, root)
    _make_backup(tmp_path, root)
    try:
        resume.select_backup(tmp_path / ".dedup-backups", None,
                             [("u", root)])
    except resume.ResumeError as e:
        assert "Multiple incomplete" in str(e)
        return
    raise AssertionError("expected ResumeError")


def test_select_explicit_path(tmp_path):
    root = tmp_path / "u"
    _mk(root, {"a.txt": b"x\nx\n"})
    plan, _ = _make_backup(tmp_path, root)
    inc = resume.select_backup(
        tmp_path / ".dedup-backups", str(plan.root), [("u", root)])
    assert inc.path == plan.root


def test_select_fails_on_complete(tmp_path):
    root = tmp_path / "u"
    _mk(root, {"a.txt": b"x\nx\n"})
    plan, _ = _make_backup(tmp_path, root)
    (plan.root / "operation.complete.json").write_text("{}")
    try:
        resume.select_backup(
            tmp_path / ".dedup-backups", str(plan.root), [("u", root)])
    except resume.ResumeError as e:
        assert "already complete" in str(e)
        return
    raise AssertionError("expected ResumeError")


# ---------------------------------------------------------------------------
# resume.check_roots_match / check_config_match
# ---------------------------------------------------------------------------

def test_roots_match_ok(tmp_path):
    a = tmp_path / "a"
    a.mkdir()
    resume.check_roots_match([("u", a)], [("u", a)])


def test_roots_match_extra(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir(); b.mkdir()
    try:
        resume.check_roots_match([("u", a)], [("u", a), ("p", b)])
    except resume.ResumeError:
        return
    raise AssertionError("expected ResumeError")


def test_roots_match_path_diff(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir(); b.mkdir()
    try:
        resume.check_roots_match([("u", a)], [("u", b)])
    except resume.ResumeError:
        return
    raise AssertionError("expected ResumeError")


def test_config_match_ok():
    resume.check_config_match(CONFIG, CONFIG)


def test_config_match_empty_mode():
    other = dict(CONFIG)
    other["empty_mode"] = "drop"
    try:
        resume.check_config_match(CONFIG, other)
    except resume.ResumeError as e:
        assert "empty_mode" in str(e)
        return
    raise AssertionError("expected ResumeError")


def test_config_match_extensions():
    other = dict(CONFIG)
    other["extensions"] = [".txt"]
    try:
        resume.check_config_match(CONFIG, other)
    except resume.ResumeError:
        return
    raise AssertionError("expected ResumeError")


# ---------------------------------------------------------------------------
# resume.validate_backup_files
# ---------------------------------------------------------------------------

def test_validate_backup_ok(tmp_path):
    root = tmp_path / "u"
    _mk(root, {"a.txt": b"x\nx\n"})
    plan, _ = _make_backup(tmp_path, root)
    inc = resume.IncompleteBackup(
        path=plan.root, operation=json.loads(
            (plan.root / "operation.json").read_text()))
    resume.validate_backup_files(inc)


def test_validate_backup_detects_tamper(tmp_path):
    root = tmp_path / "u"
    _mk(root, {"a.txt": b"x\nx\n"})
    plan, _ = _make_backup(tmp_path, root)
    (plan.root / "u" / "a.txt").write_bytes(b"tampered\n")
    inc = resume.IncompleteBackup(
        path=plan.root, operation=json.loads(
            (plan.root / "operation.json").read_text()))
    try:
        resume.validate_backup_files(inc)
    except resume.ResumeError as e:
        assert "corrupted" in str(e)
        return
    raise AssertionError("expected ResumeError")


# ---------------------------------------------------------------------------
# CLI --resume end-to-end
# ---------------------------------------------------------------------------

def test_resume_no_backup_errors(tmp_path):
    root = tmp_path / "u"
    _mk(root, {"a.txt": b"x\nx\n"})
    code, out, err = _run_cli([str(root), "--resume"])
    assert code == cli.EXIT_ERROR
    assert "Cannot resume" in err


def test_resume_after_partial_modification(tmp_path):
    """Simulate an interrupted run: backup done, one file already processed."""
    root = tmp_path / "u"
    _mk(root, {"a.txt": b"x\nx\ny\n", "b.txt": b"x\nz\n"})

    # 1. Create the backup.
    plan, _ = _make_backup(tmp_path, root)

    # 2. Simulate the first file already being deduped by the previous run.
    (root / "a.txt").write_bytes(b"x\ny\n")
    # b.txt still has the duplicate.

    # 3. Resume.
    code, out, err = _run_cli([str(root), "--resume"], input_value="yes")
    assert code == 0, err
    assert "SUCCESS" in out
    assert (root / "a.txt").read_bytes() == b"x\ny\n"
    assert (root / "b.txt").read_bytes() == b"z\n"
    assert (plan.root / "operation.complete.json").is_file()


def test_resume_after_full_modification_before_manifest(tmp_path):
    """Simulate a crash between modification and manifest."""
    root = tmp_path / "u"
    _mk(root, {"a.txt": b"x\nx\ny\n", "b.txt": b"x\nz\n"})

    plan, analysis = _make_backup(tmp_path, root)

    # Run the modification manually but do NOT write the completion manifest.
    idx = Index(tmp_path / "idx2.db")
    scans = [scanner.scan("u", root)]
    cur_analysis = analyze_roots(scans, idx)
    processor.process_roots(scans, cur_analysis, idx)
    idx.close()

    # Now resume. Only verification + manifest remain.
    code, out, err = _run_cli([str(root), "--resume"], input_value="yes")
    assert code == 0, err
    assert "SUCCESS" in out
    assert (plan.root / "operation.complete.json").is_file()


def test_resume_cancelled_by_user(tmp_path):
    root = tmp_path / "u"
    _mk(root, {"a.txt": b"x\nx\ny\n", "b.txt": b"x\nz\n"})
    plan, _ = _make_backup(tmp_path, root)
    code, out, err = _run_cli([str(root), "--resume"], input_value="n")
    assert code == cli.EXIT_OK
    assert "Cancelled" in err
    assert not (plan.root / "operation.complete.json").is_file()


def test_resume_refuses_on_config_mismatch(tmp_path):
    root = tmp_path / "u"
    _mk(root, {"a.txt": b"x\nx\n"})
    # Backup was made with empty_mode=drop; resume will use keep (default).
    wrong = dict(CONFIG)
    wrong["empty_mode"] = "drop"
    _make_backup(tmp_path, root, config=wrong)

    code, out, err = _run_cli([str(root), "--resume"], input_value="yes")
    assert code == cli.EXIT_ERROR
    assert "empty_mode" in err


def test_resume_refuses_on_backup_corruption(tmp_path):
    root = tmp_path / "u"
    _mk(root, {"a.txt": b"x\nx\n"})
    plan, _ = _make_backup(tmp_path, root)
    (plan.root / "u" / "a.txt").write_bytes(b"corrupted\n")
    code, out, err = _run_cli([str(root), "--resume"], input_value="yes")
    assert code == cli.EXIT_ERROR
    assert "corrupted" in err.lower() or "validation" in err.lower()


def test_resume_rejects_dry_run_combo(tmp_path):
    root = tmp_path / "u"
    _mk(root, {"a.txt": b"x\nx\n"})
    code, out, err = _run_cli([str(root), "--resume", "--dry-run"])
    assert code == cli.EXIT_SAFETY
    assert "cannot be combined" in err


def test_resume_from_explicit_path(tmp_path):
    root = tmp_path / "u"
    _mk(root, {"a.txt": b"x\nx\ny\n", "b.txt": b"x\nz\n"})
    plan, _ = _make_backup(tmp_path, root)
    # Modify one file to simulate a partial run.
    (root / "a.txt").write_bytes(b"x\ny\n")

    code, out, err = _run_cli(
        [str(root), "--resume-from", str(plan.root)],
        input_value="yes")
    assert code == 0, err
    assert (plan.root / "operation.complete.json").is_file()


if __name__ == "__main__":
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