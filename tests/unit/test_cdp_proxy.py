"""Unit tests for CDP proxy module."""

import asyncio
import base64
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from websockets.protocol import State as WsState

from orchestrator.browser.cdp_proxy import (
    BrowserViewSession,
    _active_tab_target,
    _active_views,
    _session_tab_targets,
    activate_browser_tab,
    cleanup_stale_view,
    dispatch_key_event,
    dispatch_mouse_event,
    dispatch_scroll_event,
    get_active_view,
    handle_client_input,
    is_view_alive,
    reconnect_cdp,
    relay_cdp_to_client,
    restart_screencast,
    start_browser_view,
    stop_browser_view,
    stop_browser_view_sync,
)


@pytest.fixture(autouse=True)
def clear_registry():
    """Clear in-memory registry before each test."""
    _active_views.clear()
    _session_tab_targets.clear()
    _active_tab_target.clear()
    yield
    _active_views.clear()
    _session_tab_targets.clear()
    _active_tab_target.clear()


def _make_view(
    session_id: str = "test-session", ws_state: WsState = WsState.OPEN
) -> BrowserViewSession:
    """Create a BrowserViewSession with a mock CDP WebSocket."""
    mock_ws = AsyncMock()
    mock_ws.send = AsyncMock()
    mock_ws.close = AsyncMock()
    mock_ws.state = ws_state

    return BrowserViewSession(
        session_id=session_id,
        host="user/rdev-vm",
        cdp_ws=mock_ws,
        tunnel_local_port=9222,
        page_url="https://sso.example.com/login",
        page_title="Sign In",
    )


class TestCdpReaderTasksField:
    """Regression: _cdp_reader_tasks must be tracked on the view so a new
    client connection can cancel an old relay's recv() before starting its own.
    Without this, two concurrent recv() calls crash the websockets library."""

    def test_initialized_empty(self):
        view = _make_view()
        assert view._cdp_reader_tasks == []

    @pytest.mark.asyncio
    async def test_old_reader_tasks_can_be_cancelled(self):
        """Simulate the pattern used by ws_browser_view: cancel+await old tasks."""
        view = _make_view()

        # Simulate a long-running relay task (like async for ws.recv())
        stalled = asyncio.Event()

        async def fake_relay():
            stalled.set()
            await asyncio.sleep(3600)  # "blocked" in recv

        task = asyncio.create_task(fake_relay())
        view._cdp_reader_tasks = [task]

        # Wait until the task is actually running
        await stalled.wait()

        # New client connection cancels old tasks (the pattern from ws_browser_view)
        for t in view._cdp_reader_tasks:
            if not t.done():
                t.cancel()
        for t in view._cdp_reader_tasks:
            if not t.done():
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass

        assert task.cancelled()

        # Now a new relay can safely start its own recv()
        view._cdp_reader_tasks = []


class TestViewRegistry:
    def test_get_active_view_returns_none(self):
        assert get_active_view("nonexistent") is None

    def test_get_active_view_returns_view(self):
        view = _make_view()
        _active_views["test-session"] = view
        assert get_active_view("test-session") is view


class TestIsViewAlive:
    def test_alive_when_open(self):
        view = _make_view("s1", ws_state=WsState.OPEN)
        _active_views["s1"] = view
        assert is_view_alive("s1") is True

    def test_dead_when_closed(self):
        view = _make_view("s1", ws_state=WsState.CLOSED)
        _active_views["s1"] = view
        assert is_view_alive("s1") is False

    def test_false_when_no_view(self):
        assert is_view_alive("nonexistent") is False


class TestCleanupStaleView:
    @patch("orchestrator.browser.cdp_proxy.close_tunnel")
    @pytest.mark.asyncio
    async def test_cleanup_dead_view(self, mock_close_tunnel):
        view = _make_view("s1", ws_state=WsState.CLOSED)
        _active_views["s1"] = view

        result = await cleanup_stale_view("s1")

        assert result is True
        assert "s1" not in _active_views
        assert view.status == "closed"
        mock_close_tunnel.assert_called_once_with(9222, "user/rdev-vm")

    @pytest.mark.asyncio
    async def test_no_cleanup_when_alive(self):
        view = _make_view("s1", ws_state=WsState.OPEN)
        _active_views["s1"] = view

        result = await cleanup_stale_view("s1")

        assert result is False
        assert "s1" in _active_views

    @pytest.mark.asyncio
    async def test_no_cleanup_when_no_view(self):
        result = await cleanup_stale_view("nonexistent")
        assert result is False


