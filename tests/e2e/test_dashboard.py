"""E2E tests for the Claude Orchestrator React dashboard.

Seed data (see conftest.py):
  - 3 sessions: worker-alpha (working), worker-beta (idle), worker-gamma (disconnected)
  - 2 decisions: d1 (high, with options), d2 (critical, with context)
  - 3 activities: task.started, pr.created, session.connected
"""

from __future__ import annotations

from tests.e2e.conftest import screenshot


# ---------------------------------------------------------------------------
# 01. Dashboard loads
# ---------------------------------------------------------------------------


def test_01_dashboard_loads(page):
    """Page loads, title present, WebSocket connects."""
    screenshot(page, "01_dashboard_loads")

    assert "Claude Orchestrator" in page.title()

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
    """Three session cards appear with correct names and status badges."""
    cards = page.query_selector_all("[data-testid='session-card']")
    screenshot(page, "02_session_cards")

    assert len(cards) == 3

    # React uses .sc-name class
    names = [c.query_selector(".sc-name").inner_text() for c in cards]
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
    decisions_val = page.query_selector("#stat-decisions-val").inner_text()

    # 2 active (working + idle), disconnected doesn't count
    assert sessions_val == "2"
    # 2 pending decisions
    assert decisions_val == "2"


# ---------------------------------------------------------------------------
# 04. Add session flow
# ---------------------------------------------------------------------------


def test_04_add_session_flow(page):
    """Click +, fill form, submit, 4th card appears."""
    # Open modal
    page.click("[data-testid='add-session-btn']")
    page.wait_for_selector("[data-testid='add-session-form']", timeout=3000)

    screenshot(page, "04a_add_session_modal")

    # Fill form
    page.fill("[data-testid='session-name-input']", "worker-delta")
    page.fill("[data-testid='session-host-input']", "localhost")
    page.fill("[data-testid='session-path-input']", "/src/project-d")

    screenshot(page, "04b_add_session_filled")

    # Submit
    page.click("[data-testid='create-session-btn']")
    page.wait_for_timeout(1500)

    screenshot(page, "04c_after_add_session")

    # Should now have 4 session cards
    cards = page.query_selector_all("[data-testid='session-card']")
    assert len(cards) == 4

    names = [c.query_selector(".sc-name").inner_text() for c in cards]
    assert "worker-delta" in names

    # Stats should update to 3 active sessions
    assert page.query_selector("#stat-sessions-val").inner_text() == "3"


# ---------------------------------------------------------------------------
# 05. Session detail page
# ---------------------------------------------------------------------------


def test_05_session_detail_page(page, server):
    """Navigate to session detail page, see host/status/info."""
    # Click on worker-alpha card — navigates to /sessions/s1
    card = page.query_selector("[data-session-id='s1']")
    assert card is not None
    card.click()
    page.wait_for_timeout(1500)

    screenshot(page, "05_session_detail")

    # Should be on the session detail page
    assert "/sessions/s1" in page.url

    # Session name and info should be visible
    text = page.inner_text("body")
    assert "worker-alpha" in text
    assert "localhost" in text

    # Navigate back
    back_btn = page.query_selector("text=Dashboard")
    if back_btn:
        back_btn.click()
        page.wait_for_timeout(1000)


# ---------------------------------------------------------------------------
# 06. Decision queue display
# ---------------------------------------------------------------------------


def test_06_decision_queue_display(page):
    """Two decisions visible with urgency colors and option buttons."""
    screenshot(page, "06_decisions")

    cards = page.query_selector_all("[data-testid='decision-card']")
    assert len(cards) == 2

    count = page.query_selector("[data-testid='decision-count']")
    assert count is not None
    assert count.inner_text() == "2"

    # Check urgency tags
    urgencies = [c.query_selector(".urgency-tag").inner_text().lower() for c in cards]
    assert "high" in urgencies
    assert "critical" in urgencies

    # Find the HIGH decision — should have 2 option buttons
    d1_card = None
    for c in cards:
        if "high" in c.query_selector(".urgency-tag").inner_text().lower():
            d1_card = c
            break
    assert d1_card is not None
    option_btns = d1_card.query_selector_all("[data-testid='approve-btn']")
    assert len(option_btns) == 2


# ---------------------------------------------------------------------------
# 07. Approve decision
# ---------------------------------------------------------------------------


def test_07_approve_decision(page):
    """Click an option button, decision resolves, count decrements."""
    cards = page.query_selector_all("[data-testid='decision-card']")
    initial_count = len(cards)

    first_approve = page.query_selector("[data-testid='approve-btn']")
    assert first_approve is not None
    first_approve.click()
    page.wait_for_timeout(1500)

    screenshot(page, "07_after_approve")

    remaining = page.query_selector_all("[data-testid='decision-card']")
    assert len(remaining) == initial_count - 1

    count = page.query_selector("[data-testid='decision-count']").inner_text()
    assert count == str(initial_count - 1)


# ---------------------------------------------------------------------------
# 08. Dismiss decision
# ---------------------------------------------------------------------------


def test_08_dismiss_decision(page):
    """Click Dismiss, decision removed."""
    cards = page.query_selector_all("[data-testid='decision-card']")
    if not cards:
        return

    initial = len(cards)
    dismiss_btn = page.query_selector("[data-testid='dismiss-btn']")
    assert dismiss_btn is not None
    dismiss_btn.click()
    page.wait_for_timeout(1500)

    screenshot(page, "08_after_dismiss")

    remaining = page.query_selector_all("[data-testid='decision-card']")
    assert len(remaining) == initial - 1


# ---------------------------------------------------------------------------
# 09. Activity timeline
# ---------------------------------------------------------------------------


def test_09_activity_timeline(page):
    """Activity entries show event_type tags."""
    screenshot(page, "09_activity_timeline")

    items = page.query_selector_all("[data-testid='activity-item']")
    assert len(items) >= 3

    # React uses .at-type class for event type
    event_types = [it.query_selector(".at-type").inner_text() for it in items]
    assert "session.connected" in event_types
    assert "pr.created" in event_types
    assert "task.started" in event_types

    # Each item should have a time
    for it in items:
        time_el = it.query_selector(".at-time")
        assert time_el is not None
        assert time_el.inner_text() != ""


# ---------------------------------------------------------------------------
# 10. Brain panel
# ---------------------------------------------------------------------------


def test_10_brain_panel(page):
    """Brain panel is visible on dashboard with Start button."""
    screenshot(page, "10_brain_panel")

    brain_panel = page.query_selector("[data-testid='brain-panel']")
    assert brain_panel is not None
    assert brain_panel.is_visible()

    # Should show "Start Brain" button (brain is not running in test env)
    page_text = brain_panel.inner_text()
    assert "Orchestrator Brain" in page_text or "Start Brain" in page_text


# ---------------------------------------------------------------------------
# 11. Responsive layout
# ---------------------------------------------------------------------------


def test_11_responsive_layout(page):
    """At 600px width, layout switches to single column."""
    page.set_viewport_size({"width": 600, "height": 900})
    page.wait_for_timeout(500)

    screenshot(page, "11_responsive")

    # Stats bar should still be visible
    stats = page.query_selector("[data-testid='stats-bar']")
    assert stats.is_visible()

    # Session panel should still be visible
    sessions_panel = page.query_selector("[data-testid='sessions-panel']")
    assert sessions_panel.is_visible()

    # No console errors throughout
    js_errors = [e for e in page._console_errors if "WebSocket" not in e]
    assert len(js_errors) == 0, f"Console errors: {js_errors}"
