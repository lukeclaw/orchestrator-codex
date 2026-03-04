"""Per-worker CDP proxy for local multi-worker browser isolation.

Multiple local workers share one Chrome instance on port 9222. Each worker's
Playwright MCP connects via PLAYWRIGHT_MCP_CDP_ENDPOINT and picks
context.pages()[0] — the first tab. With multiple workers, all Playwright MCPs
fight over the same first tab.

This module provides a lightweight per-worker CDP proxy (unique port each) that
filters Chrome's /json target list so each Playwright MCP only sees its own tab.

Request flow:
    Worker A's Playwright MCP → GET http://localhost:19222/json
      → Proxy → GET http://localhost:9222/json → filter to target_A
      → Returns [target_A only]

    Worker A's Playwright MCP → WS ws://localhost:19222/devtools/page/{target_A}
      → Proxy → WS ws://localhost:9222/devtools/page/{target_A}
      → Bidirectional relay
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import threading
from dataclasses import dataclass, field
from typing import Any

import httpx
import websockets
import websockets.asyncio.client
import websockets.asyncio.server
from websockets.datastructures import Headers
from websockets.http11 import Response

from orchestrator.session.tunnel import find_available_port

logger = logging.getLogger(__name__)


@dataclass
class CDPProxyInfo:
    """Tracks a running per-worker CDP proxy."""

    session_id: str
    target_id: str  # CDP target ID for this worker's tab
    proxy_port: int  # Port proxy listens on
    chrome_port: int  # Chrome's CDP port (9222)
    server: Any = field(default=None, repr=False)
    _loop: asyncio.AbstractEventLoop | None = field(default=None, repr=False)
    _thread: threading.Thread | None = field(default=None, repr=False)


# In-memory registry: session_id -> CDPProxyInfo
_worker_proxies: dict[str, CDPProxyInfo] = {}

PROXY_PORT_BASE = 19222
PROXY_PORT_RANGE = 10000  # Hash into 19222–29221


def _session_preferred_port(session_id: str) -> int:
    """Derive a deterministic preferred port from a session ID.

    Hashing makes the port stable across orchestrator restarts (same
    session_id → same preferred port) without needing persistent storage.
    If the port is occupied, ``find_available_port`` probes upward.
    """
    h = hashlib.sha256(session_id.encode()).digest()
    offset = int.from_bytes(h[:4], "big") % PROXY_PORT_RANGE
    return PROXY_PORT_BASE + offset


async def _proxy_http_to_chrome(chrome_port: int, path: str) -> bytes:
    """Fetch an HTTP endpoint from Chrome's CDP server."""
    url = f"http://localhost:{chrome_port}{path}"
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.content


async def _ensure_target_exists(chrome_port: int, target_id: str) -> str:
    """Ensure the target exists in Chrome, creating a tab if needed.

    Returns the (possibly new) target_id.
    """
    from orchestrator.browser.cdp_proxy import _session_tab_targets

    # Check if the target still exists
    try:
        raw = await _proxy_http_to_chrome(chrome_port, "/json")
        targets = json.loads(raw)
        for t in targets:
            if t.get("id") == target_id and t.get("type") == "page":
                return target_id
    except Exception:
        pass

    # Target missing — create a new tab
    from orchestrator.browser.cdp_proxy import create_browser_tab

    try:
        new_target = await create_browser_tab(chrome_port)
        new_id = new_target.get("id", "")
        logger.info("CDP proxy created new tab %s (old target %s missing)", new_id, target_id)
        # Update the session_tab_targets mapping so browser view can find it
        for sid, tid in list(_session_tab_targets.items()):
            if tid == target_id:
                _session_tab_targets[sid] = new_id
        return new_id
    except Exception as e:
        logger.warning("CDP proxy failed to create replacement tab: %s", e)
        return target_id


