"""Dashboard route — serves the React SPA.

Any GET request that doesn't match an /api/* or /ws* endpoint
or a static /assets/* file falls through to here and gets index.html
so that React Router can handle client-side routing.
"""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from orchestrator import paths

router = APIRouter()


def _serve_spa() -> HTMLResponse:
    dist_index = paths.web_dist_dir() / "index.html"
    if dist_index.exists():
        return HTMLResponse(dist_index.read_text())
    return HTMLResponse(
        "<h1>Dashboard not found</h1>"
        "<p>Run <code>cd frontend && npm run build</code> to build the React UI.</p>"
    )


@router.get("/", response_class=HTMLResponse)
def dashboard_root(request: Request):
    return _serve_spa()


@router.get("/{path:path}", response_class=HTMLResponse)
def dashboard_catchall(request: Request, path: str):
    """Catch-all for frontend routes — lets React Router handle them."""
    return _serve_spa()
