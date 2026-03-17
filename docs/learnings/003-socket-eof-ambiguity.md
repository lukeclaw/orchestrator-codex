# Socket EOF Is Ambiguous

**Date**: 2026-03-16
**Related**: [007-reconnect-postmortem-2026-03.md](007-reconnect-postmortem-2026-03.md)

## The Mistake

`stream_remote_pty` returned `pty_exited=True` when `sock.recv()` returned `b""` or raised `OSError`. The caller (`ws_interactive_cli`) then unconditionally sent `pty_exit` + close(4005), telling the frontend to stop retrying.

But `b""` / `OSError` means "the socket closed" -- not "the remote process died." Two distinct scenarios produce the same signal:

1. **True PTY exit**: daemon closes the stream after PTY process dies (real EOF)
2. **Tunnel death**: SSH tunnel dies, stream socket gets RST -> `OSError` -> `b""` (false positive)

When the tunnel dies, the PTY is still alive on the remote host, but the frontend was told to stop retrying.

## The Design Flaw

The code already had a `confirmed_dead` check (querying the daemon's control channel) to distinguish these cases. But the new code in `ws_interactive_cli` bypassed it -- it acted on `pty_exited` alone without checking `confirmed_dead`.

## The Fix

Return richer results from `stream_remote_pty` so callers can make informed decisions:

```python
@dataclass
class StreamResult:
    pty_exited: bool       # Socket-level signal (ambiguous)
    confirmed_dead: bool   # Daemon-level signal (authoritative when available)
```

Callers should only send definitive `pty_exit` when `confirmed_dead=True`. When `pty_exited=True` but `confirmed_dead=False`, keep the CLI in the registry and let the frontend retry with limited attempts.

## Rule

**Socket EOF is ambiguous.** `recv()` returning empty or raising `OSError` means "the socket closed," not "the remote process died." Always use an out-of-band confirmation (daemon query, process check) before treating a socket close as a semantic "process exited" event. If the confirmation channel is also down, treat the situation as ambiguous -- don't commit to either interpretation.