def _build_process_request(info: CDPProxyInfo):
    """Build a process_request handler for the websockets server.

    Intercepts HTTP requests to /json endpoints and filters/rewrites them.
    Returns None for WebSocket upgrade requests (/devtools/*) so the
    websockets library handles the upgrade.
    """

    async def process_request(connection: Any, request: Any) -> Response | None:
        path = request.path if hasattr(request, "path") else str(request)
        # Normalize trailing slash so /json/version/ matches /json/version
        path = path.rstrip("/") or "/"

        # /json/version — proxy to Chrome, rewrite webSocketDebuggerUrl port
        if path == "/json/version":
            try:
                raw = await _proxy_http_to_chrome(info.chrome_port, "/json/version")
                data = json.loads(raw)
                ws_url = data.get("webSocketDebuggerUrl", "")
                if ws_url:
                    # Replace port in ws://host:port/... with proxy port
                    data["webSocketDebuggerUrl"] = re.sub(r":\d+", f":{info.proxy_port}", ws_url)
                body = json.dumps(data).encode()
                return Response(
                    200,
                    "OK",
                    Headers([("Content-Type", "application/json")]),
                    body,
                )
            except Exception as e:
                logger.debug("CDP proxy /json/version failed: %s", e)
                return Response(
                    502,
                    "Bad Gateway",
                    Headers([("Content-Type", "text/plain")]),
                    f"Chrome unreachable: {e}".encode(),
                )

        # /json or /json/list — filter to only this worker's target
        if path in ("/json", "/json/list"):
            try:
                raw = await _proxy_http_to_chrome(info.chrome_port, "/json")
                targets = json.loads(raw)

                # Ensure we have a target_id; create tab on demand if needed
                if not info.target_id:
                    from orchestrator.browser.cdp_proxy import create_browser_tab

                    new_target = await create_browser_tab(info.chrome_port)
                    info.target_id = new_target.get("id", "")
                    logger.info(
                        "CDP proxy created initial tab %s for %s",
                        info.target_id,
                        info.session_id,
                    )

                # Filter to only this worker's target
                filtered = [
                    t for t in targets if t.get("id") == info.target_id and t.get("type") == "page"
                ]

                # If target not found, create a new tab on demand
                if not filtered:
                    new_id = await _ensure_target_exists(info.chrome_port, info.target_id)
                    info.target_id = new_id
                    # Re-fetch and filter
                    raw = await _proxy_http_to_chrome(info.chrome_port, "/json")
                    targets = json.loads(raw)
                    filtered = [
                        t
                        for t in targets
                        if t.get("id") == info.target_id and t.get("type") == "page"
                    ]

                # Rewrite webSocketDebuggerUrl ports in each target
                for t in filtered:
                    ws_url = t.get("webSocketDebuggerUrl", "")
                    if ws_url:
                        t["webSocketDebuggerUrl"] = re.sub(r":\d+", f":{info.proxy_port}", ws_url)
                    devtools_url = t.get("devtoolsFrontendUrl", "")
                    if devtools_url:
                        t["devtoolsFrontendUrl"] = re.sub(
                            r":\d+", f":{info.proxy_port}", devtools_url
                        )

                body = json.dumps(filtered).encode()
                return Response(
                    200,
                    "OK",
                    Headers([("Content-Type", "application/json")]),
                    body,
                )
            except Exception as e:
                logger.debug("CDP proxy /json failed: %s", e)
                return Response(
                    200,
                    "OK",
                    Headers([("Content-Type", "application/json")]),
                    b"[]",
                )

        # Other /json/* endpoints — block them
        if path.startswith("/json"):
            return Response(
                403,
                "Forbidden",
                Headers([("Content-Type", "text/plain")]),
                b"Blocked by CDP proxy",
            )

        # /devtools/* — return None to let WebSocket upgrade proceed
        return None

    return process_request


async def _relay(src: Any, dst: Any) -> None:
    """Relay messages from src WebSocket to dst WebSocket."""
    try:
        async for msg in src:
            await dst.send(msg)
    except websockets.exceptions.ConnectionClosed:
        pass