class TestStartBrowserView:
    @patch("orchestrator.browser.cdp_proxy.activate_browser_tab", new_callable=AsyncMock)
    @patch("orchestrator.browser.cdp_proxy.websockets.asyncio.client.connect")
    @patch("orchestrator.browser.cdp_proxy.discover_browser_targets")
    @patch("orchestrator.browser.cdp_proxy.create_tunnel")
    @pytest.mark.asyncio
    async def test_start_browser_view_success(
        self, mock_tunnel, mock_discover, mock_connect, mock_activate
    ):
        mock_tunnel.return_value = (True, {"local_port": 9222})
        mock_discover.return_value = [
            {
                "id": "page1",
                "type": "page",
                "title": "Login",
                "url": "https://sso.example.com",
                "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/ABC",
            }
        ]
        mock_ws = AsyncMock()
        mock_ws.send = AsyncMock()

        # _cdp_send_and_wait reads the response via recv(). Return a JSON
        # response whose "id" matches the last sent message so the wait
        # resolves immediately.
        async def fake_recv():
            last_call = mock_ws.send.call_args
            sent_msg = json.loads(last_call[0][0])
            return json.dumps({"id": sent_msg["id"], "result": {}})

        mock_ws.recv = fake_recv

        # websockets.connect() returns a dual-use object (awaitable + context manager).
        # When awaited, it returns the connection. AsyncMock.__await__ works fine,
        # but the return_value must be awaitable too — wrap in a coroutine.
        async def fake_connect(*args, **kwargs):
            return mock_ws

        mock_connect.side_effect = fake_connect

        view = await start_browser_view("session-1", "user/rdev-vm", cdp_port=9222)

        assert view.session_id == "session-1"
        assert view.page_url == "https://sso.example.com"
        assert view.tunnel_local_port == 9222
        assert "session-1" in _active_views
        # Verify CDP setup: Page.enable, dark mode (media + bg), viewport, screencast
        assert mock_ws.send.call_count == 5
        sent_enable = json.loads(mock_ws.send.call_args_list[0][0][0])
        assert sent_enable["method"] == "Page.enable"
        sent_media = json.loads(mock_ws.send.call_args_list[1][0][0])
        assert sent_media["method"] == "Emulation.setEmulatedMedia"
        sent_bg = json.loads(mock_ws.send.call_args_list[2][0][0])
        assert sent_bg["method"] == "Emulation.setDefaultBackgroundColorOverride"
        sent_viewport = json.loads(mock_ws.send.call_args_list[3][0][0])
        assert sent_viewport["method"] == "Emulation.setDeviceMetricsOverride"
        assert sent_viewport["params"]["width"] == 1280
        assert sent_viewport["params"]["height"] == 960
        sent_screencast = json.loads(mock_ws.send.call_args_list[4][0][0])
        assert sent_screencast["method"] == "Page.startScreencast"

    @pytest.mark.asyncio
    async def test_start_duplicate_raises_value_error(self):
        _active_views["session-1"] = _make_view("session-1")

        with pytest.raises(ValueError, match="already active"):
            await start_browser_view("session-1", "user/rdev-vm")

    @patch("orchestrator.browser.cdp_proxy.close_tunnel")
    @patch("orchestrator.browser.cdp_proxy.create_tunnel")
    @pytest.mark.asyncio
    async def test_start_tunnel_failure(self, mock_tunnel, mock_close):
        mock_tunnel.return_value = (False, {"error": "port occupied"})

        with pytest.raises(RuntimeError, match="Failed to create CDP tunnel"):
            await start_browser_view("session-1", "user/rdev-vm")

    @patch("orchestrator.browser.cdp_proxy.close_tunnel")
    @patch("orchestrator.browser.cdp_proxy.discover_browser_targets")
    @patch("orchestrator.browser.cdp_proxy.create_tunnel")
    @pytest.mark.asyncio
    async def test_start_no_browser_found(self, mock_tunnel, mock_discover, mock_close):
        mock_tunnel.return_value = (True, {"local_port": 9222})
        mock_discover.side_effect = Exception("Connection refused")

        with pytest.raises(RuntimeError, match="No browser found"):
            await start_browser_view("session-1", "user/rdev-vm")

        # Verify tunnel was cleaned up
        mock_close.assert_called_once_with(9222, "user/rdev-vm")

    @patch("orchestrator.browser.cdp_proxy.close_tunnel")
    @patch("orchestrator.browser.cdp_proxy.discover_browser_targets")
    @patch("orchestrator.browser.cdp_proxy.create_tunnel")
    @pytest.mark.asyncio
    async def test_start_no_page_targets(self, mock_tunnel, mock_discover, mock_close):
        mock_tunnel.return_value = (True, {"local_port": 9222})
        mock_discover.return_value = []  # No pages

        with pytest.raises(RuntimeError, match="No debuggable pages"):
            await start_browser_view("session-1", "user/rdev-vm")

        mock_close.assert_called_once()


