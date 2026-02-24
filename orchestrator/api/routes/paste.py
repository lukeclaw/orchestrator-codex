"""Paste image endpoint — saves clipboard images to data/images/ for serving via static mount."""

import base64
import logging
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter()


def get_images_dir() -> Path:
    """Return the persistent images directory."""
    from orchestrator import paths

    img_dir = paths.images_dir()
    img_dir.mkdir(parents=True, exist_ok=True)
    return img_dir


def save_image(image_data: str, filename: str | None = None) -> dict:
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


@router.get("/clipboard")
def read_clipboard():
    """Read the system clipboard and return its contents.

    Returns text via ``pbpaste`` and images via ``osascript`` (JXA) so the
    frontend can avoid ``navigator.clipboard`` which triggers a macOS
    WKWebView permission popup.
    """
    import subprocess

    # --- Text -----------------------------------------------------------
    text: str | None = None
    try:
        result = subprocess.run(
            ["pbpaste"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if result.returncode == 0 and result.stdout.strip():
            text = result.stdout
    except Exception:
        logger.debug("pbpaste failed", exc_info=True)

    # --- Image ----------------------------------------------------------
    image_base64: str | None = None
    jxa_script = """\
ObjC.import('AppKit');
var pb = $.NSPasteboard.generalPasteboard;
var pngData = pb.dataForType($.NSPasteboardTypePNG);
if (!pngData.isNil()) {
    pngData.base64EncodedStringWithOptions(0).js;
} else {
    var tiffData = pb.dataForType($.NSPasteboardTypeTIFF);
    if (!tiffData.isNil()) {
        var rep = $.NSBitmapImageRep.imageRepWithData(tiffData);
        var png = rep.representationUsingTypeProperties($.NSBitmapImageFileTypePNG, $());
        png.base64EncodedStringWithOptions(0).js;
    } else {
        '';
    }
}"""
    try:
        result = subprocess.run(
            ["osascript", "-l", "JavaScript", "-e", jxa_script],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if result.returncode == 0:
            b64 = result.stdout.strip()
            if b64:
                image_base64 = b64
    except Exception:
        logger.debug("osascript clipboard image read failed", exc_info=True)

    if not text and not image_base64:
        raise HTTPException(status_code=204, detail="Clipboard is empty")

    return {
        "text": text,
        "image_base64": image_base64,
    }


class PasteImageRequest(BaseModel):
    image_data: str  # base64-encoded image (with or without data URL prefix)
    filename: str | None = None  # optional custom filename


@router.post("/paste-image")
def paste_image(req: PasteImageRequest):
    """Save a clipboard image to data/images/ and return a servable URL."""
    return save_image(req.image_data, req.filename)
