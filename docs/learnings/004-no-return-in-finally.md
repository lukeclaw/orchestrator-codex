# Never `return` Inside `finally`

**Date**: 2026-03-16
**Related**: [007-reconnect-postmortem-2026-03.md](007-reconnect-postmortem-2026-03.md)

## The Mistake

`stream_remote_pty` was changed to return a `bool` (`pty_exited`), with `return pty_exited` placed at the end of the `finally` block:

```python
async def stream_remote_pty(...) -> bool:
    pty_exited = False
    try:
        # ... streaming loop ...
    except WebSocketDisconnect:
        pass
    finally:
        # ... cleanup ...
        return pty_exited  # BUG: suppresses any in-flight exception
```

Per Python semantics, a `return` in `finally` **silently suppresses any in-flight exception**. If the streaming loop raised an unexpected `KeyError`, `RuntimeError`, or `TypeError`, the exception would be discarded. The caller would receive `False` as if the stream dropped normally, and no error would be logged.

## Why It Matters

This makes bugs invisible. Any unexpected exception in the streaming loop gets swallowed silently -- no traceback, no error log, no indication anything went wrong. The caller proceeds as if everything is fine, potentially corrupting state or masking the real issue.

## The Fix

Move the `return` outside the `finally` block:

```python
async def stream_remote_pty(...) -> bool:
    pty_exited = False
    try:
        # ... streaming loop ...
    except WebSocketDisconnect:
        pass
    finally:
        # cleanup only -- no return here
        stream_closed.set()
        # ... cancel tasks, close socket ...
    return pty_exited  # After try/finally, not inside it
```

## Rule

**Never place `return` inside a `finally` block.** It silently swallows any in-flight exception from the `try` or `except` blocks. Always put the `return` statement after the `try/finally` block. If cleanup logic in `finally` needs to influence the return value, set a variable in `finally` and return it afterward.
