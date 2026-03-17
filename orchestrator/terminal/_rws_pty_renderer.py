"""Minimal VT emulator for rendering raw PTY output to text."""

from __future__ import annotations


def _render_pty_to_text(raw: bytes, cols: int = 200, rows: int = 50, last_n: int = 30) -> str:
    """Render raw PTY bytes into display text using a minimal VT emulator.

    Processes CSI sequences for cursor movement, erase, and SGR (ignored).
    Returns the last *last_n* non-empty lines from the virtual screen.
    """
    screen = [[" "] * cols for _ in range(rows)]
    cr, cc = 0, 0  # cursor row, col

    def _scroll_up():
        screen.pop(0)
        screen.append([" "] * cols)

    data = raw.decode("utf-8", errors="replace")
    i = 0
    n = len(data)
    while i < n:
        c = data[i]
        if c == "\x1b" and i + 1 < n:
            nc = data[i + 1]
            if nc == "[":
                # CSI: collect params and final byte
                j = i + 2
                while j < n and data[j] in "0123456789;?>=":
                    j += 1
                if j >= n:
                    break
                params = data[i + 2 : j]
                cmd = data[j]
                i = j + 1
                parts = params.replace("?", "").split(";") if params else []
                p1 = int(parts[0]) if parts and parts[0].isdigit() else 0
                p2 = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
                if cmd in ("H", "f"):  # Cursor position
                    cr = max(0, min(rows - 1, (p1 or 1) - 1))
                    cc = max(0, min(cols - 1, (p2 or 1) - 1))
                elif cmd == "A":  # Cursor up
                    cr = max(0, cr - max(1, p1))
                elif cmd == "B":  # Cursor down
                    cr = min(rows - 1, cr + max(1, p1))
                elif cmd == "C":  # Cursor forward
                    cc = min(cols - 1, cc + max(1, p1))
                elif cmd == "D":  # Cursor back
                    cc = max(0, cc - max(1, p1))
                elif cmd == "G":  # Cursor to column
                    cc = max(0, min(cols - 1, (p1 or 1) - 1))
                elif cmd == "J":  # Erase display
                    if p1 == 2 or p1 == 3:
                        screen[:] = [[" "] * cols for _ in range(rows)]
                        cr = cc = 0
                    elif p1 == 0:
                        screen[cr][cc:] = [" "] * (cols - cc)
                        for r in range(cr + 1, rows):
                            screen[r] = [" "] * cols
                    elif p1 == 1:
                        for r in range(cr):
                            screen[r] = [" "] * cols
                        screen[cr][: cc + 1] = [" "] * (cc + 1)
                elif cmd == "K":  # Erase line
                    if p1 == 0:
                        screen[cr][cc:] = [" "] * (cols - cc)
                    elif p1 == 1:
                        screen[cr][: cc + 1] = [" "] * (cc + 1)
                    elif p1 == 2:
                        screen[cr] = [" "] * cols
                # SGR (m), cursor show/hide (h/l), etc. — no action needed
                continue
            elif nc == "]":
                # OSC: skip to BEL or ST
                j = i + 2
                while j < n:
                    if data[j] == "\x07":
                        j += 1
                        break
                    if data[j] == "\x1b" and j + 1 < n and data[j + 1] == "\\":
                        j += 2
                        break
                    j += 1
                i = j
                continue
            else:
                i += 2  # 2-byte ESC sequence
                continue
        elif c == "\n":
            cr += 1
            if cr >= rows:
                _scroll_up()
                cr = rows - 1
            i += 1
        elif c == "\r":
            cc = 0
            i += 1
        elif c == "\t":
            cc = min(cols - 1, (cc // 8 + 1) * 8)
            i += 1
        elif c == "\x08":  # Backspace
            cc = max(0, cc - 1)
            i += 1
        elif c >= " " and c != "\x7f":
            if cr < rows and cc < cols:
                screen[cr][cc] = c
                cc += 1
                if cc >= cols:
                    cc = cols - 1
            i += 1
        else:
            i += 1  # skip other control chars

    # Extract non-empty lines from bottom of screen
    lines = ["".join(r).rstrip() for r in screen]
    # Trim trailing blank lines
    while lines and not lines[-1]:
        lines.pop()
    if last_n and len(lines) > last_n:
        lines = lines[-last_n:]
    return "\n".join(lines)
