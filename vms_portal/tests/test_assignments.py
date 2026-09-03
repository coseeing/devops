from __future__ import annotations

from datetime import UTC, datetime

from vms_portal.assignments import AssignmentRepository


def test_upsert_persists_assignment_across_repository_instances(tmp_path) -> None:
    database = tmp_path / "portal.db"
    updated_at = datetime(2026, 8, 29, 3, 4, 5, tzinfo=UTC)

    AssignmentRepository(database).upsert(
        "i-1234567890abcdef0",
        "Anson",
        updated_by="admin",
        updated_at=updated_at,
    )

    assignment = AssignmentRepository(database).get("i-1234567890abcdef0")
    assert assignment is not None
    assert assignment.instance_id == "i-1234567890abcdef0"
    assert assignment.assignee == "Anson"
    assert assignment.updated_by == "admin"
    assert assignment.updated_at == updated_at


def test_upsert_keeps_empty_assignee_as_explicit_metadata(tmp_path) -> None:
    repository = AssignmentRepository(tmp_path / "portal.db")
    repository.upsert(
        "i-1234567890abcdef0",
        "",
        updated_by="admin",
        updated_at=datetime(2026, 8, 29, tzinfo=UTC),
    )

    assignment = repository.get("i-1234567890abcdef0")
    assert assignment is not None
    assert assignment.assignee == ""


def test_get_many_returns_only_requested_existing_assignments(tmp_path) -> None:
    repository = AssignmentRepository(tmp_path / "portal.db")
    now = datetime(2026, 8, 29, tzinfo=UTC)
    repository.upsert("i-one", "One", updated_by="admin", updated_at=now)
    repository.upsert("i-two", "Two", updated_by="admin", updated_at=now)

    result = repository.get_many(["i-two", "i-missing"])

    assert list(result) == ["i-two"]
    assert result["i-two"].assignee == "Two"
