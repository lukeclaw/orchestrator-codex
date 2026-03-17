"""Remote Worker Server — re-export facade for backwards compatibility.

All symbols are defined in submodules and re-exported here so that
existing ``from orchestrator.terminal.remote_worker_server import X``
continues to work unchanged.

Architecture (see individual modules for details):
  _rws_daemon.py        — Daemon script (read as text, sent to remote hosts)
  _rws_pty_renderer.py  — VT emulator for rendering raw PTY output
  _rws_client.py        — RemoteWorkerServer client class + constants
  _rws_pool.py          — Connection pool management
"""

from orchestrator.terminal._rws_client import (
    _BOOTSTRAP_TMPL,
    _REMOTE_WORKER_SERVER_SCRIPT,
    _SCRIPT_HASH,
    _TUNNEL_SSH_OPTS,
    RWS_REMOTE_PORT,
    RemoteWorkerServer,
)
from orchestrator.terminal._rws_pool import (
    _pool_lock,
    _server_pool,
    _starting,
    ensure_rws_starting,
    force_restart_server,
    get_remote_worker_server,
    shutdown_all_rws_servers,
)
from orchestrator.terminal._rws_pty_renderer import _render_pty_to_text

__all__ = [
    "RemoteWorkerServer",
    "RWS_REMOTE_PORT",
    "_BOOTSTRAP_TMPL",
    "_REMOTE_WORKER_SERVER_SCRIPT",
    "_SCRIPT_HASH",
    "_TUNNEL_SSH_OPTS",
    "_pool_lock",
    "_render_pty_to_text",
    "_server_pool",
    "_starting",
    "ensure_rws_starting",
    "force_restart_server",
    "get_remote_worker_server",
    "shutdown_all_rws_servers",
]