class TestActivateTabOnConnect:
    """Test that start_browser_view activates the tab via HTTP to avoid black frames."""

    @patch("orchestrator.browser.cdp_proxy.activate_browser_tab")
    @patch("orchestrator.browser.cdp_proxy.websockets.asyncio.client.connect")
    @patch("orchestrator.browser.cdp_proxy.discover_browser_targets")
    @patch("orchestrator.browser.cdp_proxy.create_tunnel")
    @pytest.mark.asyncio
    async def test_activates_tab_for_remote(
        self, mock_tunnel, mock_discover, mock_connect, mock_activate
    ):
        """Remote targets get activated before screencast starts."""
        mock_tunnel.return_value = (True, {"local_port": 9222})
        mock_discover.return_value = [
            {
                "id": "page1",
                "type": "page",
                "title": "Login",
                "url": "https://example.com",
                "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/page1",
            }
        ]
        mock_ws = AsyncMock()
        mock_ws.send = AsyncMock()

        async def fake_recv():
            last_call = mock_ws.send.call_args
            sent_msg = json.loads(last_call[0][0])
            return json.dumps({"id": sent_msg["id"], "result": {}})

        mock_ws.recv = fake_recv

        async def fake_connect(*args, **kwargs):
            return mock_ws

        mock_connect.side_effect = fake_connect

        await start_browser_view("sess-activate", "user/rdev-vm", cdp_port=9222)

        mock_activate.assert_awaited_once_with(9222, "page1")
        _active_views.pop("sess-activate", None)

    @patch("orchestrator.browser.cdp_proxy.activate_browser_tab")
    @patch("orchestrator.browser.cdp_proxy.websockets.asyncio.client.connect")
    @patch("orchestrator.browser.cdp_proxy.create_browser_tab")
    @pytest.mark.asyncio
    async def test_activates_tab_for_local_new_tab(
        self, mock_create_tab, mock_connect, mock_activate
    ):
        """Local workers also activate newly created tabs."""
        mock_create_tab.return_value = {
            "id": "new-tab-1",
            "type": "page",
            "title": "",
            "url": "about:blank",
            "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/new-tab-1",
        }
        mock_ws = AsyncMock()
        mock_ws.send = AsyncMock()

        async def fake_recv():
            last_call = mock_ws.send.call_args
            sent_msg = json.loads(last_call[0][0])
            return json.dumps({"id": sent_msg["id"], "result": {}})

        mock_ws.recv = fake_recv

        async def fake_connect(*args, **kwargs):
            return mock_ws

        mock_connect.side_effect = fake_connect

        await start_browser_view("sess-local-act", "localhost", cdp_port=9222)

        mock_activate.assert_awaited_once_with(9222, "new-tab-1")
        _active_views.pop("sess-local-act", None)

    @patch(
        "orchestrator.browser.cdp_proxy.activate_browser_tab",
        side_effect=Exception("fail"),
    )
    @patch("orchestrator.browser.cdp_proxy.websockets.asyncio.client.connect")
    @patch("orchestrator.browser.cdp_proxy.create_browser_tab")
    @pytest.mark.asyncio
    async def test_activation_failure_is_non_fatal(
        self, mock_create_tab, mock_connect, mock_activate
    ):
        """If activate fails, screencast still proceeds (best-effort)."""
        mock_create_tab.return_value = {
            "id": "tab-x",
            "type": "page",
            "title": "",
            "url": "about:blank",
            "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/tab-x",
        }
        mock_ws = AsyncMock()
        mock_ws.send = AsyncMock()

        async def fake_recv():
            last_call = mock_ws.send.call_args
            sent_msg = json.loads(last_call[0][0])
            return json.dumps({"id": sent_msg["id"], "result": {}})

        mock_ws.recv = fake_recv

        async def fake_connect(*args, **kwargs):
            return mock_ws

        mock_connect.side_effect = fake_connect

        # Should NOT raise — activation failure is swallowed
        view = await start_browser_view("sess-fail-act", "localhost", cdp_port=9222)
        assert view.session_id == "sess-fail-act"
        _active_views.pop("sess-fail-act", None)


