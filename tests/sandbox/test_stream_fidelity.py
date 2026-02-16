#!/usr/bin/env python3
"""Sandbox test: compare tmux %output stream with capture-pane ground truth.

Creates a real tmux session, streams %output via control mode, feeds the raw
bytes to pyte (Python terminal emulator), and compares the rendered result
with tmux's own capture-pane.

Usage:
    python tests/sandbox/test_stream_fidelity.py
"""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import sys
import time

import pyte

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from orchestrator.terminal.control import (
    TmuxControlConnection,
    _strip_tmux_sequences,
    _unescape_tmux_output,
    get_pane_id_async,
)

TMUX_SESSION = "sandbox_test"
TMUX_WINDOW = "0"  # Use window index, not name
COLS = 120
ROWS = 40


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def tmux_run(*args, timeout=5) -> tuple[str, int]:
    result = subprocess.run(
        ["tmux"] + list(args),
        capture_output=True, text=True, timeout=timeout,
    )
    return result.stdout.strip(), result.returncode


def target():
    return f"{TMUX_SESSION}:{TMUX_WINDOW}"


def setup_tmux():
    tmux_run("kill-session", "-t", TMUX_SESSION)
    subprocess.run(
        ["tmux", "new-session", "-d", "-s", TMUX_SESSION,
         "-x", str(COLS), "-y", str(ROWS)],
        capture_output=True, timeout=5,
    )
    time.sleep(0.5)


def teardown_tmux():
    tmux_run("kill-session", "-t", TMUX_SESSION)


def capture_pane_plain() -> str:
    out, _ = tmux_run("capture-pane", "-p", "-t", target())
    return out


def send_literal(text: str):
    tmux_run("send-keys", "-t", target(), "-l", text)


def send_key(key: str):
    tmux_run("send-keys", "-t", target(), key)


def get_cursor() -> tuple[int, int]:
    out, _ = tmux_run(
        "display-message", "-p", "-t", target(),
        "#{cursor_x} #{cursor_y}",
    )
    return tuple(map(int, out.split()))


def pyte_display(screen: pyte.Screen) -> list[str]:
    lines = []
    for y in range(screen.lines):
        row = ""
        for x in range(screen.columns):
            row += screen.buffer[y][x].data
        lines.append(row.rstrip())
    return lines


def capture_to_lines(capture: str) -> list[str]:
    lines = capture.split('\n')
    while len(lines) < ROWS:
        lines.append('')
    return [l.rstrip() for l in lines[:ROWS]]


def compare(pyte_lines: list[str], cap_lines: list[str], label: str) -> bool:
    ok = True
    for i, (pl, cl) in enumerate(zip(pyte_lines, cap_lines)):
        if pl != cl:
            if ok:
                print(f"\n  MISMATCH in '{label}':")
            ok = False
            print(f"    row {i:2d} stream:  {pl!r}")
            print(f"    row {i:2d} capture: {cl!r}")
    if ok:
        print(f"  PASS: {label}")
    else:
        print(f"  FAIL: {label}")
    return ok


class StreamCapture:
    def __init__(self):
        self.events: list[bytes] = []
        self.buf = bytearray()

    async def on_output(self, raw: bytes):
        self.events.append(raw)
        self.buf.extend(raw)

    def reset(self):
        self.events.clear()
        self.buf.clear()


# ---------------------------------------------------------------------------
# Test: _unescape_tmux_output
# ---------------------------------------------------------------------------

def test_unescape():
    print("\n--- Test: _unescape_tmux_output ---")
    cases = [
        ("hello",               b"hello",           "plain ASCII"),
        ("\\033[31m",           b"\x1b[31m",        "ESC [ 31 m"),
        ("\\\\",                b"\\",              "escaped backslash"),
        ("\\100",               b"@",               "@ via octal 100"),
        ("\\012",               b"\n",              "LF via octal 012"),
        ("\\015",               b"\r",              "CR via octal 015"),
        ("\\033[H\\033[J",      b"\x1b[H\x1b[J",   "home + erase"),
        ("test\\100user",       b"test@user",       "@ embedded"),
        ("\\177",               b"\x7f",            "DEL"),
        ("\\303\\251",          b"\xc3\xa9",        "UTF-8 e-acute"),
        ("a\\",                 b"a\\",             "trailing backslash"),
        ("\\1x",                b"\\1x",            "short octal (2 chars)"),
    ]
    ok = True
    for inp, expected, desc in cases:
        got = _unescape_tmux_output(inp)
        status = "PASS" if got == expected else "FAIL"
        if got != expected:
            ok = False
        print(f"  {status}: {desc:25s}  {inp!r:30s} -> {got!r}")
    return ok


