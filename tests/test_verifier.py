"""Tests for dedup.verifier and the external verify.py."""

import importlib.util
import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

from dedup import backup, scanner, analyzer, processor, verifier
from dedup.analyzer import analyze_roots
from dedup.index import Index


HERE = Path(__file__).resolve().parent
VERIFY_PATH = HERE.parent / "verify.py"


def _load_verify():
    spec = importlib.util.spec_from_file_location("verify_ext", VERIFY_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


verify_ext = _load_verify()


def _mk(root, files):
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)


def _run_full(tmp_path, roots, empty_mode="keep"):
    """Return (scan_results, analysis, index, modified, plan)."""
    idx = Index(tmp_path / "idx.db")
    scans = [scanner.scan(name, path) for name, path in roots.items()]
    analysis = analyze_roots(scans, idx, empty_mode=empty_mode)
    home = tmp_path / ".dedup-backups"
    plan = backup.plan_backup(home, scans, analysis)
    backup.execute_backup(plan)
    backup.verify_backup(plan)
    backup.write_operation_json(plan)
    modified = processor.process_roots(
        scans, analysis, idx, empty_mode=empty_mode)
    return scans, analysis, idx, modified, plan


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_verify_passes_after_successful_run(tmp_path):
    root = tmp_path / "u"
    _mk(root, {"a.txt": b"x\nx\ny\n", "b.txt": b"y\nz\n"})
    scans, analysis, idx, modified, plan = _run_full(tmp_path, {"u": root})
    report = verifier.verify_operation(
        scans, analysis, idx, modified, plan,
        empty_mode="keep", max_line_bytes=1 << 20,
    )
    idx.close()
    assert report.passed, report.failures


def test_verify_passes_multi_root(tmp_path):
    u = tmp_path / "u"
    p = tmp_path / "p"
    _mk(u, {"a.txt": b"admin\nalice\nadmin\n"})
    _mk(p, {"b.txt": b"admin\nalice\nadmin\n"})
    scans, analysis, idx, modified, plan = _run_full(
        tmp_path, {"username": u, "password": p})
    report = verifier.verify_operation(
        scans, analysis, idx, modified, plan,
        empty_mode="keep", max_line_bytes=1 << 20,
    )
    idx.close()
    assert report.passed, report.failures


# ---------------------------------------------------------------------------
# Detects tampering post-modification
# ---------------------------------------------------------------------------

def test_detects_injected_entry(tmp_path):
    root = tmp_path / "u"
    _mk(root, {"a.txt": b"x\nx\n"})
    scans, analysis, idx, modified, plan = _run_full(tmp_path, {"u": root})

    # Inject a brand new entry into the modified tree.
    (root / "a.txt").write_bytes(b"x\ninjected\n")

    report = verifier.verify_operation(
        scans, analysis, idx, modified, plan,
        empty_mode="keep", max_line_bytes=1 << 20,
    )
    idx.close()
    assert not report.passed
    assert any("not in the original" in f for f in report.failures)


def test_detects_lost_entry(tmp_path):
    root = tmp_path / "u"
    _mk(root, {"a.txt": b"x\nx\ny\n"})
    scans, analysis, idx, modified, plan = _run_full(tmp_path, {"u": root})

    # Delete an entry that should have survived.
    (root / "a.txt").write_bytes(b"x\n")

    report = verifier.verify_operation(
        scans, analysis, idx, modified, plan,
        empty_mode="keep", max_line_bytes=1 << 20,
    )
    idx.close()
    assert not report.passed


def test_detects_duplicate_left_behind(tmp_path):
    root = tmp_path / "u"
    _mk(root, {"a.txt": b"x\nx\n"})
    scans, analysis, idx, modified, plan = _run_full(tmp_path, {"u": root})

    # Simulate a processor bug: duplicate still present.
    (root / "a.txt").write_bytes(b"x\nx\n")

    report = verifier.verify_operation(
        scans, analysis, idx, modified, plan,
        empty_mode="keep", max_line_bytes=1 << 20,
    )
    idx.close()
    assert not report.passed
    assert any("duplicates remain" in f for f in report.failures)


def test_detects_backup_tampering(tmp_path):
    root = tmp_path / "u"
    _mk(root, {"a.txt": b"x\nx\n"})
    scans, analysis, idx, modified, plan = _run_full(tmp_path, {"u": root})

    # Corrupt the backup.
    (plan.root / "u" / "a.txt").write_bytes(b"corrupted\n")

    report = verifier.verify_operation(
        scans, analysis, idx, modified, plan,
        empty_mode="keep", max_line_bytes=1 << 20,
    )
    idx.close()
    assert not report.passed
    assert any("Backup hash mismatch" in f for f in report.failures)


