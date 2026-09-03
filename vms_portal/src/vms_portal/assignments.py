from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Assignment:
    instance_id: str
    assignee: str
    updated_at: datetime
    updated_by: str


class AssignmentRepository:
    def __init__(self, database_path: str | Path) -> None:
        self._database_path = Path(database_path)
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def upsert(
        self,
        instance_id: str,
        assignee: str,
        *,
        updated_by: str,
        updated_at: datetime,
    ) -> Assignment:
        timestamp = updated_at.astimezone(UTC)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO vm_assignments (instance_id, assignee, updated_at, updated_by)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(instance_id) DO UPDATE SET
                    assignee = excluded.assignee,
                    updated_at = excluded.updated_at,
                    updated_by = excluded.updated_by
                """,
                (instance_id, assignee, timestamp.isoformat(), updated_by),
            )
        return Assignment(instance_id, assignee, timestamp, updated_by)

    def get(self, instance_id: str) -> Assignment | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT instance_id, assignee, updated_at, updated_by
                FROM vm_assignments
                WHERE instance_id = ?
                """,
                (instance_id,),
            ).fetchone()
        return _assignment_from_row(row) if row else None

    def get_many(self, instance_ids: Iterable[str]) -> Mapping[str, Assignment]:
        requested = tuple(dict.fromkeys(instance_ids))
        if not requested:
            return {}
        placeholders = ",".join("?" for _ in requested)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT instance_id, assignee, updated_at, updated_by
                FROM vm_assignments
                WHERE instance_id IN ({placeholders})
                """,  # nosec B608: placeholders are generated, values remain parameterized
                requested,
            ).fetchall()
        return {
            assignment.instance_id: assignment
            for row in rows
            if (assignment := _assignment_from_row(row))
        }

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._database_path)

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS vm_assignments (
                    instance_id TEXT PRIMARY KEY,
                    assignee TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    updated_by TEXT NOT NULL
                )
                """
            )


def _assignment_from_row(row: tuple[str, str, str, str]) -> Assignment:
    return Assignment(
        instance_id=row[0],
        assignee=row[1],
        updated_at=datetime.fromisoformat(row[2]).astimezone(UTC),
        updated_by=row[3],
    )