# ---------------------------------------------------------------------------
# Test: _strip_tmux_sequences
# ---------------------------------------------------------------------------

def test_strip_tmux():
    print("\n--- Test: _strip_tmux_sequences ---")
    cases = [
        (b"hello",                              b"hello",           "no sequences"),
        (b"\x1bkbash\x1b\\",                   b"",                "ESC k title ST only"),
        (b"\x1bkecho\x1b\\hello\r\n",          b"hello\r\n",       "title then output"),
        (b"before\x1bkls\x1b\\after",          b"beforeafter",     "title in middle"),
        (b"\x1bk\x1b\\",                       b"",                "empty title"),
        (b"\x1bkfoo\x1b\\bar\x1bkbaz\x1b\\",  b"bar",             "two sequences"),
        (b"\x1b[31mRED\x1b[0m",                b"\x1b[31mRED\x1b[0m", "ANSI preserved"),
        (b"test\x1b[Hcursor",                  b"test\x1b[Hcursor","CSI preserved"),
    ]
    ok = True
    for inp, expected, desc in cases:
        got = _strip_tmux_sequences(inp)
        status = "PASS" if got == expected else "FAIL"
        if got != expected:
            ok = False
            print(f"  {status}: {desc:30s}  expected={expected!r}  got={got!r}")
        else:
            print(f"  {status}: {desc}")
    return ok


# ---------------------------------------------------------------------------
# Test: stream fidelity (clean-slate approach)
# ---------------------------------------------------------------------------

async def test_bash_stream():
    """Compare stream vs capture for bash commands using clean-slate approach."""
    print("\n--- Test: Bash stream fidelity (clean-slate) ---")
    setup_tmux()
    try:
        pane_id = await get_pane_id_async(TMUX_SESSION, TMUX_WINDOW)
        if not pane_id:
            print("  FAIL: could not resolve pane id")
            return False
        print(f"  Pane ID: {pane_id}")

        # Set a simple ASCII prompt to avoid Unicode width issues
        send_literal("export PS1='$ '")
        send_key("Enter")
        await asyncio.sleep(0.5)

        conn = TmuxControlConnection(TMUX_SESSION)
        started = await conn.start()
        if not started:
            print("  FAIL: control mode connection failed")
            return False

        cap = StreamCapture()
        await conn.subscribe(pane_id, cap.on_output)
        await asyncio.sleep(0.3)

        # Verify streaming
        send_literal("echo ping")
        send_key("Enter")
        await asyncio.sleep(1.0)
        if len(cap.events) == 0:
            print("  FAIL: no %output events received")
            await conn.stop()
            return False

        all_ok = True

        async def run_test(cmd: str, label: str, wait: float = 1.0) -> bool:
            send_literal("clear")
            send_key("Enter")
            await asyncio.sleep(0.5)
            cap.reset()

            baseline_capture = capture_pane_plain()
            screen = pyte.Screen(COLS, ROWS)
            pstream = pyte.ByteStream(screen)
            baseline_lines = capture_to_lines(baseline_capture)
            for y, line in enumerate(baseline_lines):
                if line:
                    pstream.feed(f"\x1b[{y+1};1H{line}".encode('utf-8'))
            cx, cy = get_cursor()
            screen.cursor_position(cy + 1, cx + 1)
            cap.reset()

            send_literal(cmd)
            send_key("Enter")
            await asyncio.sleep(wait)

            pstream.feed(bytes(cap.buf))
            truth = capture_pane_plain()
            ok = compare(pyte_display(screen), capture_to_lines(truth), label)
            if not ok:
                print(f"    Stream ({len(cap.buf)} bytes, {len(cap.events)} events):")
                for i, ev in enumerate(cap.events[:15]):
                    print(f"      [{i}] {ev!r}")
            return ok

        all_ok &= await run_test("echo hello", "echo hello")
        all_ok &= await run_test("echo test@user.com", "@ character")
        all_ok &= await run_test("echo " + "A" * 100, "long line wrap")
        all_ok &= await run_test(
            "echo -e '\\033[31mRED\\033[0m normal'", "ANSI colors")
        all_ok &= await run_test("printf 'no-newline'", "no trailing newline")
        all_ok &= await run_test(
            "echo 'line1'; echo 'line2'; echo 'line3'", "multi-line")

        await conn.unsubscribe(pane_id, cap.on_output)
        await conn.stop()
        return all_ok
    finally:
        teardown_tmux()


