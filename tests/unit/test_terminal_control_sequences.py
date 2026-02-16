"""Unit tests for tmux control sequence stripping."""

from orchestrator.terminal.control import _strip_tmux_sequences


def test_strip_tmux_sequences_stateless_basics():
    assert _strip_tmux_sequences(b"hello") == b"hello"
    assert _strip_tmux_sequences(b"\x1bkbash\x1b\\") == b""
    assert _strip_tmux_sequences(b"before\x1bkls\x1b\\after") == b"beforeafter"
    assert _strip_tmux_sequences(b"\x1b[31mRED\x1b[0m") == b"\x1b[31mRED\x1b[0m"


def test_strip_tmux_sequences_stateful_across_chunks():
    state = {"in_title": False, "pending_esc": False}

    chunk1 = b"prefix\x1bkLONG_TITLE_PART"
    chunk2 = b"_CONT\x1b\\suffix"

    out1 = _strip_tmux_sequences(chunk1, state)
    out2 = _strip_tmux_sequences(chunk2, state)

    assert out1 + out2 == b"prefixsuffix"
    assert state == {"in_title": False, "pending_esc": False}


def test_strip_tmux_sequences_stateful_split_after_esc():
    state = {"in_title": False, "pending_esc": False}

    out1 = _strip_tmux_sequences(b"abc\x1b", state)
    out2 = _strip_tmux_sequences(b"ktitle\x1b\\XYZ", state)

    assert out1 + out2 == b"abcXYZ"
    assert state == {"in_title": False, "pending_esc": False}


def test_strip_tmux_sequences_stateful_non_title_escape_preserved():
    state = {"in_title": False, "pending_esc": False}

    out1 = _strip_tmux_sequences(b"\x1b", state)
    out2 = _strip_tmux_sequences(b"[31mRED", state)

    assert out1 + out2 == b"\x1b[31mRED"
    assert state == {"in_title": False, "pending_esc": False}
