"""Tests for notification repository functions."""

from orchestrator.state.repositories import notifications as repo


def test_delete_notifications_by_ids(db):
    """Create 5 notifications, delete 3 by ID, verify 2 remain."""
    created = []
    for i in range(5):
        n = repo.create_notification(db, message=f"msg {i}")
        created.append(n)

    ids_to_delete = [created[0].id, created[2].id, created[4].id]
    deleted_count = repo.delete_notifications_by_ids(db, ids_to_delete)

    assert deleted_count == 3
    remaining = repo.list_notifications(db)
    assert len(remaining) == 2
    remaining_ids = {n.id for n in remaining}
    assert created[1].id in remaining_ids
    assert created[3].id in remaining_ids


def test_delete_notifications_by_ids_empty_list(db):
    """Empty list returns 0 and deletes nothing."""
    repo.create_notification(db, message="keep me")
    deleted_count = repo.delete_notifications_by_ids(db, [])

    assert deleted_count == 0
    assert len(repo.list_notifications(db)) == 1


def test_undismiss_notification(db):
    """Create, dismiss, undismiss — verify dismissed=False and dismissed_at=None."""
    n = repo.create_notification(db, message="test undismiss")
    repo.dismiss_notification(db, n.id)

    dismissed = repo.get_notification(db, n.id)
    assert dismissed.dismissed is True
    assert dismissed.dismissed_at is not None

    restored = repo.undismiss_notification(db, n.id)
    assert restored.dismissed is False
    assert restored.dismissed_at is None


def test_delete_dismissed_notifications(db):
    """Dismiss some, bulk delete dismissed, verify only active remain."""
    n1 = repo.create_notification(db, message="active")
    n2 = repo.create_notification(db, message="to dismiss 1")
    n3 = repo.create_notification(db, message="to dismiss 2")

    repo.dismiss_notification(db, n2.id)
    repo.dismiss_notification(db, n3.id)

    deleted_count = repo.delete_dismissed_notifications(db)
    assert deleted_count == 2

    remaining = repo.list_notifications(db)
    assert len(remaining) == 1
    assert remaining[0].id == n1.id