class TestActivateTabSkipsRedundant:
    """activate_browser_tab skips the CDP call when the target is already active."""

    @patch("orchestrator.browser.cdp_proxy.websockets.asyncio.client.connect")
    @patch("orchestrator.browser.cdp_proxy.httpx.AsyncClient")
    @pytest.mark.asyncio
    async def test_skips_when_already_active(self, mock_http_cls, mock_ws_connect):
        """No CDP call if the target is already the active tab."""
        _active_tab_target[9222] = "tab-already"

        await activate_browser_tab(9222, "tab-already")

        # Should not have made any HTTP or WebSocket calls
        mock_http_cls.assert_not_called()
        mock_ws_connect.assert_not_called()

    @patch("orchestrator.browser.cdp_proxy.websockets.asyncio.client.connect")
    @patch("orchestrator.browser.cdp_proxy.httpx.AsyncClient")
    @pytest.mark.asyncio
    async def test_activates_different_tab(self, mock_http_cls, mock_ws_connect):
        """CDP call happens when switching to a different tab."""
        _active_tab_target[9222] = "tab-old"

        # Mock the HTTP client for /json/version
        # httpx.Response.json() and .raise_for_status() are sync methods
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/browser/abc"
        }
        mock_resp.raise_for_status = MagicMock()
        mock_http.get = AsyncMock(return_value=mock_resp)
        mock_http_cls.return_value = mock_http

        # Mock the WebSocket connection
        mock_ws = AsyncMock()
        mock_ws.send = AsyncMock()
        mock_ws.recv = AsyncMock(return_value=json.dumps({"id": 1, "result": {}}))
        mock_ws.__aenter__ = AsyncMock(return_value=mock_ws)
        mock_ws.__aexit__ = AsyncMock(return_value=False)
        mock_ws_connect.return_value = mock_ws

        await activate_browser_tab(9222, "tab-new")

        # Should have made the CDP call
        mock_ws.send.assert_called_once()
        sent = json.loads(mock_ws.send.call_args[0][0])
        assert sent["method"] == "Target.activateTarget"
        assert sent["params"]["targetId"] == "tab-new"

        # Should have updated tracking
        assert _active_tab_target[9222] == "tab-new"


class TestLocalTabSync:
    """Browser view should sync its tab with the CDP worker proxy so
    Playwright MCP and the dashboard view show the same page."""

    @patch("orchestrator.browser.cdp_proxy.activate_browser_tab", new_callable=AsyncMock)
    @patch("orchestrator.browser.cdp_proxy.websockets.asyncio.client.connect")
    @patch("orchestrator.browser.cdp_proxy._find_target_by_id")
    @pytest.mark.asyncio
    async def test_browser_view_uses_proxy_tab(self, mock_find, mock_connect, mock_activate):
        """When the CDP proxy already has a tab, browser view should use it."""
        from orchestrator.browser.cdp_worker_proxy import CDPProxyInfo, _worker_proxies

        # Set up a CDP proxy with an existing tab
        proxy = CDPProxyInfo(
            session_id="sess-sync",
            target_id="proxy-tab-1",
            proxy_port=19222,
            chrome_port=9222,
        )
        _worker_proxies["sess-sync"] = proxy

        # _find_target_by_id returns the proxy's tab
        mock_find.return_value = {
            "id": "proxy-tab-1",
            "type": "page",
            "title": "Test",
            "url": "https://example.com",
            "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/proxy-tab-1",
        }

        mock_ws = AsyncMock()
        mock_ws.send = AsyncMock()

        async def fake_recv():
            last_call = mock_ws.send.call_args
            sent_msg = json.loads(last_call[0][0])
            return json.dumps({"id": sent_msg["id"], "result": {}})

        mock_ws.recv = fake_recv

        async def fake_connect(*args, **kwargs):
            return mock_ws

        mock_connect.side_effect = fake_connect

        view = await start_browser_view("sess-sync", "localhost", cdp_port=9222)

        assert view.target_id == "proxy-tab-1"
        assert _session_tab_targets["sess-sync"] == "proxy-tab-1"

        _active_views.pop("sess-sync", None)
        _worker_proxies.pop("sess-sync", None)

    @patch("orchestrator.browser.cdp_proxy.activate_browser_tab", new_callable=AsyncMock)
    @patch("orchestrator.browser.cdp_proxy.websockets.asyncio.client.connect")
    @patch("orchestrator.browser.cdp_proxy.create_browser_tab")
    @pytest.mark.asyncio
    async def test_new_tab_syncs_to_proxy(self, mock_create_tab, mock_connect, mock_activate):
        """When browser view creates a new tab, it syncs the ID to the CDP proxy."""
        from orchestrator.browser.cdp_worker_proxy import CDPProxyInfo, _worker_proxies

        # Proxy exists but has no tab yet
        proxy = CDPProxyInfo(
            session_id="sess-sync2",
            target_id="",
            proxy_port=19222,
            chrome_port=9222,
        )
        _worker_proxies["sess-sync2"] = proxy

        mock_create_tab.return_value = {
            "id": "new-tab-99",
            "type": "page",
            "title": "",
            "url": "about:blank",
            "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/new-tab-99",
        }

        mock_ws = AsyncMock()
        mock_ws.send = AsyncMock()

        async def fake_recv():
            last_call = mock_ws.send.call_args
            sent_msg = json.loads(last_call[0][0])
            return json.dumps({"id": sent_msg["id"], "result": {}})

        mock_ws.recv = fake_recv

        async def fake_connect(*args, **kwargs):
            return mock_ws

        mock_connect.side_effect = fake_connect

        view = await start_browser_view("sess-sync2", "localhost", cdp_port=9222)

        assert view.target_id == "new-tab-99"
        assert proxy.target_id == "new-tab-99"
        assert _session_tab_targets["sess-sync2"] == "new-tab-99"

        _active_views.pop("sess-sync2", None)
        _worker_proxies.pop("sess-sync2", None)


