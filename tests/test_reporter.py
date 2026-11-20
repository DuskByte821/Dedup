"""Smoke tests for dedup.reporter. Renders a report and checks key lines."""

import io
from contextlib import redirect_stdout

from dedup import scanner, analyzer, reporter
from dedup.index import Index


def _mk(root, files):
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)


def _render(tmp_path, roots, config=None, dry_run=True):
    idx = Index(tmp_path / "idx.db")
    scans = [scanner.scan(name, path) for name, path in roots.items()]
    result = analyzer.analyze_roots(
        scans, idx, empty_mode=(config or {}).get("empty_mode", "keep"))
    idx.close()

    buf = io.StringIO()
    with redirect_stdout(buf):
        reporter.print_report(
            scans, result,
            config or {
                "extensions": {".txt"},
                "empty_mode": "keep",
                "follow_symlinks": False,
                "max_line_bytes": 16 * 1024 * 1024,
            },
            dry_run=dry_run,
        )
    return buf.getvalue()


def test_report_contains_per_root_sections(tmp_path):
    u = tmp_path / "u"
    p = tmp_path / "p"
    _mk(u, {"a.txt": b"x\nx\n"})
    _mk(p, {"b.txt": b"y\ny\n"})
    out = _render(tmp_path, {"username": u, "password": p})
    assert "ROOT: username" in out
    assert "ROOT: password" in out
    assert "TOTAL (informational only)" in out


def test_report_contains_cross_root_note(tmp_path):
    u = tmp_path / "u"
    p = tmp_path / "p"
    _mk(u, {"a.txt": b"x\n"})
    _mk(p, {"b.txt": b"x\n"})
    out = _render(tmp_path, {"username": u, "password": p})
    assert "NOT a duplicate" in out


def test_report_marks_dry_run(tmp_path):
    u = tmp_path / "u"
    _mk(u, {"a.txt": b"x\nx\n"})
    out = _render(tmp_path, {"u": u}, dry_run=True)
    assert "[DRY RUN]" in out


def test_report_marks_non_dry_run(tmp_path):
    u = tmp_path / "u"
    _mk(u, {"a.txt": b"x\nx\n"})
    out = _render(tmp_path, {"u": u}, dry_run=False)
    assert "No files have been modified yet." in out
    assert "[DRY RUN]" not in out


def test_report_lists_skipped_extensions(tmp_path):
    u = tmp_path / "u"
    _mk(u, {"a.txt": b"x\n", "README.md": b"y\n"})
    out = _render(tmp_path, {"u": u})
    assert "skipped_extension" in out
    assert "README.md" in out


def test_report_handles_no_issues(tmp_path):
    u = tmp_path / "u"
    _mk(u, {"a.txt": b"x\n"})
    out = _render(tmp_path, {"u": u})
    assert "Files with issues" in out
    assert "(none)" in out


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