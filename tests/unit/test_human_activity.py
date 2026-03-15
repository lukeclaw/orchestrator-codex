"""Unit tests for human_activity repository and HumanActivityTracker."""

import time
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from orchestrator.state.db import get_memory_connection
from orchestrator.state.migrations.runner import apply_migrations
from orchestrator.state.repositories import human_activity


@pytest.fixture
def db():
    conn = get_memory_connection()
    apply_migrations(conn)
    yield conn
    conn.close()


# --- Repository CRUD Tests ---


class TestStartInterval:
    def test_creates_row_with_start_time(self, db):
        row_id = human_activity.start_interval(db)
        db.commit()
        assert row_id is not None

        row = db.execute("SELECT * FROM human_activity_events WHERE id = ?", (row_id,)).fetchone()
        assert row is not None
        assert row["start_time"] is not None
        assert row["end_time"] is None

    def test_returns_row_id(self, db):
        id1 = human_activity.start_interval(db)
        db.commit()
        id2 = human_activity.start_interval(db)
        db.commit()
        assert id2 > id1


class TestCloseInterval:
    def test_sets_end_time(self, db):
        row_id = human_activity.start_interval(db)
        db.commit()

        end_time = "2026-03-15T12:00:00+00:00"
        human_activity.close_interval(db, row_id, end_time)
        db.commit()

        row = db.execute("SELECT * FROM human_activity_events WHERE id = ?", (row_id,)).fetchone()
        assert row["end_time"] == end_time


class TestGetOpenInterval:
    def test_returns_open_interval(self, db):
        row_id = human_activity.start_interval(db)
        db.commit()

        open_iv = human_activity.get_open_interval(db)
        assert open_iv is not None
        assert open_iv["id"] == row_id

    def test_returns_none_when_no_open(self, db):
        assert human_activity.get_open_interval(db) is None

    def test_returns_none_after_close(self, db):
        row_id = human_activity.start_interval(db)
        db.commit()
        human_activity.close_interval(db, row_id, "2026-03-15T12:00:00+00:00")
        db.commit()

        assert human_activity.get_open_interval(db) is None


class TestCloseStaleIntervals:
    def test_closes_open_intervals_on_recovery(self, db):
        # Insert an open interval with a known start time
        start_time = "2026-03-15T10:00:00+00:00"
        db.execute(
            "INSERT INTO human_activity_events (start_time) VALUES (?)",
            (start_time,),
        )
        db.commit()

        human_activity.close_stale_intervals(db, idle_timeout_seconds=300)

        row = db.execute("SELECT * FROM human_activity_events").fetchone()
        assert row["end_time"] is not None
        # end_time should be start_time + 300s
        expected_end = (datetime.fromisoformat(start_time) + timedelta(seconds=300)).isoformat()
        assert row["end_time"] == expected_end

    def test_leaves_closed_intervals_alone(self, db):
        db.execute(
            "INSERT INTO human_activity_events (start_time, end_time) VALUES (?, ?)",
            ("2026-03-15T10:00:00+00:00", "2026-03-15T11:00:00+00:00"),
        )
        db.commit()

        human_activity.close_stale_intervals(db)

        row = db.execute("SELECT * FROM human_activity_events").fetchone()
        assert row["end_time"] == "2026-03-15T11:00:00+00:00"

    def test_no_op_when_no_stale_intervals(self, db):
        human_activity.close_stale_intervals(db)
        # Should not raise