class TestRestartScreencast:
    @pytest.mark.asyncio
    async def test_restart_sends_stop_then_start(self):
        """restart_screencast stops and restarts the screencast."""
        view = _make_view()

        await restart_screencast(view)

        assert view.cdp_ws.send.call_count == 2
        sent_stop = json.loads(view.cdp_ws.send.call_args_list[0][0][0])
        assert sent_stop["method"] == "Page.stopScreencast"
        sent_start = json.loads(view.cdp_ws.send.call_args_list[1][0][0])
        assert sent_start["method"] == "Page.startScreencast"
        assert sent_start["params"]["quality"] == 60
        assert sent_start["params"]["maxWidth"] == 1280
        assert sent_start["params"]["maxHeight"] == 960

    @pytest.mark.asyncio
    async def test_restart_survives_stop_failure(self):
        """If stopScreencast fails, startScreencast still runs."""
        view = _make_view()
        call_count = 0

        async def fail_on_first(*args):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("stop failed")

        view.cdp_ws.send = fail_on_first

        # Should not raise — stop failure is swallowed
        await restart_screencast(view)
        assert call_count == 2  # stop (failed) + start


class TestReconnectCdp:
    """Test reconnect_cdp closes old WS and opens a fresh one."""

    @patch("orchestrator.browser.cdp_proxy.websockets.asyncio.client.connect")
    @pytest.mark.asyncio
    async def test_reconnect_replaces_ws_and_restarts_screencast(self, mock_connect):
        """reconnect_cdp closes old WS, opens new one, re-enables events."""
        old_ws = AsyncMock()
        old_ws.state = WsState.OPEN
        view = _make_view()
        view.cdp_ws = old_ws
        view.target_id = "ABC123"

        new_ws = AsyncMock()
        new_ws.send = AsyncMock()

        # _cdp_send_and_wait reads via recv(), return matching response
        async def fake_recv():
            last_call = new_ws.send.call_args
            sent_msg = json.loads(last_call[0][0])
            return json.dumps({"id": sent_msg["id"], "result": {}})

        new_ws.recv = fake_recv

        async def fake_connect(*args, **kwargs):
            return new_ws

        mock_connect.side_effect = fake_connect

        await reconnect_cdp(view)

        # Old WS was closed
        old_ws.close.assert_called_once()

        # view.cdp_ws now points to the new WS
        assert view.cdp_ws is new_ws

        # Verify CDP setup calls on new WS
        sent_methods = [json.loads(call[0][0])["method"] for call in new_ws.send.call_args_list]
        assert "Page.enable" in sent_methods
        assert "Emulation.setEmulatedMedia" in sent_methods
        assert "Emulation.setDefaultBackgroundColorOverride" in sent_methods
        assert "Emulation.setDeviceMetricsOverride" in sent_methods
        assert "Page.startScreencast" in sent_methods

        # Verify correct WS URL was used
        connect_url = mock_connect.call_args[0][0]
        assert "devtools/page/ABC123" in connect_url
        assert "127.0.0.1:9222" in connect_url

        # Verify ping_interval=None was passed
        assert mock_connect.call_args[1].get("ping_interval") is None

    @patch("orchestrator.browser.cdp_proxy.websockets.asyncio.client.connect")
    @pytest.mark.asyncio
    async def test_reconnect_survives_old_ws_close_failure(self, mock_connect):
        """If closing the old WS fails, reconnect still proceeds."""
        old_ws = AsyncMock()
        old_ws.close = AsyncMock(side_effect=Exception("already closed"))
        view = _make_view()
        view.cdp_ws = old_ws
        view.target_id = "DEF456"

        new_ws = AsyncMock()
        new_ws.send = AsyncMock()

        async def fake_recv():
            last_call = new_ws.send.call_args
            sent_msg = json.loads(last_call[0][0])
            return json.dumps({"id": sent_msg["id"], "result": {}})

        new_ws.recv = fake_recv

        async def fake_connect(*args, **kwargs):
            return new_ws

        mock_connect.side_effect = fake_connect

        # Should not raise
        await reconnect_cdp(view)
        assert view.cdp_ws is new_ws


