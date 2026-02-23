"""Update check — compares current version against GitHub releases."""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx
from fastapi import APIRouter

from orchestrator import __version__

logger = logging.getLogger(__name__)

router = APIRouter()

GITHUB_REPO = "yudongqiu/orchestrator"
LATEST_JSON_URL = f"https://github.com/{GITHUB_REPO}/releases/latest/download/latest.json"

# Simple in-memory cache (avoid hammering GitHub on repeated clicks)
_cache: dict[str, Any] = {}
_CACHE_TTL = 300  # 5 minutes


def _parse_version(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.lstrip("v").split("."))


@router.get("/updates/check")
async def check_for_updates(force: bool = False):
    """Fetch latest.json from GitHub releases and compare versions."""
    now = time.time()

    if not force and _cache.get("result") and now - _cache.get("fetched_at", 0) < _CACHE_TTL:
        return _cache["result"]

    current = __version__

    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            resp = await client.get(LATEST_JSON_URL)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.warning("Failed to check for updates: %s", e)
        return {
            "current_version": current,
            "latest_version": None,
            "update_available": False,
            "error": str(e),
        }

    latest = data.get("version", current)
    notes = data.get("notes", "")
    pub_date = data.get("pub_date", "")

    try:
        update_available = _parse_version(latest) > _parse_version(current)
    except (ValueError, TypeError):
        update_available = False

    result = {
        "current_version": current,
        "latest_version": latest,
        "update_available": update_available,
        "release_url": f"https://github.com/{GITHUB_REPO}/releases/tag/v{latest}",
        "release_notes": notes,
        "pub_date": pub_date,
    }

    _cache["result"] = result
    _cache["fetched_at"] = now

    return result
