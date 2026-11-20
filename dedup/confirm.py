"""Interactive confirmation gate.

Only the exact strings ``y`` or ``yes`` (ASCII, case-insensitive, with
optional surrounding whitespace) proceed. Every other answer — including
empty input — cancels.

Ctrl+C propagates as ``KeyboardInterrupt`` so the caller decides how to
report the cancellation. EOF (closed stdin) is treated as a cancel.

The prompt is written to stderr by default so stdout can carry the report
independently of the interactive gate.
"""

import sys
from typing import Callable, Optional


_ACCEPT = frozenset({"y", "yes"})


def confirm(
    prompt: str,
    *,
    input_fn: Callable[[], str] = input,
    output_fn: Optional[Callable[[str], None]] = None,
) -> bool:
    """Return True only for ``y``/``yes``. Everything else returns False.

    ``input_fn`` is called with no arguments and must return a string
    (matching the builtin ``input`` contract). ``output_fn`` defaults to
    ``sys.stderr.write``; it receives the prompt text with no trailing
    newline.
    """
    out = output_fn if output_fn is not None else sys.stderr.write

    out(prompt)
    try:
        line = input_fn()
    except EOFError:
        # Terminal is gone; leave a clean line on screen and cancel.
        out("\n")
        return False
    except KeyboardInterrupt:
        # Let the caller decide how to report the interruption.
        out("\n")
        raise

    return line.strip().lower() in _ACCEPT