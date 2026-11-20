"""Byte-level line iteration over binary streams.

Contract
--------
The newline is a *file record separator*, not part of an entry.

Recognized terminators, at any position:
    b"\\r\\n"   one terminator, length 2
    b"\\n"      one terminator, length 1
    b"\\r"      one terminator, length 1

Guarantees
----------
* A yielded line's bytes never contain b"\\n" or b"\\r".
* Only the terminator is removed. No stripping, no whitespace
  normalization, no Unicode normalization.
* The final line is yielded with no terminator if the file does
  not end with a newline of any kind.
* An empty file yields nothing.
* A file of only newlines yields one b"" per newline.
* b"a\\r" at EOF yields b"a", not b"a\\r".
* b"a\\r\\n" yields b"a" once, not b"a" then b"".
* A CR at a chunk boundary is not misread as a lone CR; the reader
  holds it back until it can peek the next byte.
* A line longer than ``max_line_bytes`` raises :class:`LineTooLong`.
"""

import re
from typing import BinaryIO, Iterator


class LineTooLong(Exception):
    def __init__(self, length: int, max_length: int, byte_offset: int = -1):
        msg = f"Line of {length} bytes exceeds limit of {max_length} bytes"
        if byte_offset >= 0:
            msg += f" (near byte offset {byte_offset})"
        super().__init__(msg)
        self.length = length
        self.max_length = max_length
        self.byte_offset = byte_offset


_TERMINATOR = re.compile(rb"\r\n|[\r\n]")


def iter_lines(f: BinaryIO, max_line_bytes: int) -> Iterator[bytes]:
    """Yield each logical line's content, terminator removed.

    Linear time: the buffer is scanned with a moving offset and is only
    re-sliced once per chunk read, never once per line.
    """
    if max_line_bytes <= 0:
        raise ValueError("max_line_bytes must be positive")

    buf = b""
    pos = 0            # start of the unconsumed region within buf
    base = 0           # file offset of buf[0]
    at_eof = False

    while True:
        while True:
            m = _TERMINATOR.search(buf, pos)
            if m is None:
                if len(buf) - pos > max_line_bytes:
                    raise LineTooLong(len(buf) - pos, max_line_bytes,
                                      base + pos)
                break
            if (not at_eof and m.end() == len(buf)
                    and buf[m.start():m.end()] == b"\r"):
                # CR at the end of the buffer: need one more byte to tell
                # CRLF from a lone CR.
                break
            start = m.start()
            if start - pos > max_line_bytes:
                raise LineTooLong(start - pos, max_line_bytes, base + pos)
            yield buf[pos:start]
            pos = m.end()

        if at_eof:
            if pos < len(buf):
                if len(buf) - pos > max_line_bytes:
                    raise LineTooLong(len(buf) - pos, max_line_bytes,
                                      base + pos)
                yield buf[pos:]
            return

        chunk = f.read(1 << 20)
        if not chunk:
            at_eof = True
        else:
            base += pos
            buf = buf[pos:] + chunk
            pos = 0