# ---------------------------------------------------------------------------
# Test: raw-mode pty (simulates Claude Code)
# ---------------------------------------------------------------------------

RAW_SCRIPT = r'''
import sys, os, tty, termios
fd = sys.stdin.fileno()
old = termios.tcgetattr(fd)
try:
    tty.setraw(fd)
    sys.stdout.write("prompt> ")
    sys.stdout.flush()
    buf = ""
    while True:
        ch = os.read(fd, 1)
        if ch == b"\r" or ch == b"\n":
            sys.stdout.write("\r\n")
            if buf == "quit":
                break
            if buf == "hello":
                sys.stdout.write("world\r\n")
            elif buf == "at":
                sys.stdout.write("test@user\r\n")
            elif buf == "long":
                sys.stdout.write("A" * 100 + "\r\n")
            elif buf == "nolf":
                sys.stdout.write("before\nafter\r\n")
            else:
                sys.stdout.write(f"echo: {buf}\r\n")
            sys.stdout.write("prompt> ")
            sys.stdout.flush()
            buf = ""
        elif ch == b"\x03":
            break
        elif ch == b"\x7f":
            if buf:
                buf = buf[:-1]
                sys.stdout.write("\b \b")
                sys.stdout.flush()
        else:
            c = ch.decode("utf-8", errors="replace")
            buf += c
            sys.stdout.write(c)
            sys.stdout.flush()
finally:
    termios.tcsetattr(fd, termios.TCSADRAIN, old)
    sys.stdout.write("\r\n")
'''


async def test_raw_mode_stream():
    """Compare stream vs capture for a raw-mode process."""
    print("\n--- Test: Raw-mode pty stream fidelity ---")
    setup_tmux()
    try:
        script_path = "/tmp/sandbox_raw_mode.py"
        with open(script_path, "w") as f:
            f.write(RAW_SCRIPT)

        pane_id = await get_pane_id_async(TMUX_SESSION, TMUX_WINDOW)
        if not pane_id:
            print("  FAIL: could not resolve pane id")
            return False
        print(f"  Pane ID: {pane_id}")

        conn = TmuxControlConnection(TMUX_SESSION)
        await conn.start()
        cap = StreamCapture()
        await conn.subscribe(pane_id, cap.on_output)
        await asyncio.sleep(0.3)

        send_literal("clear")
        send_key("Enter")
        await asyncio.sleep(0.5)
        cap.reset()

        baseline_capture = capture_pane_plain()
        screen = pyte.Screen(COLS, ROWS)
        pstream = pyte.ByteStream(screen)
        baseline_lines = capture_to_lines(baseline_capture)
        for y, line in enumerate(baseline_lines):
            if line:
                pstream.feed(f"\x1b[{y+1};1H{line}".encode('utf-8'))
        cx, cy = get_cursor()
        screen.cursor_position(cy + 1, cx + 1)
        cap.reset()

        send_literal(f"python3 {script_path}")
        send_key("Enter")
        await asyncio.sleep(1.0)
        pstream.feed(bytes(cap.buf))
        if len(cap.events) == 0:
            print("  FAIL: no events from control mode")
            await conn.stop()
            return False

        all_ok = True

        async def run_raw_test(input_text: str, label: str, wait: float = 1.0) -> bool:
            cap.reset()
            for ch in input_text:
                send_literal(ch)
                await asyncio.sleep(0.05)
            send_key("Enter")
            await asyncio.sleep(wait)
            pstream.feed(bytes(cap.buf))
            truth = capture_pane_plain()
            ok = compare(pyte_display(screen), capture_to_lines(truth), label)
            if not ok:
                print(f"    Stream ({len(cap.buf)} bytes, {len(cap.events)} events):")
                for i, ev in enumerate(cap.events[:20]):
                    print(f"      [{i}] {ev!r}")
            return ok

        all_ok &= await run_raw_test("hello", "raw: hello -> world")
        all_ok &= await run_raw_test("at", "raw: @ in output")
        all_ok &= await run_raw_test("long", "raw: long line wrap")
        all_ok &= await run_raw_test("nolf", "raw: bare LF (no CR)")

        send_literal("quit")
        send_key("Enter")
        await asyncio.sleep(0.5)

        await conn.unsubscribe(pane_id, cap.on_output)
        await conn.stop()
        return all_ok
    finally:
        teardown_tmux()


