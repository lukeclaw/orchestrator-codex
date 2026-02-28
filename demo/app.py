"""Demo application — thin wrapper around the orchestrator app factory.

Starts the orchestrator with an isolated demo database, auto-seeding on first run.
No changes to the main orchestrator package are needed.

Usage:
    uv run uvicorn demo.app:create_demo_app --factory --port 8094
"""

from pathlib import Path

DB_PATH = str(Path(__file__).parent / "data" / "demo.db")


def create_demo_app():
    """Create the FastAPI app pointing at the demo database."""
    if not Path(DB_PATH).exists():
        from demo.seed import seed_demo

        seed_demo(DB_PATH)

    from orchestrator.api.app import create_app

    return create_app(db_path=DB_PATH)
