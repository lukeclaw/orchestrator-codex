"""Unit tests for the minimal VT emulator used to render PTY captures."""

import pytest

from orchestrator.terminal.remote_worker_server import _render_pty_to_text


class TestRenderPtyToText:
    """Test the VT screen renderer."""

    def test_plain_text(self):
        """Plain text without escape sequences is rendered correctly."""
        raw = b"Hello World\r\nSecond line"
        result = _render_pty_to_text(raw, cols=80, rows=24, last_n=0)
        lines = result.split("\n")
        assert lines[0] == "Hello World"
        assert lines[1] == "Second line"

    def test_cursor_forward_produces_spaces(self):
        """ESC[nC (cursor forward) should produce visible spaces."""
        # "Hello" + cursor forward 3 + "World"
        raw = b"Hello\x1b[3CWorld"
        result = _render_pty_to_text(raw, cols=80, rows=24, last_n=0)
        assert result.strip() == "Hello   World"

    def test_cursor_position(self):
        """ESC[row;colH positions text correctly."""
        raw = b"\x1b[2;5HHello"
        result = _render_pty_to_text(raw, cols=80, rows=24, last_n=0)
        lines = result.split("\n")
        assert len(lines) >= 2
        assert lines[1] == "    Hello"

    def test_sgr_stripped_text_preserved(self):
        """SGR sequences (colors/bold) are ignored, text is preserved."""
        # ESC[1m (bold) + "Hello " + ESC[0m (reset) + "World"
        raw = b"\x1b[1mHello \x1b[0mWorld"
        result = _render_pty_to_text(raw, cols=80, rows=24, last_n=0)
        assert result.strip() == "Hello World"

    def test_erase_display(self):
        """ESC[2J clears the screen."""
        raw = b"Old text\x1b[2J\x1b[1;1HNew text"
        result = _render_pty_to_text(raw, cols=80, rows=24, last_n=0)
        assert "Old text" not in result
        assert result.strip() == "New text"

    def test_erase_line(self):
        """ESC[K clears to end of line."""
        raw = b"Hello World\x1b[1;6H\x1b[K"
        result = _render_pty_to_text(raw, cols=80, rows=24, last_n=0)
        assert result.strip() == "Hello"

    def test_last_n_lines(self):
        """Only the last N lines are returned when last_n is set."""
        raw = b"Line 1\r\nLine 2\r\nLine 3\r\nLine 4"
        result = _render_pty_to_text(raw, cols=80, rows=24, last_n=2)
        lines = result.split("\n")
        assert len(lines) == 2
        assert lines[0] == "Line 3"
        assert lines[1] == "Line 4"

    def test_carriage_return_overwrites(self):
        """CR without LF causes overwrite of current line."""
        raw = b"XXXXX\rHello"
        result = _render_pty_to_text(raw, cols=80, rows=24, last_n=0)
        assert result.strip() == "Hello"

    def test_tab_alignment(self):
        """Tab characters advance to next 8-column stop."""
        raw = b"A\tB"
        result = _render_pty_to_text(raw, cols=80, rows=24, last_n=0)
        assert result.strip() == "A       B"

    def test_osc_sequences_ignored(self):
        """OSC sequences (like terminal title) are skipped."""
        raw = b"\x1b]0;My Title\x07Hello"
        result = _render_pty_to_text(raw, cols=80, rows=24, last_n=0)
        assert result.strip() == "Hello"

    def test_scroll_on_overflow(self):
        """Screen scrolls when cursor goes past last row."""
        rows = 3
        raw = b"Line1\r\nLine2\r\nLine3\r\nLine4"
        result = _render_pty_to_text(raw, cols=80, rows=rows, last_n=0)
        lines = result.split("\n")
        # Line1 should have scrolled off; Line2, Line3, Line4 remain
        assert lines[0] == "Line2"
        assert lines[1] == "Line3"
        assert lines[2] == "Line4"

    def test_backspace(self):
        """Backspace moves cursor back."""
        raw = b"Hellx\x08o"
        result = _render_pty_to_text(raw, cols=80, rows=24, last_n=0)
        assert result.strip() == "Hello"

    def test_empty_input(self):
        """Empty bytes produce empty string."""
        assert _render_pty_to_text(b"", cols=80, rows=24, last_n=0) == ""

    def test_256_color_sgr(self):
        """256-color SGR sequences are handled (ignored)."""
        raw = b"\x1b[38;5;123mColored\x1b[0m text"
        result = _render_pty_to_text(raw, cols=80, rows=24, last_n=0)
        assert result.strip() == "Colored text"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
