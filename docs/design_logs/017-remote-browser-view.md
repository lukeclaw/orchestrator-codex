---
title: "Remote Browser View — CDP Screencast for rdev Browsers"
author: Yudong Qiu
created: 2026-03-01
last_modified: 2026-03-01
status: Proposed
---

# Remote Browser View — CDP Screencast for rdev Browsers

## 1. Problem

Workers on rdev frequently use Playwright to automate browser tasks. When a browser opens on an auth login page (OAuth, SSO, MFA), the worker gets stuck — it cannot interact with the page, and the operator cannot see the remote browser.

The obvious fix — port-tunneling the website's port to localhost — **does not work** for auth flows:

- **OAuth redirect URLs** are registered against the real domain. Redirecting to `localhost` fails validation.
- **Cookie domains** are scoped to the real hostname. Cookies set on `localhost` are invisible to the actual service.
- **CORS policies** reject `localhost` as an origin.

The browser must stay on rdev with its original URL intact. What we need is a way to **see and interact with the remote browser from the dashboard** without changing the URL the browser sees.

## 2. Architecture Overview

The solution uses Chrome DevTools Protocol (CDP) screencast to stream the remote browser's rendered frames to the operator, and relays mouse/keyboard events back. The browser stays on rdev; only pixels and input events travel over the wire.

```
┌─────────────────────────────────────────────────────────────────────────┐
│  rdev (remote)                                                          │
│                                                                         │
│  ┌──────────────────┐     ┌──────────────────────────────────────────┐  │
│  │ Playwright Worker │     │ Chromium Browser                         │  │
│  │ (stuck at login)  │     │   - Actual URL: https://sso.company.com │  │
│  └──────────────────┘     │   - CDP WebSocket on :9222              │  │
│                            └──────────┬─────────────────────────────┘  │
│                                       │ CDP (localhost:9222)           │
└───────────────────────────────────────┼─────────────────────────────────┘
                                        │
                                        │ SSH -L tunnel (local:9222 → rdev:9222)
                                        │
┌───────────────────────────────────────┼─────────────────────────────────┐
│  Local Machine                        │                                  │
│                                       ▼                                  │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │ Orchestrator Server (:8093)                                      │   │
│  │                                                                  │   │
│  │  CDP Proxy Module                                                │   │
│  │  ├─ Connects to localhost:9222 (tunneled CDP)                    │   │
│  │  ├─ Page.startScreencast → JPEG frames                          │   │
│  │  ├─ Input.dispatchMouseEvent / Input.dispatchKeyEvent            │   │
│  │  └─ Exposes /ws/browser-view/{session_id}                       │   │
│  │                                                                  │   │
│  │  REST API                                                        │   │
│  │  ├─ POST /api/sessions/{id}/browser-view       (start)          │   │
│  │  ├─ DELETE /api/sessions/{id}/browser-view      (stop)           │   │
│  │  └─ GET /api/sessions/{id}/browser-view         (status)         │   │
│  └──────────────────────────────────────────────────────────────────┘   │
│                                                                          │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │ Dashboard (Browser)                                              │   │
│  │  ┌────────────────────────────────────────────────────────────┐  │   │
│  │  │ SessionDetailPage                                          │  │   │
│  │  │  ┌──────────────────────────────────────────────────────┐  │  │   │
│  │  │  │ Main Terminal                                        │  │   │  │
│  │  │  │                                     ┌──────────────┐ │  │   │  │
│  │  │  │                                     │ BrowserView  │ │  │   │  │
│  │  │  │                                     │ (Canvas PiP) │ │  │   │  │
│  │  │  │                                     │              │ │  │   │  │
│  │  │  │                                     │  [Login Page] │ │  │   │  │
│  │  │  │                                     │  User: ____  │ │  │   │  │
│  │  │  │                                     │  Pass: ____  │ │  │   │  │
│  │  │  │                                     │  [Sign In]   │ │  │   │  │
│  │  │  │                                     └──────────────┘ │  │   │  │
│  │  │  └──────────────────────────────────────────────────────┘  │  │   │
│  │  └────────────────────────────────────────────────────────────┘  │   │
│  └──────────────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────────────┘
```

### Data Flow