# ---------------------------------------------------------------------------
# Test: TUI alternate screen (simulates Claude Code's ink rendering)
# ---------------------------------------------------------------------------

TUI_SCRIPT = r'''
import sys, time, os

def w(s):
    sys.stdout.buffer.write(s.encode() if isinstance(s, str) else s)
    sys.stdout.buffer.flush()

# Enter alternate screen
w('\x1b[?1049h')
w('\x1b[?25l')  # hide cursor

COLS = int(os.environ.get('COLUMNS', '120'))

# ---- Frame 1: "Running" status ----
w('\x1b[H\x1b[J')  # clear
w('\x1b[1;1H')
w('\x1b[1m\x1b[33m● Bash\x1b[0m(echo hello)')
w('\x1b[2;3H')
w('\x1b[32m●\x1b[0m Running…' + '─' * (COLS - 30) + ' 🔄')
w('\x1b[3;20H')
w('hook…')
w('\x1b[4;5H')
w('{      … (thought for 2s)')
w('\x1b[6;1H')
w('\x1b[1m\x1b[33m● Bash\x1b[0m(echo world)')
w('\x1b[7;26H')
w('ok…')
w('\x1b[8;10H')
w('g…')

time.sleep(0.3)

# ---- Frame 2: Replace with final results ----
w('\x1b[H\x1b[J')  # clear entire screen
w('\x1b[1;1H')
w('\x1b[1m\x1b[32m● Bash\x1b[0m(echo hello)')
w('\x1b[2;3H')
w('└ hello')
w('\x1b[3;1H')
w('\x1b[1m\x1b[32m● Bash\x1b[0m(echo world)')
w('\x1b[4;3H')
w('└ world')
w('\x1b[5;1H')
w('\x1b[1m\x1b[32m●\x1b[0m All commands completed successfully.')

time.sleep(0.3)

# ---- Frame 3: Add more content (simulates incremental update) ----
w('\x1b[7;1H')
w('─' * COLS)
w('\x1b[8;1H')
w('\x1b[1m›\x1b[0m ')

time.sleep(0.3)

# Show cursor again and exit alternate screen
w('\x1b[?25h')
w('\x1b[?1049l')
'''


async def test_tui_alternate_screen():
    """Test alternate screen TUI rendering (simulates Claude Code)."""
    print("\n--- Test: TUI alternate screen (Claude Code simulation) ---")
    setup_tmux()
    try:
        script_path = "/tmp/sandbox_tui.py"
        with open(script_path, "w") as f:
            f.write(TUI_SCRIPT)

        pane_id = await get_pane_id_async(TMUX_SESSION, TMUX_WINDOW)
        if not pane_id:
            print("  FAIL: could not resolve pane id")
            return False
        print(f"  Pane ID: {pane_id}")

        conn = TmuxControlConnection(TMUX_SESSION)
        await conn.start()
        cap = StreamCapture()
        await conn.subscribe(pane_id, cap.on_output)
        await asyncio.sleep(0.3)

        send_literal("clear")
        send_key("Enter")
        await asyncio.sleep(0.5)
        cap.reset()

        # Capture baseline (normal screen before alternate screen)
        baseline_capture = capture_pane_plain()
        screen = pyte.Screen(COLS, ROWS)
        pstream = pyte.ByteStream(screen)
        baseline_lines = capture_to_lines(baseline_capture)
        for y, line in enumerate(baseline_lines):
            if line:
                pstream.feed(f"\x1b[{y+1};1H{line}".encode('utf-8'))
        cx, cy = get_cursor()
        screen.cursor_position(cy + 1, cx + 1)
        cap.reset()

        # Run TUI script
        send_literal(f"COLUMNS={COLS} python3 {script_path}")
        send_key("Enter")
        # Wait for all 3 frames (0.3s each) plus buffer
        await asyncio.sleep(2.0)

        # Feed all stream bytes to pyte
        pstream.feed(bytes(cap.buf))

        print(f"  Stream: {len(cap.buf)} bytes, {len(cap.events)} events")

        # Check for specific issues in the stream
        issues = scan_stream_issues(cap.events)
        if issues:
            print("  Stream issues found:")
            for issue in issues:
                print(f"    - {issue}")

        # After the TUI exits alternate screen, we should be back to normal
        truth = capture_pane_plain()
        ok = compare(pyte_display(screen), capture_to_lines(truth), "TUI alternate screen")
        if not ok:
            print(f"    Last 10 events:")
            for i, ev in enumerate(cap.events[-10:]):
                idx = len(cap.events) - 10 + i
                print(f"      [{idx}] {ev!r}")

        await conn.unsubscribe(pane_id, cap.on_output)
        await conn.stop()
        return ok
    finally:
        teardown_tmux()


