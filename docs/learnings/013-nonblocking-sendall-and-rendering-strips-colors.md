# Non-blocking sendall Busy-Loops & Rendering Strips Colors

**Date**: 2026-03-18

## The Mistakes

Two distinct bugs in the same fix cycle:

### Bug 1: `sendall()` on a non-blocking socket busy-loops

The RWS daemon set all new stream connections to non-blocking (`conn.setblocking(False)`) at accept time. When the client connected for PTY streaming, the daemon called `conn.sendall(bytes(session.ringbuffer))` to replay up to 512KB of history. On a non-blocking socket, `sendall()` internally retries when the TCP send buffer is full — but since the socket is non-blocking, each retry returns immediately with `EAGAIN`, creating a tight busy-loop that blocks the daemon's single-threaded event loop. This caused stream connection failures and repeated WebSocket reconnections, each replaying the full history — producing the "flash every 2-3 seconds" symptom.

**Fix**: Two-part:
1. Added `skip_ringbuffer` handshake flag so the client can opt out of the stream-socket replay entirely. The orchestrator fetches the raw ringbuffer via the command socket (reliable blocking TCP) instead.
2. For backwards compatibility (old clients), the daemon now temporarily sets the socket to blocking mode with a 30s timeout before `sendall()`, then restores non-blocking mode.

### Bug 2: VT renderer strips ANSI colors

The initial fix rendered the ringbuffer through `render_pty_screen()` (a VT emulator) and sent the result as a `{"type": "history"}` JSON message with plain text. This worked — no more flashing — but all terminal colors/styles were gone. The VT emulator intentionally discards SGR sequences (`# SGR (m) — no action needed`), so the output was colorless.

**Fix**: Instead of rendering to plain text, send the raw ringbuffer bytes as a binary WebSocket frame (prefixed with `\x1b[2J\x1b[H` to clear stale screen content). Raw bytes preserve all ANSI escape sequences. The reconnection-cycle fix comes from `skip_ringbuffer` + command socket, not from rendering.

## Rules

1. **Never call `sendall()` on a non-blocking socket with large data.** Either temporarily set blocking mode with a timeout, or use a proper non-blocking send loop with `select`/`poll`. A non-blocking `sendall()` with data larger than the TCP send buffer will busy-loop.

2. **Rendering through a VT emulator strips colors.** The VT emulators (`_render_pty_to_text`, `render_pty_screen`) produce plain text — they discard all SGR (color/style) sequences. When terminal output must preserve colors for display in xterm.js, send raw bytes as binary WebSocket frames, not rendered text as JSON. Use the text renderers only for non-display purposes (previews, status monitoring, text search).

3. **Separate the transport fix from the content fix.** The flashing was a transport problem (non-blocking sendall failing → reconnect cycles), not a content problem. The fix should change *how* bytes are delivered (command socket instead of stream socket), not *what* bytes are delivered (rendered text instead of raw bytes).