1. **Startup**: Operator clicks "View Browser" on SessionDetailPage. Backend creates an SSH tunnel for the CDP port, connects to Chrome via CDP, and starts screencast.
2. **Streaming**: CDP sends JPEG frames → backend relays as binary WebSocket frames → frontend draws on `<canvas>`.
3. **Input**: User clicks/types on canvas → frontend sends JSON input events via WebSocket → backend translates to CDP `Input.dispatch*` calls.
4. **Shutdown**: Operator clicks "Close" or session ends → backend stops screencast, disconnects CDP, closes tunnel.

## 3. Backend Components

### 3.1 CDP Proxy Module

**Location**: `orchestrator/browser/cdp_proxy.py`

This module manages the CDP WebSocket connection to the remote browser and translates between the dashboard WebSocket protocol and CDP commands.

```python
@dataclass
class BrowserViewSession:
    session_id: str              # Parent worker session
    cdp_ws: websockets.WebSocketClientProtocol  # CDP connection
    tunnel_local_port: int       # SSH tunnel local port (for cleanup)
    page_id: str                 # CDP target/page ID
    viewport_width: int          # Current browser viewport width
    viewport_height: int         # Current browser viewport height
    status: str                  # "connecting" | "active" | "closed"
    created_at: str              # ISO timestamp
    url: str                     # Current page URL (from CDP)

# In-memory registry (one browser view per session, like InteractiveCLI)
_active_views: dict[str, BrowserViewSession] = {}
```

#### CDP Discovery

Before connecting, we need to find the browser's CDP WebSocket URL. Playwright-launched browsers expose CDP on a debug port. The proxy queries the CDP `/json` endpoint to discover available pages:

```python
async def discover_browser_targets(cdp_port: int) -> list[dict]:
    """Query CDP /json endpoint to find debuggable pages.

    Returns list of targets with fields: id, title, url, webSocketDebuggerUrl.
    Filters to type='page' targets only.
    """
    async with aiohttp.ClientSession() as session:
        async with session.get(f"http://localhost:{cdp_port}/json") as resp:
            targets = await resp.json()
            return [t for t in targets if t.get("type") == "page"]
```

#### Screencast Control

```python
async def start_screencast(
    cdp_ws: websockets.WebSocketClientProtocol,
    quality: int = 60,
    max_width: int = 1280,
    max_height: int = 960,
) -> None:
    """Start CDP Page.startScreencast.

    Args:
        quality: JPEG quality (1-100). 60 is a good balance of
                 quality vs bandwidth (~30-80 KB per frame).
        max_width: Maximum frame width in pixels.
        max_height: Maximum frame height in pixels.
    """
    await cdp_ws.send(json.dumps({
        "id": _next_id(),
        "method": "Page.startScreencast",
        "params": {
            "format": "jpeg",
            "quality": quality,
            "maxWidth": max_width,
            "maxHeight": max_height,
            "everyNthFrame": 1,
        }
    }))

async def stop_screencast(cdp_ws: websockets.WebSocketClientProtocol) -> None:
    await cdp_ws.send(json.dumps({
        "id": _next_id(),
        "method": "Page.stopScreencast",
    }))
```

#### Input Dispatch

```python
async def dispatch_mouse_event(
    cdp_ws: websockets.WebSocketClientProtocol,
    event_type: str,   # "mousePressed" | "mouseReleased" | "mouseMoved"
    x: float,
    y: float,
    button: str = "left",
    click_count: int = 1,
) -> None:
    """Dispatch a mouse event to the browser via CDP Input.dispatchMouseEvent."""
    await cdp_ws.send(json.dumps({
        "id": _next_id(),
        "method": "Input.dispatchMouseEvent",
        "params": {
            "type": event_type,
            "x": x,
            "y": y,
            "button": button,
            "clickCount": click_count,
        }
    }))

async def dispatch_key_event(
    cdp_ws: websockets.WebSocketClientProtocol,
    event_type: str,   # "keyDown" | "keyUp" | "char"
    key: str = "",
    code: str = "",
    text: str = "",
    modifiers: int = 0,  # bitmask: Alt=1, Ctrl=2, Meta=4, Shift=8
) -> None:
    """Dispatch a keyboard event to the browser via CDP Input.dispatchKeyEvent."""
    params = {
        "type": event_type,
        "modifiers": modifiers,
    }
    if key:
        params["key"] = key
    if code:
        params["code"] = code
    if text:
        params["text"] = text
    await cdp_ws.send(json.dumps({
        "id": _next_id(),
        "method": "Input.dispatchKeyEvent",
        "params": params,
    }))
```

