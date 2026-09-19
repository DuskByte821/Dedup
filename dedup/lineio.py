"""Consistent LF / CRLF / CR line iteration over binary streams."""


def iter_lines(f, chunk_size=1 << 20):
    """Yield (content: bytes, terminator_len: int) for each line in binary file.

    Handles LF, CRLF and bare CR. The final line yields terminator_len=0 when
    the file does not end with a newline. Empty files yield nothing.
    """
    buf = b''
    while True:
        chunk = f.read(chunk_size)
        if not chunk:
            break
        buf += chunk
        while True:
            idx_n = buf.find(b'\n')
            idx_r = buf.find(b'\r')
            if idx_n == -1 and idx_r == -1:
                break
            if idx_n != -1 and (idx_r == -1 or idx_n < idx_r):
                yield buf[:idx_n], 1
                buf = buf[idx_n + 1:]
            else:
                if idx_r + 1 >= len(buf):
                    # Could be \r\n split across chunks — read more.
                    break
                if buf[idx_r + 1] == 0x0A:
                    yield buf[:idx_r], 2
                    buf = buf[idx_r + 2:]
                else:
                    yield buf[:idx_r], 1
                    buf = buf[idx_r + 1:]
    if buf:
        if buf.endswith(b'\r'):
            yield buf[:-1], 1
        else:
            yield buf, 0