# ---------------------------------------------------------------------------
# Test: TUI with cursor-up rewrite (the pattern that causes ghosting)
# ---------------------------------------------------------------------------

# This script simulates how ink (React for CLI) does incremental re-renders:
# it moves the cursor UP to the start of the previous frame, then overwrites.
TUI_REWRITE_SCRIPT = r'''
import sys, time, os

def w(s):
    sys.stdout.buffer.write(s.encode() if isinstance(s, str) else s)
    sys.stdout.buffer.flush()

COLS = int(os.environ.get('COLUMNS', '120'))

# Enter alternate screen
w('\x1b[?1049h')
w('\x1b[?25l')
w('\x1b[H\x1b[J')  # clear

# ---- Frame 1: Write initial content ----
w('\x1b[1;1H')
w('● Running task 1…')
w('\x1b[2;3H')
w('Status: checking hook…')
w('\x1b[3;3H')
w('Progress: 45%')
w('\x1b[4;1H')
w('● Running task 2…')
w('\x1b[5;3H')
w('ok…')
w('\x1b[6;3H')
w('generating output…')

time.sleep(0.5)

# ---- Frame 2: Cursor-up rewrite (how ink updates) ----
# Move cursor back to row 1 and overwrite
w('\x1b[1;1H')           # go to row 1
w('\x1b[J')              # clear from cursor down
w('● Task 1 completed')
w('\x1b[2;3H')
w('└ Result: success')
w('\x1b[3;1H')
w('● Task 2 completed')
w('\x1b[4;3H')
w('└ Result: 42 items processed')
w('\x1b[5;1H')
w('─' * COLS)

time.sleep(0.5)

# ---- Frame 3: Another rewrite with more content ----
w('\x1b[1;1H')
w('\x1b[J')
w('● Task 1 completed')
w('\x1b[2;3H')
w('└ Result: success')
w('\x1b[3;1H')
w('● Task 2 completed')
w('\x1b[4;3H')
w('└ Result: 42 items processed')
w('\x1b[5;1H')
w('─' * COLS)
w('\x1b[6;1H')
w('● Summary: All tasks passed.')
w('\x1b[7;1H')
w('─' * COLS)
w('\x1b[8;1H')
w('› ')

time.sleep(0.3)

w('\x1b[?25h')
w('\x1b[?1049l')
'''


