"""Pre-launch Claude Code update helper.

Provides a user-controlled setting to automatically run ``claude update``
before every Claude Code launch, keeping brain and worker instances on the
latest version.

Three entry points cover all launch strategies:

* ``run_claude_update()`` — interactive (marker-based wait); used by paths
  that send individual commands to a tmux pane.
* ``get_claude_update_chain_command()`` — returns a shell fragment for
  insertion into ``&&``-joined command chains (local worker initial launch).
* ``should_update_before_start()`` — reads the setting from the config table.
"""

from __future__ import annotations

import logging
import random
import sqlite3
import time
from collections.abc import Callable

from orchestrator.state.repositories.config import get_config_value

logger = logging.getLogger(__name__)

CONFIG_KEY = "claude.update_before_start"


def should_update_before_start(conn: sqlite3.Connection) -> bool:
    """Read the ``claude.update_before_start`` config flag (default ``False``)."""
    value = get_config_value(conn, CONFIG_KEY, default=False)
    # Coerce to bool in case the stored value is a string or int
    return bool(value)


def run_claude_update(
    send_keys_fn: Callable,
    capture_fn: Callable,
    tmux_sess: str,
    tmux_win: str,
    timeout: int = 30,
) -> bool:
    """Send ``claude update`` to a tmux pane and wait for completion.

    The command is wrapped with ``2>/dev/null || true`` so that failures
    (missing binary, no internet, etc.) never block the subsequent Claude
    launch.

    Uses an inline done-marker (chained with ``;``) so there is no separate
    typeahead command and polling can start immediately with a short interval.

    Returns ``True`` if the command completed within *timeout* seconds,
    ``False`` on timeout (a warning is logged but the caller should
    proceed regardless).
    """
    marker_id = random.randint(10000, 99999)
    done_marker = f"__UPDATE_DONE_{marker_id}__"

    # Chain the marker on the same command line so it prints as soon as
    # claude update finishes — no separate typeahead command needed.
    send_keys_fn(
        tmux_sess,
        tmux_win,
        f"claude update 2>/dev/null || true; echo {done_marker}",
        enter=True,
    )

    start_time = time.time()
    while time.time() - start_time < timeout:
        time.sleep(0.5)
        output = capture_fn(tmux_sess, tmux_win, lines=20)
        if any(line.strip() == done_marker for line in output.splitlines()):
            return True

    logger.warning(
        "claude update did not complete within %ds for %s:%s — proceeding anyway",
        timeout,
        tmux_sess,
        tmux_win,
    )
    return False


def get_claude_update_chain_command() -> str:
    """Return a shell fragment suitable for ``&&``-joined command chains.

    The subshell ``( … )`` scopes the ``|| true`` so it does not short-circuit
    the outer ``&&`` chain.
    """
    return "(claude update 2>/dev/null || true)"
