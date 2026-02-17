"""Paste image endpoint — saves clipboard images to data/images/ for serving via static mount."""

import base64
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter()


def get_images_dir() -> Path:
    """Return the persistent images directory (data/images/)."""
    from orchestrator.main import PROJECT_ROOT, load_config
    config = load_config()
    db_path = config.get("database", {}).get("path", "data/orchestrator.db")
    data_dir = PROJECT_ROOT / Path(db_path).parent
    images_dir = data_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    return images_dir


def save_image(image_data: str, filename: Optional[str] = None) -> dict:
    """Save a base64-encoded image to data/images/.

    Returns dict with ok, url (relative path), and filename.
    Shared utility used by paste endpoint and potentially brain endpoint.
    """
    # Parse the base64 data (handle data URL prefix if present)
    raw_data = image_data
    file_ext = "png"

    if raw_data.startswith("data:"):
        try:
            header, raw_data = raw_data.split(",", 1)
            mime_part = header.split(";")[0]  # data:image/png
            if "/" in mime_part:
                mime_type = mime_part.split("/")[1]
                ext_map = {"png": "png", "jpeg": "jpg", "jpg": "jpg", "gif": "gif", "webp": "webp"}
                file_ext = ext_map.get(mime_type, "png")
        except ValueError:
            pass

    # Decode (validate=True rejects non-base64 characters)
    try:
        image_bytes = base64.b64decode(raw_data, validate=True)
    except Exception as e:
        raise HTTPException(400, f"Invalid base64 image data: {e}")

    images_dir = get_images_dir()

    # Generate filename
    if filename:
        safe_name = "".join(c for c in filename if c.isalnum() or c in ".-_")
        if not safe_name:
            safe_name = "image"
        fname = f"{safe_name}.{file_ext}"
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        short_id = uuid.uuid4().hex[:6]
        fname = f"clipboard_{timestamp}_{short_id}.{file_ext}"

    file_path = images_dir / fname

    # Handle collision
    counter = 1
    base_path = file_path
    while file_path.exists():
        stem = base_path.stem
        file_path = base_path.with_name(f"{stem}_{counter}{base_path.suffix}")
        counter += 1

    # Write
    try:
        file_path.write_bytes(image_bytes)
        logger.info("Saved image to %s (%d bytes)", file_path, len(image_bytes))
    except Exception as e:
        logger.exception("Failed to save image")
        raise HTTPException(500, f"Failed to save image: {e}")

    return {
        "ok": True,
        "url": f"/api/images/{file_path.name}",
        "filename": file_path.name,
        "size": len(image_bytes),
    }


def cleanup_images(images_dir: Path, max_size_mb: int = 200):
    """Delete oldest images if directory exceeds size cap."""
    if not images_dir.exists():
        return
    files = sorted(
        [f for f in images_dir.iterdir() if f.is_file()],
        key=lambda f: f.stat().st_mtime,
    )
    total = sum(f.stat().st_size for f in files)
    while total > max_size_mb * 1024 * 1024 and files:
        oldest = files.pop(0)
        total -= oldest.stat().st_size
        oldest.unlink()
        logger.info("Cleaned up old image: %s", oldest.name)


class PasteImageRequest(BaseModel):
    image_data: str  # base64-encoded image (with or without data URL prefix)
    filename: str | None = None  # optional custom filename


@router.post("/paste-image")
def paste_image(req: PasteImageRequest):
    """Save a clipboard image to data/images/ and return a servable URL."""
    return save_image(req.image_data, req.filename)
