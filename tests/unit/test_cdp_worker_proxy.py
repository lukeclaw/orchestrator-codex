"""Unit tests for per-worker CDP proxy."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchestrator.browser.cdp_worker_proxy import (
    CDPProxyInfo,
    _build_process_request,
    _worker_proxies,
    get_proxy_port,
    start_cdp_proxy,
    stop_cdp_proxy,
)


@pytest.fixture(autouse=True)
def clear_registry():
    """Clear proxy registry before and after each test."""
    _worker_proxies.clear()
    yield
    # Stop any proxies that tests may have started
    for sid in list(_worker_proxies.keys()):
        try:
            stop_cdp_proxy(sid)
        except Exception:
            pass
    _worker_proxies.clear()


class TestStartStopProxy:
    @patch("orchestrator.browser.cdp_worker_proxy.find_available_port", return_value=19222)
    @patch("orchestrator.browser.cdp_worker_proxy.threading.Thread")
    def test_start_returns_port(self, mock_thread_cls, mock_find_port):
        """start_cdp_proxy returns a port and registers the proxy."""
        mock_thread = MagicMock()
        mock_thread.is_alive.return_value = False
        mock_thread_cls.return_value = mock_thread

        # Make the thread start call the ready event immediately
        def fake_start():
            info = mock_thread_cls.call_args[1]["args"][0]
            ready = mock_thread_cls.call_args[1]["args"][1]
            info._thread = mock_thread
            ready.set()

        mock_thread.start.side_effect = fake_start

        port = start_cdp_proxy("sess-1", chrome_port=9222)
        assert port == 19222
        assert "sess-1" in _worker_proxies
        assert _worker_proxies["sess-1"].proxy_port == 19222

    @patch("orchestrator.browser.cdp_worker_proxy.find_available_port", return_value=19222)
    @patch("orchestrator.browser.cdp_worker_proxy.threading.Thread")
    def test_stop_returns_true(self, mock_thread_cls, mock_find_port):
        """stop_cdp_proxy returns True when proxy exists."""
        mock_thread = MagicMock()
        mock_thread.is_alive.return_value = False
        mock_thread_cls.return_value = mock_thread

        def fake_start():
            info = mock_thread_cls.call_args[1]["args"][0]
            ready = mock_thread_cls.call_args[1]["args"][1]
            info._thread = mock_thread
            ready.set()

        mock_thread.start.side_effect = fake_start

        start_cdp_proxy("sess-1", chrome_port=9222)
        assert stop_cdp_proxy("sess-1") is True
        assert "sess-1" not in _worker_proxies

    def test_stop_returns_false_when_not_running(self):
        """stop_cdp_proxy returns False when no proxy exists."""
        assert stop_cdp_proxy("nonexistent") is False

    @patch("orchestrator.browser.cdp_worker_proxy.find_available_port", return_value=19222)
    @patch("orchestrator.browser.cdp_worker_proxy.threading.Thread")
    def test_start_idempotent(self, mock_thread_cls, mock_find_port):
        """Calling start twice returns the same port."""
        mock_thread = MagicMock()
        mock_thread.is_alive.return_value = False
        mock_thread_cls.return_value = mock_thread

        def fake_start():
            info = mock_thread_cls.call_args[1]["args"][0]
            ready = mock_thread_cls.call_args[1]["args"][1]
            info._thread = mock_thread
            ready.set()

        mock_thread.start.side_effect = fake_start

        port1 = start_cdp_proxy("sess-1", chrome_port=9222)
        port2 = start_cdp_proxy("sess-1", chrome_port=9222)
        assert port1 == port2
        # Thread should only be started once
        assert mock_thread.start.call_count == 1

    @patch("orchestrator.browser.cdp_worker_proxy.find_available_port", return_value=None)
    def test_start_no_port_raises(self, mock_find_port):
        """start_cdp_proxy raises RuntimeError when no port available."""
        with pytest.raises(RuntimeError, match="No available port"):
            start_cdp_proxy("sess-1", chrome_port=9222)


class TestGetProxyPort:
    def test_returns_none_when_not_running(self):
        assert get_proxy_port("nonexistent") is None

    def test_returns_port_when_running(self):
        info = CDPProxyInfo(session_id="sess-1", target_id="t1", proxy_port=19222, chrome_port=9222)
        _worker_proxies["sess-1"] = info
        assert get_proxy_port("sess-1") == 19222


class TestProcessRequest:
    """Test the HTTP request filtering logic."""

    def _make_info(self, target_id="TARGET_A", proxy_port=19222, chrome_port=9222):
        return CDPProxyInfo(
            session_id="sess-1",
            target_id=target_id,
            proxy_port=proxy_port,
            chrome_port=chrome_port,
        )

    def _make_request(self, path: str):
        req = MagicMock()
        req.path = path
        return req

    @pytest.mark.asyncio
    async def test_json_filters_targets(self):
        """GET /json returns only the worker's target."""
        info = self._make_info(target_id="TARGET_A")
        process_request = _build_process_request(info)

        chrome_targets = [
            {
                "id": "TARGET_A",
                "type": "page",
                "url": "https://example.com",
                "webSocketDebuggerUrl": "ws://localhost:9222/devtools/page/TARGET_A",
                "devtoolsFrontendUrl": (
                    "/devtools/inspector.html?ws=localhost:9222/devtools/page/TARGET_A"
                ),
            },
            {
                "id": "TARGET_B",
                "type": "page",
                "url": "https://other.com",
                "webSocketDebuggerUrl": "ws://localhost:9222/devtools/page/TARGET_B",
            },
            {
                "id": "TARGET_C",
                "type": "page",
                "url": "https://third.com",
                "webSocketDebuggerUrl": "ws://localhost:9222/devtools/page/TARGET_C",
            },
        ]

        with patch(
            "orchestrator.browser.cdp_worker_proxy._proxy_http_to_chrome",
            new_callable=AsyncMock,
            return_value=json.dumps(chrome_targets).encode(),
        ):
            conn = MagicMock()
            req = self._make_request("/json")
            resp = await process_request(conn, req)

        assert resp is not None
        assert resp.status_code == 200
        body = json.loads(resp.body)
        assert len(body) == 1
        assert body[0]["id"] == "TARGET_A"
        # Port should be rewritten to proxy port
        assert ":19222/" in body[0]["webSocketDebuggerUrl"]

    @pytest.mark.asyncio
    async def test_json_list_also_works(self):
        """GET /json/list behaves the same as /json."""
        info = self._make_info(target_id="TARGET_A")
        process_request = _build_process_request(info)

        chrome_targets = [
            {
                "id": "TARGET_A",
                "type": "page",
                "url": "https://example.com",
                "webSocketDebuggerUrl": "ws://localhost:9222/devtools/page/TARGET_A",
            },
        ]

        with patch(
            "orchestrator.browser.cdp_worker_proxy._proxy_http_to_chrome",
            new_callable=AsyncMock,
            return_value=json.dumps(chrome_targets).encode(),
        ):
            conn = MagicMock()
            req = self._make_request("/json/list")
            resp = await process_request(conn, req)

        assert resp is not None
        assert resp.status_code == 200
        body = json.loads(resp.body)
        assert len(body) == 1

    @pytest.mark.asyncio
    async def test_json_creates_tab_on_demand(self):
        """When target is missing from /json, create_browser_tab is called."""
        info = self._make_info(target_id="DEAD_TARGET")
        process_request = _build_process_request(info)

        # First call: target missing; second call: includes new target
        chrome_targets_before = [
            {
                "id": "OTHER",
                "type": "page",
                "url": "about:blank",
                "webSocketDebuggerUrl": "ws://localhost:9222/devtools/page/OTHER",
            },
        ]
        chrome_targets_after = [
            {
                "id": "NEW_TARGET",
                "type": "page",
                "url": "about:blank",
                "webSocketDebuggerUrl": "ws://localhost:9222/devtools/page/NEW_TARGET",
            },
        ]

        call_count = 0

        async def mock_proxy_http(port, path):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                # First two calls: _ensure_target_exists also calls /json
                return json.dumps(chrome_targets_before).encode()
            return json.dumps(chrome_targets_after).encode()

        with (
            patch(
                "orchestrator.browser.cdp_worker_proxy._proxy_http_to_chrome",
                side_effect=mock_proxy_http,
            ),
            patch(
                "orchestrator.browser.cdp_proxy.create_browser_tab",
                new_callable=AsyncMock,
                return_value={"id": "NEW_TARGET", "type": "page", "url": "about:blank"},
            ) as mock_create,
        ):
            conn = MagicMock()
            req = self._make_request("/json")
            resp = await process_request(conn, req)

        assert resp is not None
        assert resp.status_code == 200
        # create_browser_tab should have been called
        mock_create.assert_called_once()
        # info.target_id should be updated
        assert info.target_id == "NEW_TARGET"

    @pytest.mark.asyncio
    async def test_json_version_rewrites_url(self):
        """GET /json/version rewrites webSocketDebuggerUrl port."""
        info = self._make_info(proxy_port=19222)
        process_request = _build_process_request(info)

        version_data = {
            "Browser": "Chrome/120.0",
            "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/browser/abc",
        }

        with patch(
            "orchestrator.browser.cdp_worker_proxy._proxy_http_to_chrome",
            new_callable=AsyncMock,
            return_value=json.dumps(version_data).encode(),
        ):
            conn = MagicMock()
            req = self._make_request("/json/version")
            resp = await process_request(conn, req)

        assert resp is not None
        assert resp.status_code == 200
        body = json.loads(resp.body)
        assert ":19222/" in body["webSocketDebuggerUrl"]
        assert ":9222/" not in body["webSocketDebuggerUrl"]

    @pytest.mark.asyncio
    async def test_json_chrome_down(self):
        """When Chrome is unreachable, /json returns empty list."""
        info = self._make_info()
        process_request = _build_process_request(info)

        with patch(
            "orchestrator.browser.cdp_worker_proxy._proxy_http_to_chrome",
            new_callable=AsyncMock,
            side_effect=Exception("Connection refused"),
        ):
            conn = MagicMock()
            req = self._make_request("/json")
            resp = await process_request(conn, req)

        assert resp is not None
        assert resp.status_code == 200
        body = json.loads(resp.body)
        assert body == []

    @pytest.mark.asyncio
    async def test_json_version_chrome_down(self):
        """When Chrome is unreachable, /json/version returns 502."""
        info = self._make_info()
        process_request = _build_process_request(info)

        with patch(
            "orchestrator.browser.cdp_worker_proxy._proxy_http_to_chrome",
            new_callable=AsyncMock,
            side_effect=Exception("Connection refused"),
        ):
            conn = MagicMock()
            req = self._make_request("/json/version")
            resp = await process_request(conn, req)

        assert resp is not None
        assert resp.status_code == 502

    @pytest.mark.asyncio
    async def test_other_json_endpoints_blocked(self):
        """Other /json/* paths return 403."""
        info = self._make_info()
        process_request = _build_process_request(info)

        conn = MagicMock()
        req = self._make_request("/json/activate/TARGET_A")
        resp = await process_request(conn, req)
        assert resp is not None
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_trailing_slash_normalized(self):
        """Paths with trailing slashes are handled correctly."""
        info = self._make_info()
        process_request = _build_process_request(info)

        version_data = {
            "Browser": "Chrome/120.0",
            "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/browser/abc",
        }

        with patch(
            "orchestrator.browser.cdp_worker_proxy._proxy_http_to_chrome",
            new_callable=AsyncMock,
            return_value=json.dumps(version_data).encode(),
        ):
            conn = MagicMock()
            # Playwright sends /json/version/ with trailing slash
            req = self._make_request("/json/version/")
            resp = await process_request(conn, req)

        assert resp is not None
        assert resp.status_code == 200
        body = json.loads(resp.body)
        assert ":19222/" in body["webSocketDebuggerUrl"]

    @pytest.mark.asyncio
    async def test_devtools_path_returns_none(self):
        """Paths under /devtools/* return None (WebSocket upgrade)."""
        info = self._make_info()
        process_request = _build_process_request(info)

        conn = MagicMock()
        req = self._make_request("/devtools/page/TARGET_A")
        resp = await process_request(conn, req)
        assert resp is None

    @pytest.mark.asyncio
    async def test_json_creates_initial_tab_when_no_target_id(self):
        """When target_id is empty, /json creates a tab on demand."""
        info = self._make_info(target_id="")
        process_request = _build_process_request(info)

        chrome_targets = [
            {
                "id": "NEW_TAB",
                "type": "page",
                "url": "about:blank",
                "webSocketDebuggerUrl": "ws://localhost:9222/devtools/page/NEW_TAB",
            },
        ]

        with (
            patch(
                "orchestrator.browser.cdp_worker_proxy._proxy_http_to_chrome",
                new_callable=AsyncMock,
                return_value=json.dumps(chrome_targets).encode(),
            ),
            patch(
                "orchestrator.browser.cdp_proxy.create_browser_tab",
                new_callable=AsyncMock,
                return_value={"id": "NEW_TAB", "type": "page"},
            ) as mock_create,
        ):
            conn = MagicMock()
            req = self._make_request("/json")
            resp = await process_request(conn, req)

        assert resp is not None
        assert resp.status_code == 200
        mock_create.assert_called_once()
        assert info.target_id == "NEW_TAB"