### 3.2 SSH Tunnel for CDP Port

Reuses the existing `create_tunnel()` from `orchestrator/session/tunnel.py`. The CDP debug port (typically 9222, but Playwright may choose a random port) is tunneled from rdev to localhost.

```python
from orchestrator.session.tunnel import create_tunnel, close_tunnel

async def setup_cdp_tunnel(host: str, remote_cdp_port: int) -> int:
    """Create SSH tunnel for CDP port. Returns local port."""
    success, info = create_tunnel(host, remote_cdp_port)
    if not success:
        raise RuntimeError(f"Failed to create CDP tunnel: {info.get('error')}")
    return info["local_port"]
```

The CDP port on the remote must be discovered. Playwright typically writes the debug port to stderr or a well-known file. The worker can report it via the API, or the backend can scan for it. Two approaches:

1. **Worker reports CDP port**: The worker calls an API endpoint after launching the browser:
   `POST /api/sessions/{id}/browser-view {"cdp_port": 9222}`
2. **Auto-discovery**: The backend SSHs to rdev and scans for Chrome processes with `--remote-debugging-port=NNNN`.

Approach 1 is simpler and more reliable. The worker prompt will document how to report the CDP port.

### 3.3 REST API Endpoints

**Location**: `orchestrator/api/routes/browser_view.py`

```python
class BrowserViewRequest(BaseModel):
    cdp_port: int = 9222        # CDP debug port on rdev
    quality: int = 60            # JPEG quality (1-100)
    max_width: int = 1280        # Max frame width
    max_height: int = 960        # Max frame height

@router.post("/api/sessions/{session_id}/browser-view")
async def start_browser_view(session_id: str, body: BrowserViewRequest):
    """Start a browser view session for a worker.

    1. Creates SSH tunnel for CDP port (if not already tunneled)
    2. Queries CDP /json to discover page targets
    3. Connects to the first page target via CDP WebSocket
    4. Starts Page.startScreencast
    5. Registers the BrowserViewSession

    Response:
        {
            "ok": true,
            "page_url": "https://sso.company.com/login",
            "page_title": "Sign In",
            "viewport": {"width": 1280, "height": 960},
            "tunnel_port": 9222
        }

    Errors:
        404 — Session not found
        400 — Not an rdev session
        409 — Browser view already active
        502 — No browser found on CDP port (Chromium not running or not debuggable)
    """

@router.delete("/api/sessions/{session_id}/browser-view")
async def stop_browser_view(session_id: str):
    """Stop the browser view and close CDP connection + tunnel.

    Response: { "ok": true }
    Errors: 404 — No active browser view

    Side effect: Broadcasts "browser_view_closed" via global WebSocket.
    """

@router.get("/api/sessions/{session_id}/browser-view")
async def get_browser_view_status(session_id: str):
    """Get status of the browser view.

    Response:
        { "active": true, "page_url": "...", "page_title": "...", "created_at": "..." }
        or
        { "active": false }
    """
```

### 3.4 WebSocket Streaming Endpoint

**Location**: `orchestrator/api/ws_browser_view.py`

Unlike the terminal WebSocket (which streams raw PTY bytes), the browser view WebSocket carries:
- **Server → Client**: Binary JPEG frames (from CDP screencast)
- **Client → Server**: JSON input events (mouse, keyboard, scroll)

```python
@router.websocket("/ws/browser-view/{session_id}")
async def ws_browser_view(websocket: WebSocket, session_id: str):
    """WebSocket endpoint for browser view streaming.

    Binary frames (server → client): JPEG image data
    JSON frames (client → server): Input events
    JSON frames (server → client): Metadata (page navigation, resize, error)
    """
    await websocket.accept()

    view = get_active_view(session_id)
    if not view:
        await websocket.send_json({"type": "error", "message": "No active browser view"})
        await websocket.close(code=4004)
        return

    # Task 1: Read CDP screencast frames → send as binary WS frames
    # Task 2: Read client input events → dispatch via CDP
    # Task 3: Monitor CDP connection health

    cdp_reader = asyncio.create_task(_relay_cdp_to_client(view.cdp_ws, websocket))
    client_reader = asyncio.create_task(_relay_client_to_cdp(websocket, view))

    try:
        done, pending = await asyncio.wait(
            [cdp_reader, client_reader],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
    finally:
        for task in [cdp_reader, client_reader]:
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
```

