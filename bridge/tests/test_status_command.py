"""Tests for the status command."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from asana_org_bridge.auth import AuthManager
from asana_org_bridge.db import Database, MigrationManager
from asana_org_bridge.models import PendingMutation, SyncRun, TaskSnapshot
from asana_org_bridge.sync import SyncEngine


@pytest.fixture
def temp_db_path(tmp_path: Path) -> Path:
    """Create temporary database path."""
    return tmp_path / "test_status.db"


@pytest.fixture
def database(temp_db_path: Path) -> Database:
    """Create database instance with migrations applied."""
    db = Database(db_path=temp_db_path, echo=False)
    migrator = MigrationManager(db)
    migrator.migrate()
    return db


@pytest.fixture
def empty_database(tmp_path: Path) -> Database:
    """Create database instance without migrations (uninitialized)."""
    db_path = tmp_path / "test_empty.db"
    return Database(db_path=db_path, echo=False)


@pytest.fixture
def engine(database: Database) -> SyncEngine:
    """Create a SyncEngine in mock mode."""
    auth = AuthManager()
    return SyncEngine(db=database, auth_manager=auth, use_mock=True)


@pytest.fixture
def empty_engine(empty_database: Database) -> SyncEngine:
    """Create a SyncEngine with uninitialized DB."""
    auth = AuthManager()
    return SyncEngine(db=empty_database, auth_manager=auth, use_mock=True)


def test_status_empty_db_returns_zeros(engine: SyncEngine) -> None:
    """Test status on initialized but empty DB returns zeros/nulls."""
    result = engine.get_status()

    assert result["last_pull_at"] is None
    assert result["last_apply_at"] is None
    assert result["snapshot_count"] == 0
    assert result["unique_tasks"] == 0
    assert result["pending_mutations"] == 0
    assert result["failed_mutations"] == 0
    assert result["total_sync_runs"] == 0
    assert result["schema_version"] is not None
    assert result["db_path"] is not None
    assert result["db_size_bytes"] is not None
    assert result["db_size_bytes"] > 0


def test_status_uninitialized_db_no_errors(empty_engine: SyncEngine) -> None:
    """Test status on uninitialized DB returns zeros/nulls without errors."""
    result = empty_engine.get_status()

    assert result["last_pull_at"] is None
    assert result["last_apply_at"] is None
    assert result["snapshot_count"] == 0
    assert result["unique_tasks"] == 0
    assert result["pending_mutations"] == 0
    assert result["failed_mutations"] == 0
    assert result["total_sync_runs"] == 0
    assert result["schema_version"] is None
    assert result["db_path"] is not None


def test_status_with_records(engine: SyncEngine) -> None:
    """Test status with actual data returns correct counts."""
    now = datetime.now(UTC)

    with engine.db.session() as session:
        # Add task snapshots
        for i in range(3):
            session.add(
                TaskSnapshot(
                    gid=f"task_{i:03d}",
                    permalink_url=f"https://app.asana.com/0/0/task_{i:03d}",
                    name=f"Task {i}",
                    completed=False,
                    modified_at=now,
                )
            )

        # Add a duplicate gid snapshot (same task, different snapshot)
        session.add(
            TaskSnapshot(
                gid="task_000",
                permalink_url="https://app.asana.com/0/0/task_000",
                name="Task 0 Updated",
                completed=True,
                modified_at=now,
            )
        )

        # Add sync runs
        session.add(
            SyncRun(
                run_type="pull",
                status="completed",
                tasks_pulled=3,
                completed_at=now,
            )
        )
        session.add(
            SyncRun(
                run_type="apply",
                status="completed",
                mutations_applied=1,
                completed_at=now,
            )
        )
        session.add(
            SyncRun(
                run_type="pull",
                status="failed",
                completed_at=now,
            )
        )

        # Add pending mutations
        session.add(
            PendingMutation(
                task_gid="task_000",
                operation="update_status",
                payload={"completed": True},
                status="pending",
            )
        )
        session.add(
            PendingMutation(
                task_gid="task_001",
                operation="update_dates",
                payload={"due_on": "2026-03-01"},
                status="pending",
            )
        )
        session.add(
            PendingMutation(
                task_gid="task_002",
                operation="update_status",
                payload={"completed": True},
                status="failed",
                error_message="API error",
            )
        )

    result = engine.get_status()

    assert result["snapshot_count"] == 4  # 3 unique + 1 duplicate
    assert result["unique_tasks"] == 3
    assert result["pending_mutations"] == 2
    assert result["failed_mutations"] == 1
    assert result["total_sync_runs"] == 3
    assert result["last_pull_at"] is not None
    assert result["last_apply_at"] is not None


def test_status_json_output_format(engine: SyncEngine) -> None:
    """Test that status data matches expected JSON schema structure."""
    result = engine.get_status()

    # Wrap in the envelope format like the CLI command does
    response = {
        "version": "1",
        "command": "status",
        "status": "success",
        "data": {
            "sync_status": result,
        },
    }

    # Verify it serializes to valid JSON
    json_str = json.dumps(response, indent=2)
    parsed = json.loads(json_str)

    assert parsed["version"] == "1"
    assert parsed["command"] == "status"
    assert parsed["status"] == "success"
    assert "sync_status" in parsed["data"]

    sync_status = parsed["data"]["sync_status"]
    expected_keys = {
        "last_pull_at",
        "last_apply_at",
        "snapshot_count",
        "unique_tasks",
        "pending_mutations",
        "failed_mutations",
        "total_sync_runs",
        "schema_version",
        "db_size_bytes",
        "db_path",
    }
    assert set(sync_status.keys()) == expected_keys


def test_status_pending_failed_counts_correct(engine: SyncEngine) -> None:
    """Test that pending and failed mutation counts are accurate."""
    with engine.db.session() as session:
        # Add various mutation statuses
        for status_val in ["pending", "pending", "pending", "failed", "failed", "completed"]:
            session.add(
                PendingMutation(
                    task_gid="task_000",
                    operation="update_status",
                    payload={"completed": True},
                    status=status_val,
                )
            )

    result = engine.get_status()
    assert result["pending_mutations"] == 3
    assert result["failed_mutations"] == 2


def test_status_last_pull_reflects_most_recent(engine: SyncEngine) -> None:
    """Test that last_pull_at reflects the most recent completed pull."""
    earlier = datetime(2026, 1, 1, tzinfo=UTC)
    later = datetime(2026, 3, 15, tzinfo=UTC)

    with engine.db.session() as session:
        session.add(
            SyncRun(
                run_type="pull",
                status="completed",
                completed_at=earlier,
            )
        )
        session.add(
            SyncRun(
                run_type="pull",
                status="completed",
                completed_at=later,
            )
        )
        # A failed pull should not count
        session.add(
            SyncRun(
                run_type="pull",
                status="failed",
                completed_at=datetime(2026, 6, 1, tzinfo=UTC),
            )
        )

    result = engine.get_status()
    assert result["last_pull_at"] is not None
    assert result["last_pull_at"] == later.replace(tzinfo=None).isoformat()


def test_status_schema_version_matches_current(engine: SyncEngine) -> None:
    """Test that schema_version matches the current migration version."""
    result = engine.get_status()
    expected_version = max(MigrationManager.MIGRATIONS.keys())
    assert result["schema_version"] == expected_version
