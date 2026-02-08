"""Dashboard route — serves the React SPA."""

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

router = APIRouter()

DIST_DIR = Path(__file__).parent.parent.parent / "web" / "dist"


@router.get("/", response_class=HTMLResponse)
@router.get("/projects", response_class=HTMLResponse)
@router.get("/projects/{project_id:path}", response_class=HTMLResponse)
@router.get("/sessions", response_class=HTMLResponse)
@router.get("/sessions/{session_id:path}", response_class=HTMLResponse)
@router.get("/tasks", response_class=HTMLResponse)
@router.get("/decisions", response_class=HTMLResponse)
@router.get("/chat", response_class=HTMLResponse)
@router.get("/activity", response_class=HTMLResponse)
@router.get("/settings", response_class=HTMLResponse)
def dashboard(request: Request):
    """Serve the SPA index.html for all frontend routes."""
    dist_index = DIST_DIR / "index.html"
    if dist_index.exists():
        return HTMLResponse(dist_index.read_text())

    return HTMLResponse(
        "<h1>Dashboard not found</h1>"
        "<p>Run <code>cd frontend && npm run build</code> to build the React UI.</p>"
    )
