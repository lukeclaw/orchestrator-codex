"""Tests for launcher.py setup_path().

Ensures required PATH entries are not accidentally removed.
"""

import os
from unittest.mock import patch

from orchestrator.launcher import setup_path


class TestSetupPath:
    """Verify setup_path() includes all required directories."""

    def _run_setup_path(self):
        """Run setup_path() with a clean PATH and return the resulting PATH."""
        with patch.dict(os.environ, {"PATH": "/usr/bin"}, clear=False):
            setup_path()
            return os.environ["PATH"]

    def test_includes_homebrew_intel(self):
        result = self._run_setup_path()
        assert "/usr/local/bin" in result.split(os.pathsep)

    def test_includes_homebrew_arm(self):
        result = self._run_setup_path()
        assert "/opt/homebrew/bin" in result.split(os.pathsep)

    def test_includes_linkedin_cli(self):
        result = self._run_setup_path()
        assert "/usr/local/linkedin/bin" in result.split(os.pathsep)

    def test_preserves_existing_path(self):
        result = self._run_setup_path()
        assert "/usr/bin" in result.split(os.pathsep)

    def test_extra_paths_prepended(self):
        """Extra paths should come before existing PATH entries."""
        result = self._run_setup_path()
        parts = result.split(os.pathsep)
        assert parts.index("/usr/local/bin") < parts.index("/usr/bin")