#### CDP Frame Relay

```python
async def _relay_cdp_to_client(
    cdp_ws: websockets.WebSocketClientProtocol,
    client_ws: WebSocket,
) -> None:
    """Read CDP messages, extract screencast frames, forward to client."""
    async for raw_msg in cdp_ws:
        msg = json.loads(raw_msg)

        if msg.get("method") == "Page.screencastFrame":
            params = msg["params"]
            # Decode base64 JPEG frame
            frame_data = base64.b64decode(params["data"])
            session_id = params["sessionId"]

            # Acknowledge the frame so CDP sends the next one
            await cdp_ws.send(json.dumps({
                "id": _next_id(),
                "method": "Page.screencastFrameAck",
                "params": {"sessionId": session_id},
            }))

            # Send raw JPEG bytes as binary WebSocket frame
            await client_ws.send_bytes(frame_data)

        elif msg.get("method") == "Page.frameNavigated":
            # Notify client of URL change
            url = msg["params"]["frame"].get("url", "")
            await client_ws.send_json({
                "type": "navigate",
                "url": url,
            })
```

## 4. Frontend Components

### 4.1 BrowserView Overlay Component

**Location**: `frontend/src/components/browser/BrowserView.tsx`

The BrowserView follows the same PiP overlay pattern as `InteractiveCLI.tsx` — a floating, resizable overlay on the SessionDetailPage. Instead of an xterm.js terminal, it renders a `<canvas>` element that displays JPEG frames from the WebSocket.

```tsx
interface Props {
  sessionId: string
  minimized?: boolean
  onMinimizedChange?: (minimized: boolean) => void
  onClose: () => void
}

export default function BrowserView({ sessionId, minimized, onMinimizedChange, onClose }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const wsRef = useRef<WebSocket | null>(null)
  const [pageUrl, setPageUrl] = useState('')
  const [isExpanded, setIsExpanded] = useState(false)
  const [connected, setConnected] = useState(false)

  // Connect WebSocket on mount
  useEffect(() => {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
    const ws = new WebSocket(`${proto}//${location.host}/ws/browser-view/${sessionId}`)
    ws.binaryType = 'arraybuffer'
    wsRef.current = ws

    ws.onmessage = (event) => {
      if (event.data instanceof ArrayBuffer) {
        // Binary frame — JPEG image
        drawFrame(event.data)
      } else {
        // JSON message
        const msg = JSON.parse(event.data)
        if (msg.type === 'navigate') setPageUrl(msg.url)
        if (msg.type === 'error') console.error('BrowserView:', msg.message)
      }
    }

    ws.onopen = () => setConnected(true)
    ws.onclose = () => { setConnected(false); onClose() }

    return () => ws.close()
  }, [sessionId])

  // Draw JPEG frame on canvas
  const drawFrame = (jpegData: ArrayBuffer) => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    const blob = new Blob([jpegData], { type: 'image/jpeg' })
    const url = URL.createObjectURL(blob)
    const img = new Image()
    img.onload = () => {
      canvas.width = img.width
      canvas.height = img.height
      ctx.drawImage(img, 0, 0)
      URL.revokeObjectURL(url)
    }
    img.src = url
  }

  // ... mouse/keyboard event handlers (see section 4.2)
  // ... overlay chrome (titlebar, controls — same pattern as InteractiveCLI)
}
```

### 4.2 Input Event Handling

Mouse and keyboard events on the canvas are translated to the WebSocket JSON protocol. Coordinates are scaled from canvas display size to the actual browser viewport size.

```tsx
const sendMouseEvent = (e: React.MouseEvent, type: string) => {
  const canvas = canvasRef.current
  if (!canvas || !wsRef.current) return

  const rect = canvas.getBoundingClientRect()
  // Scale from display coordinates to browser viewport coordinates
  const scaleX = canvas.width / rect.width
  const scaleY = canvas.height / rect.height
  const x = (e.clientX - rect.left) * scaleX
  const y = (e.clientY - rect.top) * scaleY

  wsRef.current.send(JSON.stringify({
    type: 'mouse',
    event: type,
    x: Math.round(x),
    y: Math.round(y),
    button: ['left', 'middle', 'right'][e.button] || 'left',
    clickCount: e.detail,
    modifiers: getModifiers(e),
  }))
}