async def test_tui_cursor_up_rewrite():
    """Test the cursor-up rewrite pattern that ink/React-for-CLI uses."""
    print("\n--- Test: TUI cursor-up rewrite (ink pattern) ---")
    setup_tmux()
    try:
        script_path = "/tmp/sandbox_tui_rewrite.py"
        with open(script_path, "w") as f:
            f.write(TUI_REWRITE_SCRIPT)

        pane_id = await get_pane_id_async(TMUX_SESSION, TMUX_WINDOW)
        if not pane_id:
            print("  FAIL: could not resolve pane id")
            return False

        conn = TmuxControlConnection(TMUX_SESSION)
        await conn.start()
        cap = StreamCapture()
        await conn.subscribe(pane_id, cap.on_output)
        await asyncio.sleep(0.3)

        send_literal("clear")
        send_key("Enter")
        await asyncio.sleep(0.5)
        cap.reset()

        baseline_capture = capture_pane_plain()
        screen = pyte.Screen(COLS, ROWS)
        pstream = pyte.ByteStream(screen)
        baseline_lines = capture_to_lines(baseline_capture)
        for y, line in enumerate(baseline_lines):
            if line:
                pstream.feed(f"\x1b[{y+1};1H{line}".encode('utf-8'))
        cx, cy = get_cursor()
        screen.cursor_position(cy + 1, cx + 1)
        cap.reset()

        send_literal(f"COLUMNS={COLS} python3 {script_path}")
        send_key("Enter")
        await asyncio.sleep(2.5)

        pstream.feed(bytes(cap.buf))

        print(f"  Stream: {len(cap.buf)} bytes, {len(cap.events)} events")

        issues = scan_stream_issues(cap.events)
        if issues:
            print("  Stream issues found:")
            for issue in issues:
                print(f"    - {issue}")

        truth = capture_pane_plain()
        ok = compare(pyte_display(screen), capture_to_lines(truth), "cursor-up rewrite")
        if not ok:
            # Dump all events for analysis
            print(f"    All events ({len(cap.events)}):")
            for i, ev in enumerate(cap.events):
                print(f"      [{i}] {ev!r}")

        await conn.unsubscribe(pane_id, cap.on_output)
        await conn.stop()
        return ok
    finally:
        teardown_tmux()


# ---------------------------------------------------------------------------
# Test: history + stream pipeline
# ---------------------------------------------------------------------------

async def test_history_plus_stream():
    """Simulate the WebSocket handler: history load then stream."""
    print("\n--- Test: History load + stream pipeline ---")
    setup_tmux()
    try:
        send_literal("export PS1='$ '")
        send_key("Enter")
        await asyncio.sleep(0.3)

        send_literal("echo 'history line 1'")
        send_key("Enter")
        await asyncio.sleep(0.3)
        send_literal("echo 'history line 2'")
        send_key("Enter")
        await asyncio.sleep(0.3)

        pane_id = await get_pane_id_async(TMUX_SESSION, TMUX_WINDOW)
        if not pane_id:
            print("  FAIL: could not resolve pane id")
            return False

        history = capture_pane_plain()
        cx, cy = get_cursor()

        screen = pyte.Screen(COLS, ROWS)
        pstream = pyte.ByteStream(screen)
        screen.reset()
        history_lines = capture_to_lines(history)
        for y, line in enumerate(history_lines):
            if line:
                pstream.feed(f"\x1b[{y+1};1H{line}".encode('utf-8'))
        screen.cursor_position(cy + 1, cx + 1)

        conn = TmuxControlConnection(TMUX_SESSION)
        await conn.start()
        cap = StreamCapture()
        await conn.subscribe(pane_id, cap.on_output)
        await asyncio.sleep(0.2)
        cap.reset()

        send_literal("echo 'after connect'")
        send_key("Enter")
        await asyncio.sleep(1.0)

        pstream.feed(bytes(cap.buf))
        truth = capture_pane_plain()
        ok = compare(pyte_display(screen), capture_to_lines(truth), "history + stream")

        await conn.unsubscribe(pane_id, cap.on_output)
        await conn.stop()
        return ok
    finally:
        teardown_tmux()


# ---------------------------------------------------------------------------
# Test: verify ESC k sequences are stripped from live stream
# ---------------------------------------------------------------------------

async def test_no_esc_k_in_stream():
    """Verify that %output events don't contain ESC k sequences."""
    print("\n--- Test: No ESC k in live stream ---")
    setup_tmux()
    try:
        pane_id = await get_pane_id_async(TMUX_SESSION, TMUX_WINDOW)
        if not pane_id:
            print("  FAIL: could not resolve pane id")
            return False

        conn = TmuxControlConnection(TMUX_SESSION)
        await conn.start()
        cap = StreamCapture()
        await conn.subscribe(pane_id, cap.on_output)
        await asyncio.sleep(0.3)

        commands = ["echo hello", "ls /tmp", "printf 'test@user'", "echo done"]
        for cmd in commands:
            cap.reset()
            send_literal(cmd)
            send_key("Enter")
            await asyncio.sleep(0.5)

            for ev in cap.events:
                if b'\x1bk' in ev:
                    print(f"  FAIL: ESC k found in event after '{cmd}': {ev!r}")
                    await conn.stop()
                    return False

        print("  PASS: No ESC k sequences in any stream events")
        await conn.unsubscribe(pane_id, cap.on_output)
        await conn.stop()
        return True
    finally:
        teardown_tmux()


