"""Unit tests for CDP proxy module."""

import json
from unittest.mock import AsyncMock, patch

import pytest
from websockets.protocol import State as WsState

from orchestrator.browser.cdp_proxy import (
    BrowserViewSession,
    _active_views,
    cleanup_stale_view,
    dispatch_key_event,
    dispatch_mouse_event,
    dispatch_scroll_event,
    get_active_view,
    handle_client_input,
    is_view_alive,
    relay_cdp_to_client,
    start_browser_view,
    stop_browser_view,
    stop_browser_view_sync,
)


@pytest.fixture(autouse=True)
def clear_registry():
    """Clear in-memory registry before each test."""
    _active_views.clear()
    yield
    _active_views.clear()


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
    @patch("orchestrator.browser.cdp_proxy.websockets.asyncio.client.connect")
    @patch("orchestrator.browser.cdp_proxy.discover_browser_targets")
    @patch("orchestrator.browser.cdp_proxy.create_tunnel")
    @pytest.mark.asyncio
    async def test_start_browser_view_success(self, mock_tunnel, mock_discover, mock_connect):
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