const sendKeyEvent = (e: React.KeyboardEvent, type: string) => {
  if (!wsRef.current) return

  // Prevent browser default for keys we're forwarding
  e.preventDefault()

  wsRef.current.send(JSON.stringify({
    type: 'key',
    event: type,    // "keyDown" | "keyUp"
    key: e.key,
    code: e.code,
    text: type === 'keyDown' && e.key.length === 1 ? e.key : '',
    modifiers: getModifiers(e),
  }))
}

function getModifiers(e: React.MouseEvent | React.KeyboardEvent): number {
  let m = 0
  if (e.altKey) m |= 1
  if (e.ctrlKey) m |= 2
  if (e.metaKey) m |= 4
  if (e.shiftKey) m |= 8
  return m
}
```

The canvas captures events via:

```tsx
<canvas
  ref={canvasRef}
  tabIndex={0}
  style={{ width: '100%', height: '100%', objectFit: 'contain', cursor: 'default' }}
  onMouseDown={(e) => sendMouseEvent(e, 'mousePressed')}
  onMouseUp={(e) => sendMouseEvent(e, 'mouseReleased')}
  onMouseMove={(e) => sendMouseEvent(e, 'mouseMoved')}
  onKeyDown={(e) => sendKeyEvent(e, 'keyDown')}
  onKeyUp={(e) => sendKeyEvent(e, 'keyUp')}
  onWheel={(e) => sendScrollEvent(e)}
  onContextMenu={(e) => e.preventDefault()}
/>
```

### 4.3 SessionDetailPage Integration

The "View Browser" button appears in the SessionDetailPage footer alongside the existing "Interactive CLI" badge. The lifecycle mirrors the InteractiveCLI pattern:

```tsx
// State
const [browserViewActive, setBrowserViewActive] = useState(false)
const [showBrowserView, setShowBrowserView] = useState(false)

// Footer button
{isRdev && (
  <button
    className="sd-browser-view-btn"
    onClick={handleStartBrowserView}
    disabled={browserViewActive}
  >
    {browserViewActive ? 'Browser View Active' : 'View Browser'}
  </button>
)}

// Overlay (rendered inside terminal container, same as InteractiveCLI)
{browserViewActive && showBrowserView && (
  <BrowserView
    sessionId={id!}
    minimized={bvMinimized}
    onMinimizedChange={setBvMinimized}
    onClose={() => { setBrowserViewActive(false); setShowBrowserView(false) }}
  />
)}
```

### 4.4 AppContext WebSocket Events

The global WebSocket listener in `AppContext.tsx` handles browser view events, mirroring the interactive CLI pattern:

```tsx
case 'browser_view_started':
  setBrowserViewSessions(prev => new Set(prev).add(msg.session_id))
  break
case 'browser_view_closed':
  setBrowserViewSessions(prev => {
    const next = new Set(prev)
    next.delete(msg.session_id)
    return next
  })
  break
```

## 5. WebSocket Message Protocol

### 5.1 Server → Client

| Frame Type | Content | Description |
|-----------|---------|-------------|
| Binary | Raw JPEG bytes | Screencast frame (typically 30-80 KB at quality=60) |
| Text/JSON | `{"type": "navigate", "url": "..."}` | Page URL changed |
| Text/JSON | `{"type": "metadata", "title": "...", "url": "..."}` | Page metadata update |
| Text/JSON | `{"type": "error", "message": "..."}` | Error (CDP disconnect, etc.) |
| Text/JSON | `{"type": "closed", "reason": "..."}` | Browser view ended |

### 5.2 Client → Server

| Frame Type | Content | Description |
|-----------|---------|-------------|
| Text/JSON | `{"type": "mouse", "event": "mousePressed", "x": N, "y": N, "button": "left", ...}` | Mouse event |
| Text/JSON | `{"type": "key", "event": "keyDown", "key": "a", "code": "KeyA", "text": "a", "modifiers": 0}` | Key event |
| Text/JSON | `{"type": "scroll", "x": N, "y": N, "deltaX": N, "deltaY": N}` | Scroll/wheel event |
| Text/JSON | `{"type": "quality", "quality": 80}` | Adjust JPEG quality dynamically |

### 5.3 Frame Rate and Bandwidth

CDP `Page.startScreencast` is self-pacing: it sends a frame, waits for `screencastFrameAck`, then sends the next. This natural backpressure prevents flooding. Typical performance:

- **Frame rate**: 10-30 fps depending on page activity (CDP only sends frames when content changes)
- **Frame size**: 30-80 KB at quality=60 for 1280x960 (mostly static login pages are very small)
- **Bandwidth**: ~1-3 MB/s during active interaction, near-zero when idle

## 6. API Contracts

### 6.1 Start Browser View

```
POST /api/sessions/{session_id}/browser-view
Content-Type: application/json