# ---------------------------------------------------------------------------
# Test: bare LF (convertEol) — validates the root cause fix
# ---------------------------------------------------------------------------

BARE_LF_SCRIPT = r'''
import sys, os, tty, termios
fd = sys.stdin.fileno()
old = termios.tcgetattr(fd)
try:
    tty.setraw(fd)
    # In raw mode (-opost), bare \n is LF only (cursor down, same column).
    # With convertEol: true in xterm.js, \n would be treated as \r\n,
    # incorrectly resetting cursor to column 0.
    sys.stdout.write("\x1b[H\x1b[J")  # clear
    sys.stdout.write("AAA\nBBB\nCCC\r\n")
    sys.stdout.write("DONE\r\n")
    sys.stdout.flush()
    import time; time.sleep(0.5)
finally:
    termios.tcsetattr(fd, termios.TCSADRAIN, old)
    sys.stdout.write("\r\n")
'''


async def test_bare_lf_convertEol():
    """Verify that bare LF in raw-mode PTY must NOT be treated as CR+LF.

    This test demonstrates the root cause of the 'extra characters' bug:
    - Raw-mode apps send bare \\n for line feed (cursor down, same column).
    - tmux renders this correctly (LF only).
    - xterm.js with convertEol: true wrongly adds CR, causing misalignment.
    - Fix: remove convertEol, convert \\n -> \\r\\n only for sync/history text.
    """
    print("\n--- Test: Bare LF (convertEol root cause) ---")
    setup_tmux()
    try:
        script_path = "/tmp/sandbox_bare_lf.py"
        with open(script_path, "w") as f:
            f.write(BARE_LF_SCRIPT)

        pane_id = await get_pane_id_async(TMUX_SESSION, TMUX_WINDOW)
        if not pane_id:
            print("  FAIL: could not resolve pane id")
            return False

        conn = TmuxControlConnection(TMUX_SESSION)
        await conn.start()
        cap = StreamCapture()
        await conn.subscribe(pane_id, cap.on_output)
        await asyncio.sleep(0.3)

        send_literal("clear")
        send_key("Enter")
        await asyncio.sleep(0.5)
        cap.reset()

        send_literal(f"python3 {script_path}")
        send_key("Enter")
        await asyncio.sleep(2.0)

        stream_bytes = bytes(cap.buf)
        truth = capture_pane_plain()
        truth_lines = capture_to_lines(truth)

        # pyte with default settings (LF = cursor down, same column)
        # This matches xterm.js WITHOUT convertEol — the correct behavior.
        screen_correct = pyte.Screen(COLS, ROWS)
        pstream_correct = pyte.ByteStream(screen_correct)
        pstream_correct.feed(stream_bytes)
        correct_lines = pyte_display(screen_correct)

        # Simulate convertEol: replace bare \n with \r\n in stream bytes.
        # This matches xterm.js WITH convertEol: true — the broken behavior.
        eol_bytes = bytearray()
        for i, b in enumerate(stream_bytes):
            if b == 0x0A:  # \n
                # Check if preceded by \r
                if i > 0 and stream_bytes[i - 1] == 0x0D:
                    eol_bytes.append(b)  # already \r\n, keep as is
                else:
                    eol_bytes.append(0x0D)  # add \r before bare \n
                    eol_bytes.append(b)
            else:
                eol_bytes.append(b)

        screen_broken = pyte.Screen(COLS, ROWS)
        pstream_broken = pyte.ByteStream(screen_broken)
        pstream_broken.feed(bytes(eol_bytes))
        broken_lines = pyte_display(screen_broken)

        # Check if stream contains bare \n (LF not preceded by CR)
        bare_lf_count = 0
        for i, b in enumerate(stream_bytes):
            if b == 0x0A and (i == 0 or stream_bytes[i - 1] != 0x0D):
                bare_lf_count += 1

        print(f"  Stream: {len(stream_bytes)} bytes, {bare_lf_count} bare LF found")

        # The correct rendering (no convertEol) should match tmux
        ok_correct = compare(correct_lines, truth_lines, "without convertEol (correct)")

        # The broken rendering (with convertEol) should NOT match tmux
        # (if there are bare LFs in the stream)
        ok_broken = compare(broken_lines, truth_lines, "with convertEol (should differ)")

        if bare_lf_count > 0 and ok_broken:
            print("  WARNING: convertEol rendering matched tmux despite bare LFs!")
            print("  This suggests bare LFs are not causing the issue.")

        if bare_lf_count > 0 and not ok_broken and ok_correct:
            print("  CONFIRMED: bare LF in raw mode causes convertEol mismatch.")
            print("  Fix: remove convertEol from xterm.js config.")

        await conn.unsubscribe(pane_id, cap.on_output)
        await conn.stop()
        return ok_correct
    finally:
        teardown_tmux()