class TestStopBrowserView:
    @pytest.mark.asyncio
    async def test_stop_existing_view(self):
        view = _make_view("session-1")
        _active_views["session-1"] = view

        result = await stop_browser_view("session-1")

        assert result is True
        assert "session-1" not in _active_views
        assert view.status == "closed"
        # Verify CDP commands sent
        view.cdp_ws.send.assert_called()
        view.cdp_ws.close.assert_called()

    @pytest.mark.asyncio
    async def test_stop_nonexistent_returns_false(self):
        result = await stop_browser_view("nonexistent")
        assert result is False

    @patch("orchestrator.browser.cdp_proxy.close_tunnel")
    def test_stop_sync(self, mock_close):
        view = _make_view("session-1")
        _active_views["session-1"] = view

        result = stop_browser_view_sync("session-1")

        assert result is True
        assert "session-1" not in _active_views
        mock_close.assert_called_once_with(9222, "user/rdev-vm")

    @patch("orchestrator.browser.cdp_proxy.close_tunnel")
    def test_stop_sync_nonexistent(self, mock_close):
        result = stop_browser_view_sync("nonexistent")
        assert result is False
        mock_close.assert_not_called()


class TestInputDispatch:
    @pytest.mark.asyncio
    async def test_dispatch_mouse_event(self):
        view = _make_view()

        await dispatch_mouse_event(view, "mousePressed", x=100, y=200, button="left")

        view.cdp_ws.send.assert_called_once()
        sent = json.loads(view.cdp_ws.send.call_args[0][0])
        assert sent["method"] == "Input.dispatchMouseEvent"
        assert sent["params"]["type"] == "mousePressed"
        assert sent["params"]["x"] == 100
        assert sent["params"]["y"] == 200
        assert sent["params"]["button"] == "left"

    @pytest.mark.asyncio
    async def test_dispatch_key_event(self):
        view = _make_view()

        await dispatch_key_event(view, "keyDown", key="a", code="KeyA", text="a")

        view.cdp_ws.send.assert_called_once()
        sent = json.loads(view.cdp_ws.send.call_args[0][0])
        assert sent["method"] == "Input.dispatchKeyEvent"
        assert sent["params"]["type"] == "keyDown"
        assert sent["params"]["key"] == "a"
        assert sent["params"]["text"] == "a"
        assert sent["params"]["windowsVirtualKeyCode"] == ord("A")

    @pytest.mark.asyncio
    async def test_dispatch_enter_key(self):
        """Enter key must include windowsVirtualKeyCode and text='\\r'."""
        view = _make_view()

        await dispatch_key_event(view, "keyDown", key="Enter", code="Enter")

        sent = json.loads(view.cdp_ws.send.call_args[0][0])
        assert sent["params"]["windowsVirtualKeyCode"] == 13
        assert sent["params"]["text"] == "\r"

    @pytest.mark.asyncio
    async def test_dispatch_backspace_key(self):
        """Backspace key must include windowsVirtualKeyCode."""
        view = _make_view()

        await dispatch_key_event(view, "keyDown", key="Backspace", code="Backspace")

        sent = json.loads(view.cdp_ws.send.call_args[0][0])
        assert sent["params"]["windowsVirtualKeyCode"] == 8
        assert "text" not in sent["params"]  # Backspace has no text

    @pytest.mark.asyncio
    async def test_special_key_no_text_on_keyup(self):
        """Enter keyUp should NOT include text (only keyDown generates input)."""
        view = _make_view()

        await dispatch_key_event(view, "keyUp", key="Enter", code="Enter")

        sent = json.loads(view.cdp_ws.send.call_args[0][0])
        assert sent["params"]["windowsVirtualKeyCode"] == 13
        assert "text" not in sent["params"]

    @pytest.mark.asyncio
    async def test_dispatch_period_key_with_keycode(self):
        """Period key must use browser-supplied keyCode (190), not ord('.')=46
        which collides with VK_DELETE and causes the character to be dropped."""
        view = _make_view()

        await dispatch_key_event(view, "keyDown", key=".", code="Period", text=".", key_code=190)

        sent = json.loads(view.cdp_ws.send.call_args[0][0])
        assert sent["params"]["windowsVirtualKeyCode"] == 190
        assert sent["params"]["nativeVirtualKeyCode"] == 190
        assert sent["params"]["text"] == "."

    @pytest.mark.asyncio
    async def test_dispatch_punctuation_keys_with_keycode(self):
        """Various punctuation keys should use browser keyCode, not ASCII."""
        view = _make_view()
        # comma: ASCII 44, correct VK = 188
        await dispatch_key_event(view, "keyDown", key=",", code="Comma", text=",", key_code=188)
        sent = json.loads(view.cdp_ws.send.call_args[0][0])
        assert sent["params"]["windowsVirtualKeyCode"] == 188

    @pytest.mark.asyncio
    async def test_dispatch_key_falls_back_without_keycode(self):
        """Without browser keyCode, falls back to ord(key.upper()) for letters."""
        view = _make_view()

        await dispatch_key_event(view, "keyDown", key="a", code="KeyA", text="a")

        sent = json.loads(view.cdp_ws.send.call_args[0][0])
        assert sent["params"]["windowsVirtualKeyCode"] == ord("A")

    @pytest.mark.asyncio
    async def test_dispatch_special_key_falls_back_without_keycode(self):
        """Without browser keyCode, special keys still use _SPECIAL_KEYS table."""
        view = _make_view()

        await dispatch_key_event(view, "keyDown", key="Enter", code="Enter", key_code=0)

        sent = json.loads(view.cdp_ws.send.call_args[0][0])
        assert sent["params"]["windowsVirtualKeyCode"] == 13
        assert sent["params"]["text"] == "\r"

    @pytest.mark.asyncio
    async def test_dispatch_scroll_event(self):
        view = _make_view()

        await dispatch_scroll_event(view, x=100, y=200, delta_x=0, delta_y=-120)

        view.cdp_ws.send.assert_called_once()
        sent = json.loads(view.cdp_ws.send.call_args[0][0])
        assert sent["method"] == "Input.dispatchMouseEvent"
        assert sent["params"]["type"] == "mouseWheel"
        assert sent["params"]["deltaY"] == -120


