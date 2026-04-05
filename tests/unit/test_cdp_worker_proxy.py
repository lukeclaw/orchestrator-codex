"""Unit tests for per-worker CDP proxy."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchestrator.browser.cdp_worker_proxy import (
    PROXY_PORT_BASE,
    PROXY_PORT_RANGE,
    CDPProxyInfo,
    _build_process_request,
    _filter_get_targets_response,
    _filtering_relay,
    _session_preferred_port,
    _should_drop_target_event,
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


class TestSessionPreferredPort:
    def test_deterministic(self):
        """Same session_id always produces the same port."""
        assert _session_preferred_port("sess-abc") == _session_preferred_port("sess-abc")

    def test_in_range(self):
        """Returned port falls within [PROXY_PORT_BASE, PROXY_PORT_BASE + PROXY_PORT_RANGE)."""
        for sid in ["a", "b", "sess-1", "sess-2", "x" * 200]:
            port = _session_preferred_port(sid)
            assert PROXY_PORT_BASE <= port < PROXY_PORT_BASE + PROXY_PORT_RANGE

    def test_different_sessions_differ(self):
        """Different session IDs (usually) produce different ports."""
        ports = {_session_preferred_port(f"sess-{i}") for i in range(50)}
        # With 50 sessions over a 10k range, collisions are possible but
        # we should get at least 40 distinct ports.
        assert len(ports) > 40


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

    @patch("orchestrator.browser.cdp_worker_proxy.threading.Thread")
    @patch("orchestrator.browser.cdp_worker_proxy.find_available_port", return_value=25000)
    def test_start_uses_session_derived_port(self, mock_find_port, mock_thread_cls):
        """start_cdp_proxy passes the session-derived preferred port, not the base."""
        mock_thread = MagicMock()
        mock_thread.is_alive.return_value = False
        mock_thread_cls.return_value = mock_thread

        def fake_start():
            info = mock_thread_cls.call_args[1]["args"][0]
            ready = mock_thread_cls.call_args[1]["args"][1]
            info._thread = mock_thread
            ready.set()

        mock_thread.start.side_effect = fake_start

        start_cdp_proxy("sess-xyz", chrome_port=9222)
        expected_preferred = _session_preferred_port("sess-xyz")
        mock_find_port.assert_called_once_with(expected_preferred)


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


class TestShouldDropTargetEvent:
    """Test the CDP target event filtering logic (exclusion-set based)."""

    def test_drops_hidden_page_created(self):
        msg = {
            "method": "Target.targetCreated",
            "params": {"targetInfo": {"type": "page", "targetId": "OTHER"}},
        }
        assert _should_drop_target_event(msg, {"OTHER", "ANOTHER"}) is True

    def test_keeps_own_page_created(self):
        msg = {
            "method": "Target.targetCreated",
            "params": {"targetInfo": {"type": "page", "targetId": "MINE"}},
        }
        assert _should_drop_target_event(msg, {"OTHER"}) is False

    def test_keeps_new_page_not_in_hidden_set(self):
        """Pages created by this worker (not in hidden set) pass through."""
        msg = {
            "method": "Target.targetCreated",
            "params": {"targetInfo": {"type": "page", "targetId": "NEW_TAB"}},
        }
        assert _should_drop_target_event(msg, {"OTHER"}) is False

    def test_keeps_non_page_target(self):
        msg = {
            "method": "Target.targetCreated",
            "params": {"targetInfo": {"type": "service_worker", "targetId": "SW1"}},
        }
        assert _should_drop_target_event(msg, {"OTHER"}) is False

    def test_drops_hidden_attached_to_target(self):
        msg = {
            "method": "Target.attachedToTarget",
            "params": {"targetInfo": {"type": "page", "targetId": "OTHER"}},
        }
        assert _should_drop_target_event(msg, {"OTHER"}) is True

    def test_drops_hidden_target_info_changed(self):
        msg = {
            "method": "Target.targetInfoChanged",
            "params": {"targetInfo": {"type": "page", "targetId": "OTHER"}},
        }
        assert _should_drop_target_event(msg, {"OTHER"}) is True

    def test_drops_hidden_target_destroyed_and_removes_from_set(self):
        hidden = {"OTHER", "ANOTHER"}
        msg = {
            "method": "Target.targetDestroyed",
            "params": {"targetId": "OTHER"},
        }
        assert _should_drop_target_event(msg, hidden) is True
        assert "OTHER" not in hidden  # Removed after destruction

    def test_keeps_own_target_destroyed(self):
        msg = {
            "method": "Target.targetDestroyed",
            "params": {"targetId": "MINE"},
        }
        assert _should_drop_target_event(msg, {"OTHER"}) is False

    def test_keeps_unrelated_method(self):
        msg = {"method": "Page.frameNavigated", "params": {}}
        assert _should_drop_target_event(msg, {"OTHER"}) is False

    def test_no_op_with_empty_hidden_set(self):
        msg = {
            "method": "Target.targetCreated",
            "params": {"targetInfo": {"type": "page", "targetId": "ANY"}},
        }
        assert _should_drop_target_event(msg, set()) is False


class TestFilterGetTargetsResponse:
    """Test filtering of Target.getTargets responses."""

    def test_filters_hidden_pages(self):
        msg = {
            "id": 1,
            "result": {
                "targetInfos": [
                    {"type": "page", "targetId": "MINE"},
                    {"type": "page", "targetId": "OTHER"},
                    {"type": "service_worker", "targetId": "SW1"},
                ]
            },
        }
        result = _filter_get_targets_response(msg, {"OTHER"})
        infos = result["result"]["targetInfos"]
        assert len(infos) == 2
        assert infos[0]["targetId"] == "MINE"
        assert infos[1]["targetId"] == "SW1"

    def test_no_op_without_target_infos(self):
        msg = {"id": 1, "result": {"something": "else"}}
        result = _filter_get_targets_response(msg, {"OTHER"})
        assert result == msg

    def test_no_op_with_empty_hidden_set(self):
        msg = {
            "id": 1,
            "result": {
                "targetInfos": [
                    {"type": "page", "targetId": "A"},
                    {"type": "page", "targetId": "B"},
                ]
            },
        }
        result = _filter_get_targets_response(msg, set())
        assert len(result["result"]["targetInfos"]) == 2

    def test_preserves_original_msg(self):
        """Filtering should not mutate the original message dict."""
        original_infos = [
            {"type": "page", "targetId": "MINE"},
            {"type": "page", "targetId": "OTHER"},
        ]
        msg = {"id": 1, "result": {"targetInfos": original_infos}}
        _filter_get_targets_response(msg, {"OTHER"})
        assert len(original_infos) == 2


class TestFilteringRelay:
    """Test the full filtering relay coroutine."""

    @pytest.mark.asyncio
    async def test_drops_hidden_page_events(self):
        """Messages about hidden (other workers') pages are dropped."""
        hidden = {"OTHER", "ANOTHER"}
        messages = [
            json.dumps(
                {
                    "method": "Target.targetCreated",
                    "params": {"targetInfo": {"type": "page", "targetId": "MINE"}},
                }
            ),
            json.dumps(
                {
                    "method": "Target.targetCreated",
                    "params": {"targetInfo": {"type": "page", "targetId": "OTHER"}},
                }
            ),
            json.dumps(
                {
                    "method": "Target.attachedToTarget",
                    "params": {"targetInfo": {"type": "page", "targetId": "MINE"}},
                }
            ),
            json.dumps(
                {
                    "method": "Target.attachedToTarget",
                    "params": {"targetInfo": {"type": "page", "targetId": "ANOTHER"}},
                }
            ),
        ]

        src = AsyncIteratorMock(messages)
        dst = AsyncMock()
        await _filtering_relay(src, dst, hidden)

        # Only the two "MINE" messages should be forwarded
        assert dst.send.call_count == 2
        sent = [call.args[0] for call in dst.send.call_args_list]
        for s in sent:
            assert "MINE" in s

    @pytest.mark.asyncio
    async def test_passes_new_pages_created_by_worker(self):
        """Pages created by this worker (not in hidden set) pass through."""
        hidden = {"OTHER"}
        messages = [
            json.dumps(
                {
                    "method": "Target.targetCreated",
                    "params": {"targetInfo": {"type": "page", "targetId": "NEW_TAB"}},
                }
            ),
            json.dumps(
                {
                    "method": "Target.attachedToTarget",
                    "params": {"targetInfo": {"type": "page", "targetId": "NEW_TAB"}},
                }
            ),
        ]

        src = AsyncIteratorMock(messages)
        dst = AsyncMock()
        await _filtering_relay(src, dst, hidden)

        assert dst.send.call_count == 2

    @pytest.mark.asyncio
    async def test_passes_non_page_targets(self):
        """Service workers and other non-page targets are always forwarded."""
        messages = [
            json.dumps(
                {
                    "method": "Target.targetCreated",
                    "params": {"targetInfo": {"type": "service_worker", "targetId": "SW1"}},
                }
            ),
        ]

        src = AsyncIteratorMock(messages)
        dst = AsyncMock()
        await _filtering_relay(src, dst, {"OTHER"})

        assert dst.send.call_count == 1

    @pytest.mark.asyncio
    async def test_passes_non_target_messages(self):
        """Regular CDP messages like Page.frameNavigated pass through."""
        messages = [
            json.dumps({"method": "Page.frameNavigated", "params": {"url": "https://example.com"}}),
            json.dumps({"id": 42, "result": {"data": "ok"}}),
        ]

        src = AsyncIteratorMock(messages)
        dst = AsyncMock()
        await _filtering_relay(src, dst, {"OTHER"})

        assert dst.send.call_count == 2

    @pytest.mark.asyncio
    async def test_filters_get_targets_response(self):
        """Target.getTargets responses have their targetInfos filtered."""
        messages = [
            json.dumps(
                {
                    "id": 5,
                    "result": {
                        "targetInfos": [
                            {"type": "page", "targetId": "MINE"},
                            {"type": "page", "targetId": "OTHER"},
                            {"type": "browser", "targetId": "BROWSER"},
                        ]
                    },
                }
            ),
        ]

        src = AsyncIteratorMock(messages)
        dst = AsyncMock()
        await _filtering_relay(src, dst, {"OTHER"})

        assert dst.send.call_count == 1
        sent = json.loads(dst.send.call_args[0][0])
        infos = sent["result"]["targetInfos"]
        assert len(infos) == 2
        ids = {t["targetId"] for t in infos}
        assert ids == {"MINE", "BROWSER"}

    @pytest.mark.asyncio
    async def test_passes_binary_messages(self):
        """Binary WebSocket messages pass through untouched."""
        messages = [b"\x00\x01\x02"]

        src = AsyncIteratorMock(messages)
        dst = AsyncMock()
        await _filtering_relay(src, dst, {"OTHER"})

        assert dst.send.call_count == 1
        assert dst.send.call_args[0][0] == b"\x00\x01\x02"


class AsyncIteratorMock:
    """Mock async iterator for simulating a WebSocket source."""

    def __init__(self, messages):
        self._messages = list(messages)
        self._index = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._index >= len(self._messages):
            raise StopAsyncIteration
        msg = self._messages[self._index]
        self._index += 1
        return msg
