# 016 — Remote Worker Server (RWS)

## Problem

Remote interactive CLI has two issues:
1. **Slow startup** (10-30s for a separate SSH handshake per terminal)
2. **Doesn't survive SSH reconnects** — the tmux window with SSH dies, killing the terminal session

The existing `remote_file_server.py` communicates over SSH stdin/stdout, so it also dies on SSH disconnect.

## Solution

A daemonized TCP server on the remote host that handles both file operations and PTY terminal sessions. Pre-started when the user opens the session detail page. Survives SSH disconnects; the orchestrator reaches it via an SSH forward tunnel.

## Architecture

```
Orchestrator                        Remote Host
+------------------+               +---------------------------+
| RemoteWorkerSvr  |-- TCP via -->| RWS Daemon (port 9741)    |
| (client class)   |  SSH -L fwd  |  +- file ops (JSON req)   |
|                  |  tunnel      |  +- PTY sessions (push)    |
| ws_terminal.py   |              |  +- health / info          |
|  +- stream_      |              |                            |
|     remote_pty() |              |  Survives SSH disconnect   |
+------------------+               +---------------------------+
```

## Protocol

TCP socket with JSON-lines. Each TCP connection sends a handshake on connect:
- `{"type": "command"}` — file ops, PTY management, ping
- `{"type": "pty_stream", "pty_id": "abc123"}` — dedicated PTY I/O

Command connections use JSON-line request/response. PTY stream connections are full-duplex: server pushes raw bytes, client sends JSON-line input/resize commands.

## Daemon Lifecycle

- Deployed via SSH (base64 bootstrap, same as current file server)
- Forks to background with `os.fork()` + `os.setsid()`, detaches from SSH
- Binds `127.0.0.1:9741`, writes PID file `/tmp/orchestrator-rws-{port}.pid`
- Checks for existing daemon on start (PID file + TCP ping) — reuses if alive
- Auto-shutdown after 60 min inactivity (no connections, no active PTYs)

## PTY Management

- `pty_create`: `pty.openpty()` + `os.fork()` + `os.execvp("/bin/bash", ["bash", "-l"])`
- 64KB ringbuffer per PTY for history replay on reattach
- Stream connections receive raw PTY bytes; on attach, ringbuffer is replayed first
- After replay, `Ctrl+L` is sent to force screen redraw

## Connection Resilience

The RWS client automatically recovers from broken connections at multiple levels:

1. **Command socket auto-reconnect**: If the TCP command socket breaks (EOF, broken pipe, connection reset), `execute()` retries once by reconnecting the socket through the existing tunnel.
2. **Tunnel-level reconnect**: If the SSH forward tunnel dies, `get_remote_worker_server()` removes the stale server from the pool and kicks off a background restart (new tunnel + socket).
3. **Socket reconnect in pool**: If the socket is dead but the tunnel is alive, `get_remote_worker_server()` attempts `_connect_command_socket()` before falling back to a full restart.
4. **Final resort: daemon kill+restart**: If the background start fails (e.g., daemon is stuck/unresponsive), the system kills the remote daemon via SSH (`kill_remote_daemon()`) and starts fresh. This ensures the user is never permanently stuck.

Timeout handling: socket timeouts clear both `_cmd_sock` and `_cmd_buffer` so the next call can reconnect cleanly.

## Fallback Strategy

All file operations go through RWS:

```
RWS daemon (TCP) -> error (HTTP 502 with message)
```

Interactive CLI goes through RWS, falls back to legacy tmux+SSH:

```
RWS PTY (TCP stream) -> tmux window + SSH
```

## Files

| File | Action |
|------|--------|
| `orchestrator/terminal/remote_worker_server.py` | Created — daemon script + client + pool |
| `orchestrator/state/models.py` | Modified — `remote_pty_id`, `rws_host` on `InteractiveCLI` |
| `orchestrator/terminal/interactive.py` | Modified — `open_interactive_cli_via_rws()`, RWS-aware close/send/capture |
| `orchestrator/api/routes/interactive_cli.py` | Modified — RWS path for remote workers |
| `orchestrator/api/ws_terminal.py` | Modified — `stream_remote_pty()`, routing in `ws_interactive_cli()` |
| `orchestrator/api/routes/files.py` | Modified — RWS as primary for all remote file ops |
| `orchestrator/api/routes/sessions.py` | Modified — pre-start RWS on session detail load |
| `orchestrator/api/app.py` | Modified — RWS pool shutdown in lifespan |
| `orchestrator/session/reconnect.py` | Modified — RWS tunnel re-establishment after SSH reconnect |
| `tests/test_remote_worker_server.py` | Created — 49 unit tests |