class TestHandleClientInput:
    @pytest.mark.asyncio
    async def test_handle_mouse_input(self):
        view = _make_view()

        await handle_client_input(
            view,
            {
                "type": "mouse",
                "event": "mousePressed",
                "x": 50,
                "y": 100,
                "button": "left",
                "clickCount": 1,
                "modifiers": 0,
            },
        )

        view.cdp_ws.send.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_key_input(self):
        view = _make_view()

        await handle_client_input(
            view,
            {
                "type": "key",
                "event": "keyDown",
                "key": "Enter",
                "code": "Enter",
                "text": "",
                "modifiers": 0,
            },
        )

        view.cdp_ws.send.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_key_input_with_keycode(self):
        """handle_client_input forwards browser keyCode for punctuation keys."""
        view = _make_view()

        await handle_client_input(
            view,
            {
                "type": "key",
                "event": "keyDown",
                "key": ".",
                "code": "Period",
                "keyCode": 190,
                "text": ".",
                "modifiers": 0,
            },
        )

        view.cdp_ws.send.assert_called_once()
        sent = json.loads(view.cdp_ws.send.call_args[0][0])
        assert sent["params"]["windowsVirtualKeyCode"] == 190
        assert sent["params"]["text"] == "."

    @pytest.mark.asyncio
    async def test_handle_scroll_input(self):
        view = _make_view()

        await handle_client_input(
            view,
            {
                "type": "scroll",
                "x": 100,
                "y": 200,
                "deltaX": 0,
                "deltaY": -120,
            },
        )

        view.cdp_ws.send.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_quality_change(self):
        view = _make_view()
        view.quality = 60

        await handle_client_input(
            view,
            {
                "type": "quality",
                "quality": 80,
            },
        )

        assert view.quality == 80
        # stopScreencast + startScreencast = 2 calls
        assert view.cdp_ws.send.call_count == 2

    @pytest.mark.asyncio
    async def test_handle_unknown_type_is_noop(self):
        view = _make_view()

        await handle_client_input(view, {"type": "unknown_type"})

        view.cdp_ws.send.assert_not_called()


class _AsyncIterFromList:
    """Async iterator that yields items from a list, then stops."""

    def __init__(self, items):
        self._items = list(items)
        self._index = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._index >= len(self._items):
            raise StopAsyncIteration
        item = self._items[self._index]
        self._index += 1
        return item