Request:
{
    "cdp_port": 9222,           // CDP debug port on rdev (required)
    "quality": 60,              // JPEG quality 1-100 (optional, default 60)
    "max_width": 1280,          // Max frame width (optional, default 1280)
    "max_height": 960           // Max frame height (optional, default 960)
}

Response (200):
{
    "ok": true,
    "page_url": "https://sso.company.com/login",
    "page_title": "Sign In - Company SSO",
    "viewport": {"width": 1280, "height": 800},
    "tunnel_port": 9222,
    "targets": [
        {"id": "ABC123", "title": "Sign In", "url": "https://sso.company.com/login"}
    ]
}

Errors:
    404: Session not found
    400: Not an rdev session / invalid port
    409: Browser view already active for this session
    502: No browser found on CDP port
    504: CDP connection timeout
```

### 6.2 Stop Browser View

```
DELETE /api/sessions/{session_id}/browser-view

Response (200):
{ "ok": true }

Errors:
    404: No active browser view for this session
```

### 6.3 Get Browser View Status

```
GET /api/sessions/{session_id}/browser-view

Response (200) — active:
{
    "active": true,
    "page_url": "https://sso.company.com/login",
    "page_title": "Sign In",
    "created_at": "2026-03-01T10:30:00Z",
    "tunnel_port": 9222
}

Response (200) — inactive:
{ "active": false }
```

### 6.4 WebSocket

```
WS /ws/browser-view/{session_id}

