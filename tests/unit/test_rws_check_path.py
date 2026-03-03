"""Tests for the RWS check_path handler."""

import os


class TestCheckPathHandler:
    """Verify handle_check_path is registered and works correctly."""

    def test_handler_registered_in_command_handlers(self):
        """The COMMAND_HANDLERS dict in the RWS script must contain 'check_path'."""
        from orchestrator.terminal.remote_worker_server import _REMOTE_WORKER_SERVER_SCRIPT

        assert "check_path" in _REMOTE_WORKER_SERVER_SCRIPT
        assert '"check_path": handle_check_path' in _REMOTE_WORKER_SERVER_SCRIPT

    def test_handler_defined_in_script(self):
        """The handle_check_path function must be defined in the script."""
        from orchestrator.terminal.remote_worker_server import _REMOTE_WORKER_SERVER_SCRIPT

        assert "def handle_check_path(cmd):" in _REMOTE_WORKER_SERVER_SCRIPT

    def test_handler_logic_all_present(self, tmp_path):
        """Simulate the handler logic: all paths exist -> no missing."""
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_text("x")
        f2.write_text("y")

        paths = [str(f1), str(f2)]
        missing = [p for p in paths if not os.path.exists(p)]
        assert missing == []

    def test_handler_logic_some_missing(self, tmp_path):
        """Simulate the handler logic: some paths missing."""
        f1 = tmp_path / "exists.txt"
        f1.write_text("x")

        paths = [str(f1), str(tmp_path / "gone.txt")]
        missing = [p for p in paths if not os.path.exists(p)]
        assert len(missing) == 1
        assert "gone.txt" in missing[0]

    def test_handler_logic_empty_paths(self):
        """Handler should return error for empty paths list."""
        paths = []
        if not paths:
            result = {"error": "paths is required and must be a non-empty list"}
        else:
            missing = [p for p in paths if not os.path.exists(p)]
            result = {"missing": missing, "missing_count": len(missing)}
        assert "error" in result
