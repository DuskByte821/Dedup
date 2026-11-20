"""End-to-end tests for the CLI confirmation gate.

Loads dedup.py (the CLI script) as a module via importlib so the tests do
not need to rename it, and so ``from dedup import ...`` still resolves to
the package.
"""

import importlib.util
import io
import sys
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path


HERE = Path(__file__).resolve().parent
CLI_PATH = HERE.parent / "dedup.py"


def _load_cli():
    spec = importlib.util.spec_from_file_location("dedup_cli", CLI_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cli = _load_cli()


def _mk(root, files):
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)


def _fake_input(value):
    def fn():
        return value
    return fn


def _run_cli(argv, input_value=None, input_exc=None):
    buf_out = io.StringIO()
    buf_err = io.StringIO()

    def input_fn():
        if input_exc is not None:
            raise input_exc
        return input_value

    code = None
    with redirect_stdout(buf_out), redirect_stderr(buf_err):
        try:
            code = cli.run(argv, confirm_input_fn=input_fn)
        except KeyboardInterrupt:
            code = cli.EXIT_INTERRUPTED
    return code, buf_out.getvalue(), buf_err.getvalue()


# ---------------------------------------------------------------------------

def test_cancel_with_n(tmp_path):
    root = tmp_path / "u"
    _mk(root, {"a.txt": b"x\nx\n"})
    before = (root / "a.txt").read_bytes()
    code, out, err = _run_cli([str(root)], input_value="n")
    assert code == cli.EXIT_OK
    assert "Cancelled" in err
    assert (root / "a.txt").read_bytes() == before


def test_cancel_with_empty(tmp_path):
    root = tmp_path / "u"
    _mk(root, {"a.txt": b"x\nx\n"})
    before = (root / "a.txt").read_bytes()
    code, out, err = _run_cli([str(root)], input_value="")
    assert code == cli.EXIT_OK
    assert "Cancelled" in err
    assert (root / "a.txt").read_bytes() == before


def test_cancel_with_yes_prefixed_junk(tmp_path):
    root = tmp_path / "u"
    _mk(root, {"a.txt": b"x\nx\n"})
    before = (root / "a.txt").read_bytes()
    code, out, err = _run_cli([str(root)], input_value="yep")
    assert code == cli.EXIT_OK
    assert "Cancelled" in err
    assert (root / "a.txt").read_bytes() == before


def test_confirm_yes_reports_not_implemented(tmp_path):
    root = tmp_path / "u"
    _mk(root, {"a.txt": b"x\nx\n"})
    before = (root / "a.txt").read_bytes()
    code, out, err = _run_cli([str(root)], input_value="yes")
    assert code == cli.EXIT_NOT_IMPLEMENTED
    assert "Confirmed" in err
    assert "not yet implemented" in err
    # Confirmation must never modify anything at this phase.
    assert (root / "a.txt").read_bytes() == before


def test_confirm_uppercase_yes(tmp_path):
    root = tmp_path / "u"
    _mk(root, {"a.txt": b"x\nx\n"})
    code, _, err = _run_cli([str(root)], input_value="YES")
    assert code == cli.EXIT_NOT_IMPLEMENTED


def test_eof_at_prompt_cancels(tmp_path):
    root = tmp_path / "u"
    _mk(root, {"a.txt": b"x\nx\n"})
    before = (root / "a.txt").read_bytes()
    code, _, err = _run_cli([str(root)], input_exc=EOFError())
    assert code == cli.EXIT_OK
    assert "Cancelled" in err
    assert (root / "a.txt").read_bytes() == before


def test_keyboard_interrupt_at_prompt_is_interrupted(tmp_path):
    root = tmp_path / "u"
    _mk(root, {"a.txt": b"x\nx\n"})
    before = (root / "a.txt").read_bytes()
    code, _, err = _run_cli([str(root)], input_exc=KeyboardInterrupt())
    assert code == cli.EXIT_INTERRUPTED
    assert (root / "a.txt").read_bytes() == before


def test_dry_run_never_prompts(tmp_path):
    root = tmp_path / "u"
    _mk(root, {"a.txt": b"x\nx\n"})

    def blow_up():
        raise AssertionError("dry-run must not prompt")

    buf_out, buf_err = io.StringIO(), io.StringIO()
    with redirect_stdout(buf_out), redirect_stderr(buf_err):
        code = cli.run([str(root), "--dry-run"], confirm_input_fn=blow_up)
    assert code == cli.EXIT_OK
    assert "[DRY RUN]" in buf_out.getvalue()


def test_no_duplicates_never_prompts(tmp_path):
    root = tmp_path / "u"
    _mk(root, {"a.txt": b"a\nb\nc\n"})

    def blow_up():
        raise AssertionError("nothing to do must not prompt")

    buf_err = io.StringIO()
    with redirect_stdout(io.StringIO()), redirect_stderr(buf_err):
        code = cli.run([str(root)], confirm_input_fn=blow_up)
    assert code == cli.EXIT_OK
    assert "No duplicates found" in buf_err.getvalue()


def test_multi_root_warning_lists_both_roots(tmp_path):
    u = tmp_path / "u"
    p = tmp_path / "p"
    _mk(u, {"a.txt": b"x\nx\n"})
    _mk(p, {"b.txt": b"y\ny\n"})
    code, _, err = _run_cli(
        ["--root", f"username={u}", "--root", f"password={p}"],
        input_value="n",
    )
    assert code == cli.EXIT_OK
    assert "username: 1" in err
    assert "password: 1" in err


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