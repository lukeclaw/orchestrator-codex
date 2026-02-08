"""Read/write Anthropic API key from macOS Keychain."""

import os
import subprocess

SERVICE_NAME = "claude-orchestrator"
ACCOUNT_NAME = "anthropic-api-key"


def _keychain_read(service: str, account: str | None = None) -> str | None:
    """Read a password from macOS Keychain."""
    cmd = ["security", "find-generic-password", "-s", service]
    if account:
        cmd.extend(["-a", account])
    cmd.append("-w")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


def get_api_key() -> str | None:
    """Get the Anthropic API key.

    Resolution order:
    1. ANTHROPIC_API_KEY environment variable
    2. "claude-orchestrator" keychain entry (dedicated orchestrator key)
    3. "Claude Code" keychain entry (reuse Claude Code's token)
    """
    # Environment variable takes precedence (useful for testing / CI)
    env_key = os.environ.get("ANTHROPIC_API_KEY")
    if env_key:
        return env_key

    # Try orchestrator's own keychain entry
    key = _keychain_read(SERVICE_NAME, ACCOUNT_NAME)
    if key:
        return key

    # Fallback: reuse Claude Code's stored token
    key = _keychain_read("Claude Code")
    if key:
        return key

    return None


def set_api_key(api_key: str) -> bool:
    """Store the Anthropic API key in macOS Keychain."""
    try:
        # Delete existing entry if present (ignore errors)
        subprocess.run(
            [
                "security", "delete-generic-password",
                "-s", SERVICE_NAME,
                "-a", ACCOUNT_NAME,
            ],
            capture_output=True,
            timeout=5,
        )
        # Add new entry
        result = subprocess.run(
            [
                "security", "add-generic-password",
                "-s", SERVICE_NAME,
                "-a", ACCOUNT_NAME,
                "-w", api_key,
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def has_api_key() -> bool:
    """Check if an API key is available."""
    return get_api_key() is not None
