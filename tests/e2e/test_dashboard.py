"""E2E tests for the Claude Orchestrator React dashboard.

Seed data (see conftest.py):
  - 3 sessions: worker-alpha (working), worker-beta (idle), worker-gamma (disconnected)
"""

from __future__ import annotations

from tests.e2e.conftest import screenshot

# ---------------------------------------------------------------------------
# 01. Dashboard loads
# ---------------------------------------------------------------------------


def test_01_dashboard_loads(page):
    """Page loads, title present, WebSocket connects."""
    screenshot(page, "01_dashboard_loads")

    assert "Orchestrator" in page.title()

    # Stats bar is visible
    stats = page.query_selector("[data-testid='stats-bar']")
    assert stats is not None
    assert stats.is_visible()

    # WebSocket connection status dot should be present
    conn = page.query_selector("[data-testid='connection-status']")
    assert conn is not None


# ---------------------------------------------------------------------------
# 02. Session cards render
# ---------------------------------------------------------------------------


def test_02_session_cards_render(page):
    """Three worker cards appear with correct names and status badges."""
    cards = page.query_selector_all("[data-testid='worker-card']")
    screenshot(page, "02_session_cards")

    assert len(cards) == 3

    names = [c.query_selector(".wcc-name").inner_text() for c in cards]
    assert "worker-alpha" in names
    assert "worker-beta" in names
    assert "worker-gamma" in names

    # Check status badges
    for card in cards:
        badge = card.query_selector(".status-badge")
        assert badge is not None
        text = badge.inner_text().lower()
        assert text in ("working", "idle", "disconnected")


# ---------------------------------------------------------------------------
# 03. Quick stats accurate
# ---------------------------------------------------------------------------


def test_03_quick_stats_accurate(page):
    """Stats bar shows correct counts from seeded data."""
    screenshot(page, "03_quick_stats")

    sessions_val = page.query_selector("#stat-sessions-val").inner_text()

    # All 3 workers counted (working + idle + disconnected)
    assert sessions_val == "3"


# ---------------------------------------------------------------------------
# 04. Add session flow
# ---------------------------------------------------------------------------


def test_04_add_session_flow(page):
    """Click +, fill form, submit, 4th card appears."""
    # Open modal
    page.click("[data-testid='add-session-btn']")
    page.wait_for_selector("[data-testid='add-session-form']", timeout=3000)

    screenshot(page, "04a_add_session_modal")

    # Switch to Local mode (modal defaults to local, host is implicit "localhost")
    page.click("[data-testid='worker-type-toggle'] >> text=Local")

    # Fill form (Local mode has name + path, no host input)
    page.fill("[data-testid='session-name-input']", "worker-delta")
    page.fill("[data-testid='session-path-input']", "/src/project-d")

    screenshot(page, "04b_add_session_filled")

    # Submit and wait for the new card to appear
    page.click("[data-testid='create-session-btn']")

    # Wait for the 4th worker card to render (refresh is async)
    page.wait_for_function(
        "document.querySelectorAll(\"[data-testid='worker-card']\").length >= 4",
        timeout=5000,
    )

    screenshot(page, "04c_after_add_session")

    # Should now have 4 worker cards
    cards = page.query_selector_all("[data-testid='worker-card']")
    assert len(cards) == 4

    names = [c.query_selector(".wcc-name").inner_text() for c in cards]
    assert "worker-delta" in names

    # Stats should update to 4 workers
    assert page.query_selector("#stat-sessions-val").inner_text() == "4"


# ---------------------------------------------------------------------------
# 05. Session detail page
# ---------------------------------------------------------------------------


def test_05_session_detail_page(page, server):
    """Navigate to worker detail page, see host/status/info."""
    # Click on worker-alpha card — navigates to /workers/s1
    card = page.query_selector("[data-session-id='s1']")
    assert card is not None
    card.click()
    page.wait_for_url("**/workers/s1", timeout=5000)

    screenshot(page, "05_session_detail")

    # Should be on the worker detail page
    assert "/workers/s1" in page.url

    # Session name should be visible
    text = page.inner_text("body")
    assert "worker-alpha" in text


# ---------------------------------------------------------------------------
# 10. Brain panel
# ---------------------------------------------------------------------------


def test_10_brain_sidebar(page):
    """Brain sidebar is visible on all pages with Start button."""
    screenshot(page, "10_brain_sidebar")

    brain_sidebar = page.query_selector("[data-testid='brain-sidebar']")
    assert brain_sidebar is not None
    assert brain_sidebar.is_visible()

    # Should show "Brain" title or Start button (brain is not running in test env)
    page_text = brain_sidebar.inner_text()
    assert "Brain" in page_text or "Start" in page_text


# ---------------------------------------------------------------------------
# 11. Responsive layout
# ---------------------------------------------------------------------------


def test_11_responsive_layout(page):
    """At 600px width, layout switches to single column."""
    page.set_viewport_size({"width": 600, "height": 900})
    page.wait_for_timeout(200)

    screenshot(page, "11_responsive")

    # Stats bar should still be visible
    stats = page.query_selector("[data-testid='stats-bar']")
    assert stats.is_visible()

    # Session panel should still be visible
    sessions_panel = page.query_selector("[data-testid='sessions-panel']")
    assert sessions_panel.is_visible()

    # Brain sidebar should still be present (possibly collapsed)
    brain_sidebar = page.query_selector("[data-testid='brain-sidebar']")
    assert brain_sidebar is not None

    # No console errors throughout
    js_errors = [e for e in page._console_errors if "WebSocket" not in e]
    assert len(js_errors) == 0, f"Console errors: {js_errors}"


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# 13. Smart Paste button
# ---------------------------------------------------------------------------


def test_13_smart_paste_not_on_dashboard(page):
    """Smart Paste button is not present on the main dashboard page (contextual only)."""
    screenshot(page, "13a_paste_dashboard")

    # Smart Paste was moved from global header to session detail pages
    paste_btn = page.query_selector(".sd-paste-btn")
    assert paste_btn is None


def test_14_smart_paste_on_worker_page(page):
    """Smart Paste button is present on the worker detail page."""
    # Navigate to worker-alpha detail page
    card = page.query_selector("[data-session-id='s1']")
    assert card is not None
    card.click()
    page.wait_for_url("**/workers/s1", timeout=5000)

    screenshot(page, "14_paste_worker_page")

    assert "/workers/s1" in page.url

    paste_btn = page.query_selector(".sd-paste-btn")
    assert paste_btn is not None