class TestRelayCdpToClient:
    @pytest.mark.asyncio
    async def test_relays_screencast_frame(self):
        """Test that screencast frames are decoded and forwarded as binary."""
        import base64

        # Fake JPEG data (just bytes for testing)
        fake_jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 100
        b64_data = base64.b64encode(fake_jpeg).decode()

        frame_msg = json.dumps(
            {
                "method": "Page.screencastFrame",
                "params": {
                    "data": b64_data,
                    "sessionId": "cdp-session-1",
                    "metadata": {},
                },
            }
        )

        # Create a view whose cdp_ws is an async iterable
        view = _make_view()
        view.cdp_ws = _AsyncIterFromList([frame_msg])
        view.cdp_ws.send = AsyncMock()

        send_binary = AsyncMock()
        send_json = AsyncMock()

        await relay_cdp_to_client(view, send_binary, send_json)

        # Verify binary frame was sent
        send_binary.assert_called_once()
        sent_data = send_binary.call_args[0][0]
        assert sent_data == fake_jpeg

        # Verify ack was sent back to CDP
        view.cdp_ws.send.assert_called_once()
        ack = json.loads(view.cdp_ws.send.call_args[0][0])
        assert ack["method"] == "Page.screencastFrameAck"

    @pytest.mark.asyncio
    async def test_relays_navigation_event(self):
        nav_msg = json.dumps(
            {
                "method": "Page.frameNavigated",
                "params": {
                    "frame": {
                        "url": "https://app.example.com/dashboard",
                        "name": "Dashboard",
                    },
                },
            }
        )

        view = _make_view()
        view.cdp_ws = _AsyncIterFromList([nav_msg])
        view.cdp_ws.send = AsyncMock()

        send_binary = AsyncMock()
        send_json = AsyncMock()

        await relay_cdp_to_client(view, send_binary, send_json)

        send_json.assert_called_once()
        msg = send_json.call_args[0][0]
        assert msg["type"] == "navigate"
        assert msg["url"] == "https://app.example.com/dashboard"

    async def test_viewport_override_reasserts_after_frame_delivery(self):
        """When metadata shows unexpected dimensions, re-assert viewport after sending frame."""
        frame_msg = json.dumps(
            {
                "method": "Page.screencastFrame",
                "params": {
                    "data": base64.b64encode(b"fake-jpeg").decode(),
                    "sessionId": "sess1",
                    "metadata": {
                        "deviceWidth": 800,
                        "deviceHeight": 600,
                    },
                },
            }
        )

        view = _make_view()
        view._zoom_percent = 100
        view._last_viewport_fix = 0
        view.cdp_ws = _AsyncIterFromList([frame_msg])
        view.cdp_ws.send = AsyncMock()

        send_binary = AsyncMock()
        send_json = AsyncMock()

        await relay_cdp_to_client(view, send_binary, send_json)

        # Frame must be delivered first (ack + binary), then viewport fix
        sent_calls = view.cdp_ws.send.call_args_list
        methods = [json.loads(c[0][0]).get("method") for c in sent_calls]
        assert methods[0] == "Page.screencastFrameAck"
        assert methods[1] == "Emulation.setDeviceMetricsOverride"
        override = json.loads(sent_calls[1][0][0])
        assert override["params"]["width"] == 1280
        assert override["params"]["height"] == 960
        assert override["params"]["deviceScaleFactor"] == 1
        send_binary.assert_called_once()

    async def test_viewport_override_respects_cooldown(self):
        """Viewport re-assertion fires at most once per second."""
        frame_msg = json.dumps(
            {
                "method": "Page.screencastFrame",
                "params": {
                    "data": base64.b64encode(b"fake-jpeg").decode(),
                    "sessionId": "sess1",
                    "metadata": {"deviceWidth": 800, "deviceHeight": 600},
                },
            }
        )

        view = _make_view()
        view._zoom_percent = 100
        # Pretend we just fixed it (cooldown active)
        import time

        view._last_viewport_fix = time.monotonic()
        view.cdp_ws = _AsyncIterFromList([frame_msg])
        view.cdp_ws.send = AsyncMock()

        send_binary = AsyncMock()
        send_json = AsyncMock()

        await relay_cdp_to_client(view, send_binary, send_json)

        # Should only send ack, no viewport fix (cooldown active)
        sent_calls = view.cdp_ws.send.call_args_list
        methods = [json.loads(c[0][0]).get("method") for c in sent_calls]
        assert methods == ["Page.screencastFrameAck"]
        send_binary.assert_called_once()

    async def test_no_viewport_fix_when_dimensions_match(self):
        """No re-assertion when metadata matches expected viewport."""
        frame_msg = json.dumps(
            {
                "method": "Page.screencastFrame",
                "params": {
                    "data": base64.b64encode(b"fake-jpeg").decode(),
                    "sessionId": "sess1",
                    "metadata": {"deviceWidth": 1280, "deviceHeight": 960},
                },
            }
        )

        view = _make_view()
        view._zoom_percent = 100
        view._last_viewport_fix = 0
        view.cdp_ws = _AsyncIterFromList([frame_msg])
        view.cdp_ws.send = AsyncMock()

        send_binary = AsyncMock()
        send_json = AsyncMock()

        await relay_cdp_to_client(view, send_binary, send_json)

        # Only ack, no viewport fix
        sent_calls = view.cdp_ws.send.call_args_list
        methods = [json.loads(c[0][0]).get("method") for c in sent_calls]
        assert methods == ["Page.screencastFrameAck"]
        send_binary.assert_called_once()
