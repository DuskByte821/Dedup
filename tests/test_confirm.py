"""Tests for dedup.confirm and the confirmation gate in dedup.py."""

import io
import sys

from dedup import confirm
from dedup.dedup_gate_testutil import run_gate  # see helper below


def _fixed_input(value):
    def fn():
        return value
    return fn


def _raise(exc):
    def fn():
        raise exc
    return fn


def _capture():
    buf = io.StringIO()
    return buf, buf.write


# ---------------------------------------------------------------------------
# confirm.confirm
# ---------------------------------------------------------------------------

def _run_confirm(response_value=None, *, exc=None):
    buf, out = _capture()
    input_fn = _raise(exc) if exc is not None else _fixed_input(response_value)
    try:
        result = confirm.confirm("> ", input_fn=input_fn, output_fn=out)
    except BaseException as e:
        return None, out.getvalue(), e
    return result, out.getvalue(), None


def test_accepts_lowercase_y():
    result, _, exc = _run_confirm("y")
    assert exc is None and result is True


def test_accepts_uppercase_y():
    result, _, exc = _run_confirm("Y")
    assert exc is None and result is True


def test_accepts_yes_lowercase():
    result, _, exc = _run_confirm("yes")
    assert exc is None and result is True


def test_accepts_yes_uppercase():
    result, _, exc = _run_confirm("YES")
    assert exc is None and result is True


def test_accepts_mixed_case_yes():
    result, _, exc = _run_confirm("YeS")
    assert exc is None and result is True


def test_accepts_yes_with_surrounding_whitespace():
    result, _, exc = _run_confirm("  yes  ")
    assert exc is None and result is True


def test_rejects_empty_string():
    result, _, exc = _run_confirm("")
    assert exc is None and result is False


def test_rejects_whitespace_only():
    result, _, exc = _run_confirm("   ")
    assert exc is None and result is False


def test_rejects_n():
    result, _, exc = _run_confirm("n")
    assert exc is None and result is False


def test_rejects_no():
    result, _, exc = _run_confirm("no")
    assert exc is None and result is False


def test_rejects_ye():
    result, _, exc = _run_confirm("ye")
    assert exc is None and result is False


def test_rejects_yes_with_trailing_junk():
    result, _, exc = _run_confirm("yes!")
    assert exc is None and result is False


def test_rejects_yes_please():
    result, _, exc = _run_confirm("yes please")
    assert exc is None and result is False


def test_rejects_numeric_one():
    result, _, exc = _run_confirm("1")
    assert exc is None and result is False


def test_rejects_true():
    result, _, exc = _run_confirm("true")
    assert exc is None and result is False


def test_eof_cancels():
    result, _, exc = _run_confirm(exc=EOFError())
    assert exc is None and result is False


def test_keyboard_interrupt_propagates():
    result, _, exc = _run_confirm(exc=KeyboardInterrupt())
    assert result is None
    assert isinstance(exc, KeyboardInterrupt)


def test_prompt_written_to_output_fn():
    _, text, _ = _run_confirm("n")
    assert ">" in text


def test_eof_leaves_a_newline():
    _, text, _ = _run_confirm(exc=EOFError())
    assert text.endswith("\n")


def test_interrupt_leaves_a_newline():
    _, text, _ = _run_confirm(exc=KeyboardInterrupt())
    assert text.endswith("\n")


if __name__ == "__main__":
    import sys as _sys
    mod = _sys.modules[__name__]
    failed = 0
    for name in sorted(dir(mod)):
        if not name.startswith("test_"):
            continue
        fn = getattr(mod, name)
        try:
            fn()
        except Exception as e:
            print(f"FAIL {name}: {e!r}")
            failed += 1
        else:
            print(f"ok   {name}")
    _sys.exit(1 if failed else 0)