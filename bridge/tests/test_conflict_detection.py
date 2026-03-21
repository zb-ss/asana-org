"""Tests for remote conflict detection during apply.

Verifies that _apply_mutation_via_api checks the remote Asana task state
against the local baseline snapshot before executing mutations.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import pytest

from asana_org_bridge.asana_client import AsanaResult
from asana_org_bridge.db import Database, MigrationManager
from asana_org_bridge.models import PendingMutation, TaskSnapshot
from asana_org_bridge.sync import SyncEngine


class AuthManagerProto(Protocol):
    def get_pat(self) -> str | None: ...


@pytest.fixture
def temp_db_path(tmp_path: Path) -> Path:
    return tmp_path / "conflict_detect.db"


@pytest.fixture
def database(temp_db_path: Path) -> Database:
    db = Database(db_path=temp_db_path, echo=False)
    MigrationManager(db).migrate()
    return db


@pytest.fixture
def auth_manager() -> AuthManagerProto:
    class MockAuthManager:
        def get_pat(self) -> str:
            return "mock_pat"

    return MockAuthManager()


def _make_fake_client(
    remote_task: dict[str, Any],
    update_success: bool = True,
) -> Any:
    """Build a FakeAsanaClient that returns controlled data for conflict tests."""

    class FakeAsanaClient:
        def __init__(self) -> None:
            self.update_calls: list[dict[str, Any]] = []

        def get_task(
            self, task_gid: str, opt_fields: str | None = None
        ) -> dict[str, Any]:
            return remote_task

        def update_task(
            self,
            task_gid: str,
            completed: bool | None = None,
            due_on: str | None = None,
            start_on: str | None = None,
            name: str | None = None,
        ) -> AsanaResult:
            self.update_calls.append(
                {
                    "task_gid": task_gid,
                    "completed": completed,
                    "due_on": due_on,
                    "start_on": start_on,
                    "name": name,
                }
            )
            if update_success:
                return AsanaResult(
                    success=True,
                    data={"gid": task_gid},
                    status_code=200,
                )
            return AsanaResult(
                success=False,
                error="API failure",
                status_code=500,
            )

        def add_comment(self, task_gid: str, text: str) -> AsanaResult:
            return AsanaResult(
                success=True,
                data={"story_gid": "story_001", "task_gid": task_gid},
                status_code=201,
            )

        def move_task_to_section(
            self, task_gid: str, section_gid: str
        ) -> AsanaResult:
            return AsanaResult(success=True, data={}, status_code=200)

    return FakeAsanaClient()


# ---- Helper to seed a snapshot ----

def _seed_snapshot(
    database: Database,
    task_gid: str = "task_001",
    completed: bool = False,
    due_on: str | None = "2026-03-15",
    start_on: str | None = "2026-03-10",
    modified_at: datetime | None = None,
) -> datetime:
    """Insert a TaskSnapshot and return its modified_at."""
    ts = modified_at or datetime.now(UTC) - timedelta(hours=1)
    with database.session() as session:
        session.add(
            TaskSnapshot(
                gid=task_gid,
                permalink_url=f"https://app.asana.com/0/0/{task_gid}",
                name="Test Task",
                completed=completed,
                due_on=due_on,
                start_on=start_on,
                modified_at=ts,
                project_gid="proj_001",
                section_gid="sect_001",
                section_name="To Do",
            )
        )
    return ts


# ---- Tests ----


def test_remote_unchanged_mutation_applies_normally(
    database: Database, auth_manager: AuthManagerProto
) -> None:
    """When the remote task has not changed since baseline, mutation applies."""
    baseline_ts = _seed_snapshot(database, completed=False)

    # Remote has same modified_at as baseline (unchanged)
    remote_task = {
        "gid": "task_001",
        "completed": False,
        "due_on": "2026-03-15",
        "start_on": "2026-03-10",
        "modified_at": baseline_ts.isoformat(),
    }
    fake_client = _make_fake_client(remote_task)

    engine = SyncEngine(db=database, auth_manager=auth_manager, use_mock=False)  # type: ignore[arg-type]
    engine._asana_client = fake_client  # type: ignore[assignment]

    with database.session() as session:
        session.add(
            PendingMutation(
                task_gid="task_001",
                operation="update_status",
                payload={"task_gid": "task_001", "completed": True},
                idempotency_key="no-conflict-001",
                status="pending",
            )
        )

    result = engine.apply()
    assert result.applied == 1
    assert result.failed == 0
    assert len(fake_client.update_calls) == 1


def test_remote_changed_same_field_blocks_mutation(
    database: Database, auth_manager: AuthManagerProto
) -> None:
    """When remote changed the same field we want to mutate, mutation is blocked."""
    baseline_ts = _seed_snapshot(database, completed=False)

    # Remote completed was changed from False to True (same field we want to set)
    remote_task = {
        "gid": "task_001",
        "completed": True,  # Changed from baseline (False)
        "due_on": "2026-03-15",
        "start_on": "2026-03-10",
        "modified_at": (baseline_ts + timedelta(minutes=30)).isoformat(),
    }
    fake_client = _make_fake_client(remote_task)

    engine = SyncEngine(db=database, auth_manager=auth_manager, use_mock=False)  # type: ignore[arg-type]
    engine._asana_client = fake_client  # type: ignore[assignment]

    with database.session() as session:
        session.add(
            PendingMutation(
                task_gid="task_001",
                operation="update_status",
                payload={"task_gid": "task_001", "completed": True},
                idempotency_key="conflict-status-001",
                status="pending",
            )
        )

    result = engine.apply()
    assert result.applied == 0
    assert result.failed == 1
    # update_task should NOT have been called
    assert len(fake_client.update_calls) == 0


def test_remote_changed_different_field_applies_with_warning(
    database: Database, auth_manager: AuthManagerProto
) -> None:
    """When remote changed a different field, mutation applies with a warning."""
    baseline_ts = _seed_snapshot(database, completed=False, due_on="2026-03-15")

    # Remote changed completed (unrelated) but we're updating due_on
    # Since completed didn't change, and due_on didn't change either,
    # but modified_at is newer -- this is an unrelated field change
    remote_task = {
        "gid": "task_001",
        "completed": False,  # Same as baseline
        "due_on": "2026-03-15",  # Same as baseline
        "start_on": "2026-03-10",  # Same as baseline
        "modified_at": (baseline_ts + timedelta(minutes=30)).isoformat(),
    }
    fake_client = _make_fake_client(remote_task)

    engine = SyncEngine(db=database, auth_manager=auth_manager, use_mock=False)  # type: ignore[arg-type]
    engine._asana_client = fake_client  # type: ignore[assignment]

    with database.session() as session:
        session.add(
            PendingMutation(
                task_gid="task_001",
                operation="update_status",
                payload={"task_gid": "task_001", "completed": True},
                idempotency_key="warn-unrelated-001",
                status="pending",
            )
        )

    result = engine.apply()
    assert result.applied == 1
    assert result.failed == 0
    # update_task SHOULD have been called
    assert len(fake_client.update_calls) == 1


def test_no_snapshot_skips_conflict_check(
    database: Database, auth_manager: AuthManagerProto
) -> None:
    """When no baseline snapshot exists, mutation applies without conflict check."""
    # No snapshot seeded -- first-time apply scenario
    remote_task = {
        "gid": "task_new",
        "completed": True,
        "due_on": "2026-03-20",
        "start_on": None,
        "modified_at": datetime.now(UTC).isoformat(),
    }
    fake_client = _make_fake_client(remote_task)

    engine = SyncEngine(db=database, auth_manager=auth_manager, use_mock=False)  # type: ignore[arg-type]
    engine._asana_client = fake_client  # type: ignore[assignment]

    with database.session() as session:
        session.add(
            PendingMutation(
                task_gid="task_new",
                operation="update_status",
                payload={"task_gid": "task_new", "completed": False},
                idempotency_key="no-snapshot-001",
                status="pending",
            )
        )

    result = engine.apply()
    assert result.applied == 1
    assert result.failed == 0
    assert len(fake_client.update_calls) == 1


def test_mock_mode_skips_conflict_detection(
    database: Database, auth_manager: AuthManagerProto
) -> None:
    """In mock mode, conflict detection is skipped entirely."""
    _seed_snapshot(database, completed=False)

    engine = SyncEngine(db=database, auth_manager=auth_manager, use_mock=True)  # type: ignore[arg-type]

    with database.session() as session:
        session.add(
            PendingMutation(
                task_gid="task_001",
                operation="update_status",
                payload={"task_gid": "task_001", "completed": True},
                idempotency_key="mock-apply-001",
                status="pending",
            )
        )

    result = engine.apply()
    assert result.applied == 1
    assert result.failed == 0


def test_date_field_conflict_blocks_mutation(
    database: Database, auth_manager: AuthManagerProto
) -> None:
    """Date change is blocked when the same date field changed remotely."""
    baseline_ts = _seed_snapshot(
        database, due_on="2026-03-15", start_on="2026-03-10"
    )

    # Remote changed due_on (same field we want to update)
    remote_task = {
        "gid": "task_001",
        "completed": False,
        "due_on": "2026-03-20",  # Changed from baseline
        "start_on": "2026-03-10",
        "modified_at": (baseline_ts + timedelta(minutes=10)).isoformat(),
    }
    fake_client = _make_fake_client(remote_task)

    engine = SyncEngine(db=database, auth_manager=auth_manager, use_mock=False)  # type: ignore[arg-type]
    engine._asana_client = fake_client  # type: ignore[assignment]

    with database.session() as session:
        session.add(
            PendingMutation(
                task_gid="task_001",
                operation="update_dates",
                payload={
                    "task_gid": "task_001",
                    "due_on": "2026-04-01",
                },
                idempotency_key="conflict-date-001",
                status="pending",
            )
        )

    result = engine.apply()
    assert result.applied == 0
    assert result.failed == 1
    assert len(fake_client.update_calls) == 0


def test_apply_from_json_conflict_returns_conflict_status(
    database: Database, auth_manager: AuthManagerProto
) -> None:
    """apply_from_json should return conflict status in JSON envelope."""
    baseline_ts = _seed_snapshot(database, completed=False)

    remote_task = {
        "gid": "task_001",
        "completed": True,  # Conflicting change
        "due_on": "2026-03-15",
        "start_on": "2026-03-10",
        "modified_at": (baseline_ts + timedelta(minutes=30)).isoformat(),
    }
    fake_client = _make_fake_client(remote_task)

    engine = SyncEngine(db=database, auth_manager=auth_manager, use_mock=False)  # type: ignore[arg-type]
    engine._asana_client = fake_client  # type: ignore[assignment]

    result = engine.apply_from_json(
        {
            "version": "1",
            "command": "sync-apply",
            "mutations": [
                {
                    "idempotency_key": "json-conflict-001",
                    "type": "status_change",
                    "payload": {
                        "task_gid": "task_001",
                        "completed": True,
                    },
                }
            ],
        }
    )

    assert result.failed == 1
    assert result.applied == 0

    results_data = result.results_json["data"]["results"]
    assert len(results_data) == 1
    assert results_data[0]["status"] == "conflict"
    assert results_data[0]["task_gid"] == "task_001"
    assert "conflict" in results_data[0]
    assert results_data[0]["conflict"]["field"] == "completed"

    # Summary should include conflicts count
    summary = result.results_json["data"]["summary"]
    assert summary["conflicts"] == 1


def test_apply_from_json_warning_on_unrelated_change(
    database: Database, auth_manager: AuthManagerProto
) -> None:
    """apply_from_json includes a warning when remote changed unrelated fields."""
    baseline_ts = _seed_snapshot(database, completed=False, due_on="2026-03-15")

    # Remote is newer, but the field we're mutating (completed) is unchanged
    remote_task = {
        "gid": "task_001",
        "completed": False,  # Same as baseline
        "due_on": "2026-03-15",
        "start_on": "2026-03-10",
        "modified_at": (baseline_ts + timedelta(minutes=30)).isoformat(),
    }
    fake_client = _make_fake_client(remote_task)

    engine = SyncEngine(db=database, auth_manager=auth_manager, use_mock=False)  # type: ignore[arg-type]
    engine._asana_client = fake_client  # type: ignore[assignment]

    result = engine.apply_from_json(
        {
            "version": "1",
            "command": "sync-apply",
            "mutations": [
                {
                    "idempotency_key": "json-warn-001",
                    "type": "status_change",
                    "payload": {
                        "task_gid": "task_001",
                        "completed": True,
                    },
                }
            ],
        }
    )

    assert result.applied == 1
    assert result.failed == 0

    results_data = result.results_json["data"]["results"]
    assert len(results_data) == 1
    assert results_data[0]["status"] == "applied"
    # The warning should be present in the result or details
    assert "warning" in results_data[0] or "warning" in results_data[0].get(
        "details", {}
    )


def test_multiple_mutations_mixed_conflict_states(
    database: Database, auth_manager: AuthManagerProto
) -> None:
    """Multiple mutations: one conflict, one clean apply, one with warning."""
    baseline_ts = datetime.now(UTC) - timedelta(hours=1)

    # Seed snapshots for two tasks
    _seed_snapshot(database, task_gid="task_conflict", completed=False, modified_at=baseline_ts)
    _seed_snapshot(database, task_gid="task_clean", completed=False, modified_at=baseline_ts)

    class MultiFakeClient:
        """Returns different remote states per task_gid."""

        def __init__(self) -> None:
            self.update_calls: list[str] = []

        def get_task(
            self, task_gid: str, opt_fields: str | None = None
        ) -> dict[str, Any]:
            if task_gid == "task_conflict":
                return {
                    "gid": "task_conflict",
                    "completed": True,  # Remote changed same field
                    "due_on": "2026-03-15",
                    "start_on": "2026-03-10",
                    "modified_at": (baseline_ts + timedelta(minutes=30)).isoformat(),
                }
            # task_clean: remote not changed
            return {
                "gid": "task_clean",
                "completed": False,
                "due_on": "2026-03-15",
                "start_on": "2026-03-10",
                "modified_at": baseline_ts.isoformat(),
            }

        def update_task(
            self,
            task_gid: str,
            completed: bool | None = None,
            due_on: str | None = None,
            start_on: str | None = None,
            name: str | None = None,
        ) -> AsanaResult:
            self.update_calls.append(task_gid)
            return AsanaResult(success=True, data={"gid": task_gid}, status_code=200)

    fake_client = MultiFakeClient()
    engine = SyncEngine(db=database, auth_manager=auth_manager, use_mock=False)  # type: ignore[arg-type]
    engine._asana_client = fake_client  # type: ignore[assignment]

    result = engine.apply_from_json(
        {
            "version": "1",
            "command": "sync-apply",
            "mutations": [
                {
                    "idempotency_key": "multi-conflict-001",
                    "type": "status_change",
                    "payload": {
                        "task_gid": "task_conflict",
                        "completed": True,
                    },
                },
                {
                    "idempotency_key": "multi-clean-001",
                    "type": "status_change",
                    "payload": {
                        "task_gid": "task_clean",
                        "completed": True,
                    },
                },
            ],
        }
    )

    assert result.applied == 1
    assert result.failed == 1

    results_data = result.results_json["data"]["results"]
    statuses = [r["status"] for r in results_data]
    assert "conflict" in statuses
    assert "applied" in statuses

    # Only the clean task should have been updated via API
    assert fake_client.update_calls == ["task_clean"]

    # Summary
    summary = result.results_json["data"]["summary"]
    assert summary["conflicts"] == 1
    assert summary["applied"] == 1
    assert summary["failed"] == 1


def test_comment_add_skips_conflict_detection(
    database: Database, auth_manager: AuthManagerProto
) -> None:
    """Comment additions should not trigger conflict detection (additive)."""
    baseline_ts = _seed_snapshot(database, completed=False)

    # Remote is modified (normally would trigger conflict check),
    # but comment_add should skip it
    remote_task = {
        "gid": "task_001",
        "completed": True,  # Changed
        "due_on": "2026-03-15",
        "start_on": "2026-03-10",
        "modified_at": (baseline_ts + timedelta(minutes=30)).isoformat(),
    }
    fake_client = _make_fake_client(remote_task)

    engine = SyncEngine(db=database, auth_manager=auth_manager, use_mock=False)  # type: ignore[arg-type]
    engine._asana_client = fake_client  # type: ignore[assignment]

    with database.session() as session:
        session.add(
            PendingMutation(
                task_gid="task_001",
                operation="append_comment",
                payload={"task_gid": "task_001", "text": "Hello"},
                idempotency_key="comment-no-conflict-001",
                status="pending",
            )
        )

    result = engine.apply()
    assert result.applied == 1
    assert result.failed == 0


def test_start_on_field_conflict_blocks_date_mutation(
    database: Database, auth_manager: AuthManagerProto
) -> None:
    """start_on change is blocked when start_on changed remotely."""
    baseline_ts = _seed_snapshot(
        database, due_on="2026-03-15", start_on="2026-03-10"
    )

    remote_task = {
        "gid": "task_001",
        "completed": False,
        "due_on": "2026-03-15",
        "start_on": "2026-03-12",  # Changed from baseline
        "modified_at": (baseline_ts + timedelta(minutes=10)).isoformat(),
    }
    fake_client = _make_fake_client(remote_task)

    engine = SyncEngine(db=database, auth_manager=auth_manager, use_mock=False)  # type: ignore[arg-type]
    engine._asana_client = fake_client  # type: ignore[assignment]

    with database.session() as session:
        session.add(
            PendingMutation(
                task_gid="task_001",
                operation="update_dates",
                payload={
                    "task_gid": "task_001",
                    "start_on": "2026-03-01",
                },
                idempotency_key="conflict-start-001",
                status="pending",
            )
        )

    result = engine.apply()
    assert result.applied == 0
    assert result.failed == 1
    assert len(fake_client.update_calls) == 0
