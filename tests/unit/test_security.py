"""Tests for security hardening: shell quoting, name sanitization, URL validation, keychain."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Session name sanitization
# ---------------------------------------------------------------------------


class TestSanitizeWorkerName:
    def _sanitize(self, name: str) -> str:
        from orchestrator.api.routes.sessions import _sanitize_worker_name

        return _sanitize_worker_name(name)

    def test_plain_name(self):
        assert self._sanitize("my-worker") == "my-worker"

    def test_strips_whitespace(self):
        assert self._sanitize("  hello  ") == "hello"

    def test_strips_leading_trailing_dashes(self):
        assert self._sanitize("--worker--") == "worker"

    def test_replaces_slashes(self):
        assert self._sanitize("path/to\\worker") == "path_to_worker"

    def test_replaces_shell_metacharacters(self):
        assert self._sanitize("a;rm -rf /") == "a_rm -rf _"

    def test_allows_at_colon_dot(self):
        assert self._sanitize("user@host:dir.name") == "user@host:dir.name"

    def test_max_length(self):
        long_name = "a" * 200
        assert len(self._sanitize(long_name)) == 100

    def test_backtick_injection(self):
        result = self._sanitize("`whoami`")
        assert "`" not in result

    def test_dollar_injection(self):
        result = self._sanitize("$(evil)")
        assert "$" not in result
        assert "(" not in result

    def test_pipe_semicolon_ampersand(self):
        result = self._sanitize("a|b;c&d")
        assert "|" not in result
        assert ";" not in result
        assert "&" not in result


# ---------------------------------------------------------------------------
# URL validation (open-url endpoint)
# ---------------------------------------------------------------------------


def _create_test_app():
    from orchestrator.api.app import create_app
    from orchestrator.state.db import get_connection

    conn = get_connection(":memory:")
    return create_app(db=conn, test_mode=True)


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    app = _create_test_app()
    return TestClient(app)


class TestOpenUrlValidation:
    """Test the /api/open-url endpoint rejects non-http(s) URLs."""

    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient

        app = _create_test_app()
        return TestClient(app)

    def test_valid_https_url(self, client):
        with patch("subprocess.Popen"):
            resp = client.post("/api/open-url", json={"url": "https://example.com"})
            assert resp.json()["status"] == "ok"

    def test_valid_http_url(self, client):
        with patch("subprocess.Popen"):
            resp = client.post("/api/open-url", json={"url": "http://localhost:5173"})
            assert resp.json()["status"] == "ok"

    def test_rejects_file_url(self, client):
        resp = client.post("/api/open-url", json={"url": "file:///etc/passwd"})
        assert resp.json()["status"] == "error"

    def test_rejects_javascript_url(self, client):
        resp = client.post("/api/open-url", json={"url": "javascript:alert(1)"})
        assert resp.json()["status"] == "error"

    def test_rejects_data_url(self, client):
        resp = client.post("/api/open-url", json={"url": "data:text/html,<h1>hi</h1>"})
        assert resp.json()["status"] == "error"

    def test_rejects_empty_url(self, client):
        resp = client.post("/api/open-url", json={"url": ""})
        assert resp.json()["status"] == "error"

    def test_rejects_scheme_only(self, client):
        resp = client.post("/api/open-url", json={"url": "http://"})
        assert resp.json()["status"] == "error"


# ---------------------------------------------------------------------------
# Backup password Keychain helpers
# ---------------------------------------------------------------------------


class TestKeychainHelpers:
    """Test keychain set/get/delete with mocked subprocess."""

    @patch("orchestrator.api.routes.backup.platform")
    def test_keychain_available_on_macos(self, mock_platform):
        from orchestrator.api.routes.backup import _keychain_available

        mock_platform.system.return_value = "Darwin"
        assert _keychain_available() is True

    @patch("orchestrator.api.routes.backup.platform")
    def test_keychain_not_available_on_linux(self, mock_platform):
        from orchestrator.api.routes.backup import _keychain_available

        mock_platform.system.return_value = "Linux"
        assert _keychain_available() is False

    @patch("orchestrator.api.routes.backup.subprocess.run")
    def test_keychain_set_success(self, mock_run):
        from orchestrator.api.routes.backup import _keychain_set

        mock_run.return_value = MagicMock(returncode=0)
        assert _keychain_set("my-secret") is True
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert "security" in args
        assert "add-generic-password" in args
        assert "my-secret" in args

    @patch("orchestrator.api.routes.backup.subprocess.run")
    def test_keychain_set_failure(self, mock_run):
        from orchestrator.api.routes.backup import _keychain_set

        mock_run.side_effect = OSError("security not found")
        assert _keychain_set("secret") is False

    @patch("orchestrator.api.routes.backup.subprocess.run")
    def test_keychain_get_success(self, mock_run):
        from orchestrator.api.routes.backup import _keychain_get

        mock_run.return_value = MagicMock(returncode=0, stdout="my-secret\n")
        assert _keychain_get() == "my-secret"

    @patch("orchestrator.api.routes.backup.subprocess.run")
    def test_keychain_get_not_found(self, mock_run):
        from orchestrator.api.routes.backup import _keychain_get

        mock_run.return_value = MagicMock(returncode=44, stdout="")
        assert _keychain_get() is None

    @patch("orchestrator.api.routes.backup.subprocess.run")
    def test_keychain_delete_success(self, mock_run):
        from orchestrator.api.routes.backup import _keychain_delete

        mock_run.return_value = MagicMock(returncode=0)
        assert _keychain_delete() is True

    @patch("orchestrator.api.routes.backup.subprocess.run")
    def test_keychain_delete_failure(self, mock_run):
        from orchestrator.api.routes.backup import _keychain_delete

        mock_run.side_effect = OSError("not found")
        assert _keychain_delete() is False


class TestPasswordAccessors:
    """Test _set_password, _get_password, _has_password, _clear_password."""

    @patch("orchestrator.api.routes.backup._keychain_available", return_value=True)
    @patch("orchestrator.api.routes.backup._keychain_set", return_value=True)
    def test_set_password_uses_keychain_on_macos(self, mock_kc_set, mock_kc_avail):
        from orchestrator.api.routes.backup import _set_password

        mock_conn = MagicMock()
        with patch("orchestrator.api.routes.backup.config_repo"):
            _set_password(mock_conn, "secret")
        mock_kc_set.assert_called_once_with("secret")

    @patch("orchestrator.api.routes.backup._keychain_available", return_value=False)
    def test_set_password_uses_db_on_non_macos(self, mock_kc_avail):
        from orchestrator.api.routes.backup import _set_password

        mock_conn = MagicMock()
        with patch("orchestrator.api.routes.backup.config_repo") as mock_repo:
            _set_password(mock_conn, "secret")
            mock_repo.set_config.assert_called_once_with(
                mock_conn, "backup.password", "secret", category="backup"
            )

    @patch("orchestrator.api.routes.backup._keychain_available", return_value=True)
    @patch("orchestrator.api.routes.backup._keychain_get", return_value="kc-secret")
    def test_get_password_from_keychain(self, mock_kc_get, mock_kc_avail):
        from orchestrator.api.routes.backup import _get_password

        mock_conn = MagicMock()
        assert _get_password(mock_conn) == "kc-secret"

    @patch("orchestrator.api.routes.backup._keychain_available", return_value=True)
    @patch("orchestrator.api.routes.backup._keychain_get", return_value=None)
    @patch("orchestrator.api.routes.backup._keychain_set", return_value=True)
    def test_get_password_migrates_from_db(self, mock_kc_set, mock_kc_get, mock_kc_avail):
        from orchestrator.api.routes.backup import _get_password

        mock_conn = MagicMock()
        with patch("orchestrator.api.routes.backup.config_repo") as mock_repo:
            mock_repo.get_config_value.return_value = "db-secret"
            result = _get_password(mock_conn)
            assert result == "db-secret"
            # Should migrate to keychain
            mock_kc_set.assert_called_once_with("db-secret")
            # Should clear from DB
            mock_repo.set_config.assert_called_once()

    @patch("orchestrator.api.routes.backup._keychain_available", return_value=True)
    @patch("orchestrator.api.routes.backup._keychain_get", return_value=None)
    def test_get_password_returns_none_when_empty(self, mock_kc_get, mock_kc_avail):
        from orchestrator.api.routes.backup import _get_password

        mock_conn = MagicMock()
        with patch("orchestrator.api.routes.backup.config_repo") as mock_repo:
            mock_repo.get_config_value.return_value = ""
            assert _get_password(mock_conn) is None

    @patch("orchestrator.api.routes.backup._keychain_available", return_value=False)
    def test_get_password_db_fallback_empty_is_none(self, mock_kc_avail):
        from orchestrator.api.routes.backup import _get_password

        mock_conn = MagicMock()
        with patch("orchestrator.api.routes.backup.config_repo") as mock_repo:
            mock_repo.get_config_value.return_value = ""
            assert _get_password(mock_conn) is None

    @patch("orchestrator.api.routes.backup._keychain_available", return_value=True)
    @patch("orchestrator.api.routes.backup._keychain_delete", return_value=True)
    def test_clear_password(self, mock_kc_del, mock_kc_avail):
        from orchestrator.api.routes.backup import _clear_password

        mock_conn = MagicMock()
        with patch("orchestrator.api.routes.backup.config_repo"):
            _clear_password(mock_conn)
            mock_kc_del.assert_called_once()


# ---------------------------------------------------------------------------
# GitHub auth endpoint
# ---------------------------------------------------------------------------


class TestGhAuth:
    """Test the /api/gh-auth endpoint opens Terminal with gh auth login."""

    @patch("platform.system", return_value="Darwin")
    @patch("subprocess.Popen")
    def test_gh_auth_calls_osascript_on_darwin(self, mock_popen, mock_system, client):
        resp = client.post("/api/gh-auth")
        assert resp.json() == {"ok": True}
        mock_popen.assert_called_once_with(
            [
                "osascript",
                "-e",
                'tell application "Terminal" to do script "gh auth login"',
            ]
        )

    @patch("platform.system", return_value="Linux")
    @patch("subprocess.Popen")
    def test_gh_auth_calls_terminal_on_linux(self, mock_popen, mock_system, client):
        resp = client.post("/api/gh-auth")
        assert resp.json() == {"ok": True}
        mock_popen.assert_called_once_with(["x-terminal-emulator", "-e", "gh", "auth", "login"])

    @patch("platform.system", return_value="Linux")
    @patch("subprocess.Popen")
    def test_gh_auth_falls_back_to_xterm(self, mock_popen, mock_system, client):
        mock_popen.side_effect = [FileNotFoundError, MagicMock()]
        resp = client.post("/api/gh-auth")
        assert resp.json() == {"ok": True}
        assert mock_popen.call_count == 2
        # Second call should be xterm
        assert mock_popen.call_args_list[1][0][0] == ["xterm", "-e", "gh", "auth", "login"]

    @patch("platform.system", return_value="Darwin")
    @patch("subprocess.Popen", side_effect=OSError("not found"))
    def test_gh_auth_returns_error_on_failure(self, mock_popen, mock_system, client):
        resp = client.post("/api/gh-auth")
        data = resp.json()
        assert data["status"] == "error"
        assert "not found" in data["message"]