class TestQueryHumanHours:
    def _insert_interval(self, db, start, end):
        db.execute(
            "INSERT INTO human_activity_events (start_time, end_time) VALUES (?, ?)",
            (start, end),
        )
        db.commit()

    def test_single_interval(self, db):
        now = datetime.now(UTC)
        start = now.replace(hour=10, minute=0, second=0, microsecond=0)
        end = start + timedelta(hours=2)
        self._insert_interval(db, start.isoformat(), end.isoformat())

        since = (now - timedelta(days=1)).strftime("%Y-%m-%d")
        result = human_activity.query_human_hours(db, since)

        assert len(result) >= 1
        total = sum(r["hours"] for r in result)
        assert abs(total - 2.0) < 0.01

    def test_cross_midnight_interval(self, db):
        # Create an interval that spans midnight
        today = datetime.now().astimezone().replace(hour=23, minute=0, second=0, microsecond=0)
        start = today.astimezone(UTC)
        end = (today + timedelta(hours=2)).astimezone(UTC)
        self._insert_interval(db, start.isoformat(), end.isoformat())

        since = (datetime.now(UTC) - timedelta(days=2)).strftime("%Y-%m-%d")
        result = human_activity.query_human_hours(db, since)

        # Should be split across two days
        total = sum(r["hours"] for r in result)
        assert abs(total - 2.0) < 0.01
        assert len(result) == 2

    def test_open_interval_clamped_to_now(self, db):
        now = datetime.now(UTC)
        start = (now - timedelta(hours=1)).isoformat()
        db.execute(
            "INSERT INTO human_activity_events (start_time) VALUES (?)",
            (start,),
        )
        db.commit()

        since = (now - timedelta(days=1)).strftime("%Y-%m-%d")
        result = human_activity.query_human_hours(db, since)

        total = sum(r["hours"] for r in result)
        assert total > 0.9  # Should be close to 1 hour
        assert total < 1.1

    def test_empty_result(self, db):
        result = human_activity.query_human_hours(db, "2026-01-01")
        assert result == []


class TestQueryHumanHoursDetail:
    def test_returns_intervals_for_date(self, db):
        today = datetime.now().astimezone()
        start = today.replace(hour=9, minute=0, second=0, microsecond=0)
        end = start + timedelta(hours=3)
        db.execute(
            "INSERT INTO human_activity_events (start_time, end_time) VALUES (?, ?)",
            (start.astimezone(UTC).isoformat(), end.astimezone(UTC).isoformat()),
        )
        db.commit()

        date_str = today.strftime("%Y-%m-%d")
        result = human_activity.query_human_hours_detail(db, date_str)

        assert len(result) == 1
        assert abs(result[0]["hours"] - 3.0) < 0.01
        assert "start" in result[0]
        assert "end" in result[0]

    def test_clamps_to_date_boundaries(self, db):
        # Interval starts before target date (yesterday 23:00 to today 02:00)
        today = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
        start = (today - timedelta(hours=1)).astimezone(UTC)  # Yesterday 23:00
        end = (today + timedelta(hours=2)).astimezone(UTC)  # Today 02:00
        db.execute(
            "INSERT INTO human_activity_events (start_time, end_time) VALUES (?, ?)",
            (start.isoformat(), end.isoformat()),
        )
        db.commit()

        date_str = today.strftime("%Y-%m-%d")
        result = human_activity.query_human_hours_detail(db, date_str)

        assert len(result) == 1
        # Should be clamped to 00:00-02:00 = 2 hours
        assert abs(result[0]["hours"] - 2.0) < 0.01

    def test_empty_for_different_date(self, db):
        result = human_activity.query_human_hours_detail(db, "2020-01-01")
        assert result == []


class TestCleanupOldEvents:
    def test_deletes_old_events(self, db):
        old_time = (datetime.now(UTC) - timedelta(days=200)).isoformat()
        recent_time = datetime.now(UTC).isoformat()
        db.execute(
            "INSERT INTO human_activity_events (start_time, end_time) VALUES (?, ?)",
            (old_time, old_time),
        )
        db.execute(
            "INSERT INTO human_activity_events (start_time, end_time) VALUES (?, ?)",
            (recent_time, recent_time),
        )
        db.commit()

        human_activity.cleanup_old_events(db, retention_days=180)

        rows = db.execute("SELECT * FROM human_activity_events").fetchall()
        assert len(rows) == 1


