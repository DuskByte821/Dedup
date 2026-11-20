"""Tests for dedup.lineio. Runnable standalone or under pytest."""

import io

from dedup.lineio import LineTooLong, iter_lines


def _collect(data, chunk_size=1 << 20, max_line=1 << 20):
    # iter_lines reads in 1 MiB chunks; small inputs never hit the boundary.
    # To exercise boundaries, we shrink the read size by wrapping the stream.
    return list(iter_lines(_ShrunkReader(io.BytesIO(data), chunk_size), max_line))


class _ShrunkReader:
    """Wrap a binary stream so read(n) never returns more than chunk_size bytes."""

    def __init__(self, inner, chunk_size):
        self.inner = inner
        self.chunk_size = chunk_size

    def read(self, n):
        return self.inner.read(min(n, self.chunk_size))


def test_lf_only():
    assert _collect(b"abc\ndef\n") == [b"abc", b"def"]


def test_crlf_only():
    assert _collect(b"abc\r\ndef\r\n") == [b"abc", b"def"]


def test_cr_only():
    assert _collect(b"abc\rdef\r") == [b"abc", b"def"]


def test_mixed_terminators():
    assert _collect(b"a\nb\r\nc\rd\n") == [b"a", b"b", b"c", b"d"]


def test_no_final_newline():
    assert _collect(b"abc\ndef") == [b"abc", b"def"]


def test_trailing_cr_at_eof_is_terminator():
    assert _collect(b"abc\r") == [b"abc"]


def test_empty_file():
    assert _collect(b"") == []


def test_only_newlines():
    assert _collect(b"\n\n\n") == [b"", b"", b""]


def test_empty_line_between_content():
    assert _collect(b"a\n\nb\n") == [b"a", b"", b"b"]


def test_crlf_split_across_chunks():
    # 1-byte reads force the boundary between CR and LF.
    assert _collect(b"abc\r\ndef", chunk_size=1) == [b"abc", b"def"]


def test_lone_cr_split_across_chunks():
    assert _collect(b"abc\rdef", chunk_size=1) == [b"abc", b"def"]


def test_whitespace_preserved_exactly():
    assert _collect(b"  abc  \n\tabc\t\nabc \n abc\n") == [
        b"  abc  ", b"\tabc\t", b"abc ", b" abc"]


def test_tab_and_space_are_not_terminators():
    assert _collect(b"a\tb\na b\n") == [b"a\tb", b"a b"]


def test_unicode_bytes_preserved():
    data = "café\nnaïve\n☕\n".encode("utf-8")
    assert _collect(data) == [
        "café".encode("utf-8"),
        "naïve".encode("utf-8"),
        "☕".encode("utf-8"),
    ]


def test_long_line_at_limit():
    assert _collect(b"a" * 10 + b"\n", max_line=10) == [b"a" * 10]


def test_long_line_over_limit():
    try:
        _collect(b"a" * 11 + b"\n", max_line=10)
    except LineTooLong:
        return
    raise AssertionError("Expected LineTooLong")


def test_long_line_without_terminator_over_limit():
    try:
        _collect(b"a" * 11, max_line=10)
    except LineTooLong:
        return
    raise AssertionError("Expected LineTooLong")


def test_invalid_max_line():
    try:
        _collect(b"x", max_line=0)
    except ValueError:
        return
    raise AssertionError("Expected ValueError")


if __name__ == "__main__":
    import sys
    mod = sys.modules[__name__]
    failed = 0
    for name in sorted(dir(mod)):
        if name.startswith("test_"):
            try:
                getattr(mod, name)()
            except Exception as e:
                print(f"FAIL {name}: {e!r}")
                failed += 1
            else:
                print(f"ok   {name}")
    sys.exit(1 if failed else 0)