# ---------------------------------------------------------------------------
# Stream analysis: scan for non-standard or problematic sequences
# ---------------------------------------------------------------------------

def scan_stream_issues(events: list[bytes]) -> list[str]:
    """Scan stream events for sequences that could cause rendering issues."""
    issues = []
    all_bytes = b''.join(events)

    # Check for ESC k (window title) - should already be stripped
    if b'\x1bk' in all_bytes:
        count = all_bytes.count(b'\x1bk')
        issues.append(f"ESC k (window title) found {count} times - should be stripped")

    # Check for APC (Application Program Command): ESC _
    if b'\x1b_' in all_bytes:
        count = all_bytes.count(b'\x1b_')
        issues.append(f"ESC _ (APC) found {count} times")

    # Check for PM (Privacy Message): ESC ^
    if b'\x1b^' in all_bytes:
        count = all_bytes.count(b'\x1b^')
        issues.append(f"ESC ^ (PM) found {count} times")

    # Check for DCS (Device Control String): ESC P
    if b'\x1bP' in all_bytes:
        count = all_bytes.count(b'\x1bP')
        issues.append(f"ESC P (DCS) found {count} times")

    # Check for SOS (Start of String): ESC X
    if b'\x1bX' in all_bytes:
        count = all_bytes.count(b'\x1bX')
        issues.append(f"ESC X (SOS) found {count} times")

    # Check for C1 control codes (0x80-0x9F range) used as 8-bit controls
    c1_found = []
    for b in all_bytes:
        if 0x80 <= b <= 0x9F:
            c1_found.append(b)
    if c1_found:
        unique = set(c1_found)
        issues.append(f"C1 control codes found: {[hex(x) for x in sorted(unique)]}")

    # Check for OSC (Operating System Command): ESC ]
    # These should be handled by xterm.js, but let's catalog them
    osc_pattern = re.compile(rb'\x1b\](\d+);')
    osc_matches = osc_pattern.findall(all_bytes)
    if osc_matches:
        osc_types = {}
        for m in osc_matches:
            t = int(m)
            osc_types[t] = osc_types.get(t, 0) + 1
        issues.append(f"OSC sequences found (should be handled by xterm.js): {osc_types}")

    # Check for unterminated ESC sequences at event boundaries
    for i, ev in enumerate(events):
        if ev and ev[-1] == 0x1B:
            issues.append(f"Event [{i}] ends with lone ESC (potential split sequence)")
        # Check for split CSI: ESC at end of one event, [ at start of next
        if ev and ev[-1] == 0x1B and i + 1 < len(events):
            next_ev = events[i + 1]
            if next_ev and next_ev[0] == 0x5B:  # [
                issues.append(f"Events [{i}]-[{i+1}]: split CSI (ESC | [)")

    return issues


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    print("=" * 70)
    print("Terminal Stream Fidelity Test")
    print("=" * 70)

    results = {}
    results["unescape"] = test_unescape()
    results["strip_tmux"] = test_strip_tmux()
    results["no_esc_k"] = await test_no_esc_k_in_stream()
    results["bash_stream"] = await test_bash_stream()
    results["raw_mode"] = await test_raw_mode_stream()
    results["bare_lf"] = await test_bare_lf_convertEol()
    results["tui_altscreen"] = await test_tui_alternate_screen()
    results["tui_rewrite"] = await test_tui_cursor_up_rewrite()
    results["history_stream"] = await test_history_plus_stream()

    print("\n" + "=" * 70)
    print("Summary:")
    for name, ok in results.items():
        print(f"  {'PASS' if ok else 'FAIL'}: {name}")
    print("=" * 70)

    if not all(results.values()):
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