def _build_ws_handler(info: CDPProxyInfo):
    """Build a WebSocket handler that relays to Chrome's CDP WebSocket."""

    async def handler(ws: Any) -> None:
        # Extract the path from the WebSocket request
        path = ws.request.path if hasattr(ws, "request") and ws.request else ""
        chrome_url = f"ws://localhost:{info.chrome_port}{path}"

        try:
            async with websockets.asyncio.client.connect(
                chrome_url,
                max_size=16 * 1024 * 1024,
                open_timeout=10,
            ) as chrome_ws:
                # Bidirectional relay
                to_chrome = asyncio.create_task(_relay(ws, chrome_ws))
                to_client = asyncio.create_task(_relay(chrome_ws, ws))

                # Wait for either direction to finish
                done, pending = await asyncio.wait(
                    [to_chrome, to_client],
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
                    try:
                        await task
                    except (asyncio.CancelledError, Exception):
                        pass
        except Exception as e:
            logger.debug("CDP proxy WS relay failed for %s: %s", path, e)

    return handler


async def _run_proxy_server(info: CDPProxyInfo, ready: threading.Event) -> None:
    """Run the proxy server in an asyncio event loop."""
    process_request = _build_process_request(info)
    handler = _build_ws_handler(info)

    try:
        async with websockets.asyncio.server.serve(
            handler,
            "",  # Bind all interfaces (IPv4 + IPv6) so ::1 and 127.0.0.1 both work
            info.proxy_port,
            process_request=process_request,
        ) as server:
            info.server = server
            ready.set()
            # Run until the event loop is stopped
            await asyncio.Future()  # Block forever
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.error("CDP proxy server error for %s: %s", info.session_id, e)
        ready.set()  # Unblock the caller even on error


def _proxy_thread_target(info: CDPProxyInfo, ready: threading.Event) -> None:
    """Thread target: create a new event loop and run the proxy server."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    info._loop = loop
    try:
        loop.run_until_complete(_run_proxy_server(info, ready))
    except Exception:
        pass
    finally:
        loop.close()


def start_cdp_proxy(session_id: str, target_id: str = "", chrome_port: int = 9222) -> int:
    """Start a per-worker CDP proxy. Idempotent.

    Args:
        session_id: The worker session ID.
        target_id: CDP target ID for this worker's tab (optional, will create on demand).
        chrome_port: Chrome's CDP port (default 9222).

    Returns:
        The proxy port number.
    """
    # Idempotent — return existing proxy port
    existing = _worker_proxies.get(session_id)
    if existing is not None:
        return existing.proxy_port

    preferred = _session_preferred_port(session_id)
    port = find_available_port(preferred)
    if port is None:
        raise RuntimeError("No available port for CDP proxy")

    info = CDPProxyInfo(
        session_id=session_id,
        target_id=target_id,
        proxy_port=port,
        chrome_port=chrome_port,
    )

    ready = threading.Event()
    thread = threading.Thread(
        target=_proxy_thread_target,
        args=(info, ready),
        daemon=True,
        name=f"cdp-proxy-{session_id[:8]}",
    )
    info._thread = thread
    thread.start()

    # Wait for the server to bind
    ready.wait(timeout=10.0)

    _worker_proxies[session_id] = info
    logger.info(
        "Started CDP proxy for %s on port %d (chrome=%d, target=%s)",
        session_id,
        port,
        chrome_port,
        target_id or "(on-demand)",
    )
    return port


def stop_cdp_proxy(session_id: str) -> bool:
    """Stop the CDP proxy for a session.

    Returns True if a proxy was stopped, False if none was running.
    """
    info = _worker_proxies.pop(session_id, None)
    if info is None:
        return False

    # Stop the event loop
    loop = info._loop
    if loop is not None and loop.is_running():
        loop.call_soon_threadsafe(loop.stop)

    # Wait for the thread to finish
    thread = info._thread
    if thread is not None and thread.is_alive():
        thread.join(timeout=5.0)

    logger.info("Stopped CDP proxy for %s (port %d)", session_id, info.proxy_port)
    return True


def get_proxy_port(session_id: str) -> int | None:
    """Get the proxy port for a session, or None if no proxy is running."""
    info = _worker_proxies.get(session_id)
    return info.proxy_port if info is not None else None