See section 5 for message protocol.
```

## 7. Error Handling

### 7.1 No Browser Found

When the worker hasn't launched a browser, or Chromium wasn't started with `--remote-debugging-port`:

- **API response**: 502 with `"No debuggable browser found on CDP port 9222"`
- **User action**: Ensure the Playwright worker launches with CDP enabled. The worker prompt will include instructions for this.

### 7.2 CDP Disconnect

If the browser closes (user completes login, navigates away, or Playwright closes it):

- CDP WebSocket closes → backend detects `ConnectionClosed`
- Backend sends `{"type": "closed", "reason": "browser_closed"}` to dashboard WebSocket
- Frontend shows "Browser closed" message and removes the overlay
- SSH tunnel is cleaned up

### 7.3 SSH Tunnel Failure

If the SSH tunnel to the CDP port fails to establish:

- `create_tunnel()` returns `(False, {"error": "..."})` — same as existing tunnel error handling
- API returns 502 with the tunnel error message
- No partial state is left behind (cleanup on failure)

### 7.4 Stale Session

If the browser view session refers to a worker session that has been deleted or stopped:

- Session deletion handler calls `stop_browser_view(session_id)` — same pattern as `close_interactive_cli(session_id)`
- Cleans up CDP connection and SSH tunnel

### 7.5 Multiple Tabs/Pages

CDP discovery may find multiple page targets. The API returns all targets but connects to the first one by default. Future enhancement: allow selecting a specific target by ID.

### 7.6 Latency

The screencast path introduces latency:

- **CDP screencast**: ~30-50ms per frame (CDP internal rendering + JPEG encoding)
- **SSH tunnel**: ~1-5ms (local to rdev, typically same datacenter)
- **WebSocket relay**: ~1ms (local loopback)
- **Canvas rendering**: ~5-10ms (JPEG decode + draw)
- **Total**: ~40-70ms perceived latency

This is acceptable for auth page interaction (clicking buttons, typing credentials) but would not be suitable for real-time gaming or video.

## 8. Implementation Order

### Phase 1: Backend Core

1. `orchestrator/browser/__init__.py` — Package init
2. `orchestrator/browser/cdp_proxy.py` — CDP connection, screencast, input dispatch
3. `orchestrator/api/routes/browser_view.py` — REST API endpoints (start/stop/status)
4. `orchestrator/api/ws_browser_view.py` — WebSocket streaming endpoint
5. Wire routes into `orchestrator/api/app.py`
6. Integrate with existing tunnel infrastructure (`create_tunnel` / `close_tunnel`)

### Phase 2: Frontend

7. `frontend/src/components/browser/BrowserView.tsx` — Canvas overlay component
8. `frontend/src/components/browser/BrowserView.css` — Overlay styles (mirror InteractiveCLI pattern)
9. `SessionDetailPage.tsx` — "View Browser" button, overlay toggle
10. `AppContext.tsx` — Track `browserViewSessions` set from WebSocket events

### Phase 3: Worker Integration

11. Worker prompt update — document how to launch Playwright with CDP port exposed
12. `orch-browser-view` CLI tool — worker can call to signal browser is ready
13. Auto-detection fallback — scan for Chrome processes with `--remote-debugging-port`

### Phase 4: Polish

14. Quality slider in the BrowserView overlay (adjust JPEG quality dynamically)
15. Connection status indicator (connecting/connected/disconnected)
16. Reconnection logic (if CDP drops, offer "Reconnect" button)
17. Session cleanup hooks — stop browser view on session delete/stop
18. Tests: CDP proxy unit tests (mock WebSocket), API endpoint tests, frontend component tests

## 9. Security Considerations

- **CDP access**: The CDP port is only accessible via SSH tunnel — never exposed directly. The tunnel is scoped to the specific rdev host.
- **Input isolation**: Input events are only dispatched to the CDP target that was explicitly connected. No cross-page or cross-browser leakage.
- **No credential capture**: The browser view shows the same pixels as looking at the screen directly. Password fields render as dots (browser default). The JPEG frames do not contain plaintext credentials.
- **Tunnel cleanup**: CDP tunnels are cleaned up on session deletion, browser close, and server shutdown — same lifecycle as existing port-forward tunnels.

## 10. Alternatives Considered

### A. VNC/RDP to the rdev desktop

Stream the entire rdev desktop via VNC. Rejected: heavyweight (requires VNC server install), shows the entire desktop instead of just the browser, high bandwidth.

### B. Port-tunnel the website

Tunnel the web app's port and access via `localhost`. Rejected: breaks OAuth redirects, cookie domains, and CORS (this is the problem statement).

### C. Playwright screenshot polling

Periodically take Playwright screenshots and display in the dashboard. Rejected: no interactivity (can't click or type), high latency (polling interval), Playwright is blocked waiting for user action so it can't take screenshots.

### D. noVNC for the browser window only

Use noVNC to stream just the browser window. Rejected: requires X11/Xvfb setup on rdev, complex windowing integration, CDP screencast is simpler and purpose-built for this use case.

### E. Puppeteer's built-in screencast

Use Puppeteer instead of raw CDP. Rejected: adds a large dependency (Puppeteer) when we only need two CDP methods (`Page.startScreencast` and `Input.dispatch*`). The raw CDP protocol is simple enough to use directly via `websockets`.

---

## Summary

The Remote Browser View feature uses Chrome DevTools Protocol (CDP) screencast to stream a remote browser's visual output to the operator's dashboard as JPEG frames over WebSocket, while relaying mouse and keyboard events back through the same channel. The browser stays on rdev with its original URL intact, preserving OAuth redirects, cookies, and CORS. The backend reuses the existing SSH tunnel infrastructure for CDP port forwarding and follows the same REST + WebSocket patterns as the Interactive CLI feature. The frontend renders frames on a `<canvas>` element in a PiP overlay, mirroring the `InteractiveCLI` component's layout and lifecycle. CDP's built-in frame acknowledgment provides natural backpressure, keeping bandwidth reasonable (~1-3 MB/s active, near-zero idle) with acceptable latency (~40-70ms) for auth page interaction.
