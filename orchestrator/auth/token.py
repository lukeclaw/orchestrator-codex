"""Token validation and caching."""

import time

from orchestrator.auth.keychain import get_api_key

_cached_key: str | None = None
_cached_at: float = 0
CACHE_TTL = 300  # Re-check every 5 minutes


def get_validated_key() -> str | None:
    """Get the API key with caching. Returns None if unavailable."""
    global _cached_key, _cached_at

    now = time.time()
    if _cached_key and (now - _cached_at) < CACHE_TTL:
        return _cached_key

    key = get_api_key()
    if key and key.startswith("sk-"):
        _cached_key = key
        _cached_at = now
        return key

    _cached_key = None
    _cached_at = 0
    return None


def invalidate_cache():
    """Force re-read of API key on next call."""
    global _cached_key, _cached_at
    _cached_key = None
    _cached_at = 0
