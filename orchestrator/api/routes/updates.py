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
GITHUB_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"

# Simple in-memory cache (avoid hammering GitHub on repeated clicks)
_cache: dict[str, Any] = {}
_CACHE_TTL = 300  # 5 minutes


def _parse_version(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.lstrip("v").split("."))


@router.get("/updates/check")
async def check_for_updates(force: bool = False):
    """Check the GitHub Releases API for a newer version."""
    now = time.time()

    if not force and _cache.get("result") and now - _cache.get("fetched_at", 0) < _CACHE_TTL:
        return _cache["result"]

    current = __version__

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                GITHUB_API_URL,
                headers={"Accept": "application/vnd.github+json"},
            )
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

    tag = data.get("tag_name", "")
    latest = tag.lstrip("v")
    notes = data.get("body", "")
    pub_date = data.get("published_at", "")
    release_url = data.get("html_url", f"https://github.com/{GITHUB_REPO}/releases/latest")

    # Find the DMG asset URL for the direct download link
    dmg_url = None
    for asset in data.get("assets", []):
        if asset.get("name", "").endswith(".dmg"):
            dmg_url = asset["browser_download_url"]
            break

    try:
        update_available = bool(latest) and _parse_version(latest) > _parse_version(current)
    except (ValueError, TypeError):
        update_available = False

    result = {
        "current_version": current,
        "latest_version": latest or None,
        "update_available": update_available,
        "release_url": release_url,
        "dmg_url": dmg_url,
        "release_notes": notes,
        "pub_date": pub_date,
    }

    _cache["result"] = result
    _cache["fetched_at"] = now

    return result