def test_detects_missing_operation_json(tmp_path):
    root = tmp_path / "u"
    _mk(root, {"a.txt": b"x\nx\n"})
    scans, analysis, idx, modified, plan = _run_full(tmp_path, {"u": root})

    (plan.root / "operation.json").unlink()

    report = verifier.verify_operation(
        scans, analysis, idx, modified, plan,
        empty_mode="keep", max_line_bytes=1 << 20,
    )
    idx.close()
    assert not report.passed
    assert any("operation.json missing" in f for f in report.failures)


def test_detects_touched_untouched_file(tmp_path):
    root = tmp_path / "u"
    _mk(root, {"a.txt": b"x\nx\n", "b.txt": b"unchanged\n"})
    scans, analysis, idx, modified, plan = _run_full(tmp_path, {"u": root})

    # b.txt was not in modified list. Touch it.
    import time
    time.sleep(0.01)
    (root / "b.txt").write_bytes(b"unchanged\n")

    report = verifier.verify_operation(
        scans, analysis, idx, modified, plan,
        empty_mode="keep", max_line_bytes=1 << 20,
    )
    idx.close()
    assert not report.passed
    assert any("untouched file" in f for f in report.failures)


def test_detects_cross_root_leak(tmp_path):
    u = tmp_path / "u"
    p = tmp_path / "p"
    _mk(u, {"a.txt": b"admin\n"})
    _mk(p, {"b.txt": b"admin\n"})
    scans, analysis, idx, modified, plan = _run_full(
        tmp_path, {"username": u, "password": p})

    # Simulate a broken processor that deduplicated across roots.
    (p / "b.txt").write_bytes(b"")

    report = verifier.verify_operation(
        scans, analysis, idx, modified, plan,
        empty_mode="keep", max_line_bytes=1 << 20,
    )
    idx.close()
    assert not report.passed


# ---------------------------------------------------------------------------
# External verify.py
# ---------------------------------------------------------------------------

def test_external_verify_passes_on_clean_run(tmp_path):
    original = tmp_path / "Secn"
    _mk(original, {
        "a.txt": b"apple\nbanana\napple\ncherry\n",
        "nested/b.txt": b"banana\ndate\n",
    })

    # Simulate: copy original to a working tree, run dedup, keep both.
    import shutil
    processed = tmp_path / "Secn_proc"
    shutil.copytree(original, processed)

    idx = Index(tmp_path / "idx.db")
    sr = scanner.scan("Secn", processed)
    analysis = analyze_roots([sr], idx)
    home = tmp_path / ".dedup-backups"
    plan = backup.plan_backup(home, [sr], analysis)
    backup.execute_backup(plan)
    backup.verify_backup(plan)
    backup.write_operation_json(plan)
    processor.process_roots([sr], analysis, idx)
    idx.close()

    buf = io.StringIO()
    with redirect_stdout(buf):
        code = verify_ext.main([
            "--original", str(original),
            "--backup", str(plan.root),
            "--processed", str(processed),
        ])
    assert code == 0, buf.getvalue()
    assert "VERIFICATION PASSED" in buf.getvalue()


def test_external_verify_detects_processed_tamper(tmp_path):
    original = tmp_path / "Secn"
    _mk(original, {"a.txt": b"one\none\ntwo\n"})

    import shutil
    processed = tmp_path / "Secn_proc"
    shutil.copytree(original, processed)

    idx = Index(tmp_path / "idx.db")
    sr = scanner.scan("Secn", processed)
    analysis = analyze_roots([sr], idx)
    plan = backup.plan_backup(tmp_path / ".dedup-backups", [sr], analysis)
    backup.execute_backup(plan)
    backup.verify_backup(plan)
    backup.write_operation_json(plan)
    processor.process_roots([sr], analysis, idx)
    idx.close()

    # Tamper with processed.
    (processed / "a.txt").write_bytes(b"one\ntwo\nINJECTED\n")

    buf = io.StringIO()
    with redirect_stdout(buf):
        code = verify_ext.main([
            "--original", str(original),
            "--backup", str(plan.root),
            "--processed", str(processed),
        ])
    assert code == 2
    assert "VERIFICATION FAILED" in buf.getvalue()


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