# --- HumanActivityTracker Tests ---


class TestHumanActivityTracker:
    def test_tick_starts_interval_when_active(self, db):
        from orchestrator.core.human_tracker import HumanActivityTracker

        # Create a mock ConnectionFactory
        factory = MagicMock()
        factory.connection.return_value.__enter__ = MagicMock(return_value=db)
        factory.connection.return_value.__exit__ = MagicMock(return_value=False)

        tracker = HumanActivityTracker(factory)
        tracker._last_heartbeat = time.time()  # Active now

        tracker._tick()

        # Should have started an interval
        open_iv = human_activity.get_open_interval(db)
        assert open_iv is not None

    def test_tick_closes_interval_when_idle(self, db):
        from orchestrator.core.human_tracker import HumanActivityTracker

        factory = MagicMock()
        factory.connection.return_value.__enter__ = MagicMock(return_value=db)
        factory.connection.return_value.__exit__ = MagicMock(return_value=False)

        # Start with an open interval
        human_activity.start_interval(db)
        db.commit()

        tracker = HumanActivityTracker(factory)
        tracker._last_heartbeat = time.time() - 400  # Idle for 400s (> 300s timeout)

        tracker._tick()

        # Should have closed the interval
        open_iv = human_activity.get_open_interval(db)
        assert open_iv is None

    def test_tick_no_op_when_already_active(self, db):
        from orchestrator.core.human_tracker import HumanActivityTracker

        factory = MagicMock()
        factory.connection.return_value.__enter__ = MagicMock(return_value=db)
        factory.connection.return_value.__exit__ = MagicMock(return_value=False)

        # Start with an open interval
        human_activity.start_interval(db)
        db.commit()

        tracker = HumanActivityTracker(factory)
        tracker._last_heartbeat = time.time()  # Active now

        # Should not create a second interval
        tracker._tick()
        rows = db.execute("SELECT * FROM human_activity_events WHERE end_time IS NULL").fetchall()
        assert len(rows) == 1

    def test_tick_no_op_when_already_idle(self, db):
        from orchestrator.core.human_tracker import HumanActivityTracker

        factory = MagicMock()
        factory.connection.return_value.__enter__ = MagicMock(return_value=db)
        factory.connection.return_value.__exit__ = MagicMock(return_value=False)

        tracker = HumanActivityTracker(factory)
        tracker._last_heartbeat = 0  # Never active

        # Should not do anything
        tracker._tick()
        rows = db.execute("SELECT * FROM human_activity_events").fetchall()
        assert len(rows) == 0

    def test_tick_handles_db_error(self, db):
        from orchestrator.core.human_tracker import HumanActivityTracker

        factory = MagicMock()
        factory.connection.side_effect = Exception("DB error")

        tracker = HumanActivityTracker(factory)
        tracker._last_heartbeat = time.time()

        # Should not raise
        with pytest.raises(Exception, match="DB error"):
            tracker._tick()

    def test_record_heartbeat_updates_timestamp(self):
        from orchestrator.core.human_tracker import HumanActivityTracker

        factory = MagicMock()
        tracker = HumanActivityTracker(factory)
        assert tracker._last_heartbeat == 0

        tracker.record_heartbeat()
        assert tracker._last_heartbeat > 0

    def test_close_open_interval(self, db):
        from orchestrator.core.human_tracker import HumanActivityTracker

        factory = MagicMock()
        factory.connection.return_value.__enter__ = MagicMock(return_value=db)
        factory.connection.return_value.__exit__ = MagicMock(return_value=False)

        # Start an open interval
        human_activity.start_interval(db)
        db.commit()

        tracker = HumanActivityTracker(factory)
        tracker._last_heartbeat = time.time()

        tracker._close_open_interval()

        # Should be closed
        open_iv = human_activity.get_open_interval(db)
        assert open_iv is None
