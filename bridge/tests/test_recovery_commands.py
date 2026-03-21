"""Tests for recovery commands: reconcile, rebuild-cache, validate."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import pytest

from asana_org_bridge.db import Database, MigrationManager
from asana_org_bridge.models import TaskSnapshot
from asana_org_bridge.sync import SyncEngine


class AuthManagerProto(Protocol):
    def get_pat(self) -> str | None: ...


@pytest.fixture
def temp_db_path(tmp_path: Path) -> Path:
    return tmp_path / "recovery_test.db"


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


@pytest.fixture
def engine(database: Database, auth_manager: AuthManagerProto) -> SyncEngine:
    return SyncEngine(db=database, auth_manager=auth_manager, use_mock=True)  # type: ignore[arg-type]


def _insert_snapshot(
    database: Database,
    gid: str,
    name: str = "Test Task",
    completed: bool = False,
    due_on: str | None = "2026-03-15",
    start_on: str | None = "2026-03-10",
    modified_at: datetime | None = None,
) -> None:
    """Insert a TaskSnapshot for testing."""
    with database.session() as session:
        snapshot = TaskSnapshot(
            gid=gid,
            permalink_url=f"https://app.asana.com/0/0/{gid}",
            name=name,
            completed=completed,
            due_on=due_on,
            start_on=start_on,
            notes="Some notes",
            modified_at=modified_at or datetime.now(UTC),
        )
        session.add(snapshot)


# --- Reconcile Tests ---


def test_reconcile_detects_drift_when_snapshot_differs(
    engine: SyncEngine, database: Database
) -> None:
    """Reconcile detects drift when snapshot fields differ from mock data."""
    # Insert a snapshot that differs from mock data for task_001
    # Mock task_001: completed=False, due_on="2026-02-28", start_on="2026-02-25"
    _insert_snapshot(
        database,
        gid="task_001",
        name="Review project proposal",
        completed=True,  # differs from mock (False)
        due_on="2026-02-28",
        start_on="2026-02-25",
    )

    result = engine.reconcile()

    assert result.summary["total_checked"] == 1
    assert result.summary["drifted"] >= 1
    # Should detect completed field drift
    completed_drifts = [
        d for d in result.drifted_tasks if d["field"] == "completed"
    ]
    assert len(completed_drifts) == 1
    assert completed_drifts[0]["snapshot_value"] is True
    assert completed_drifts[0]["remote_value"] is False


def test_reconcile_no_drift_when_fields_match(
    engine: SyncEngine, database: Database
) -> None:
    """Reconcile reports no drift when all fields match mock data."""
    # Insert snapshot matching mock task_001 exactly
    _insert_snapshot(
        database,
        gid="task_001",
        name="Review project proposal",
        completed=False,
        due_on="2026-02-28",
        start_on="2026-02-25",
    )

    result = engine.reconcile()

    assert result.summary["total_checked"] == 1
    assert result.summary["drifted"] == 0
    assert len(result.drifted_tasks) == 0


def test_reconcile_missing_remote(
    engine: SyncEngine, database: Database
) -> None:
    """Reconcile reports tasks present in DB but not in remote/mock."""
    # Insert a snapshot with a GID not in mock data
    _insert_snapshot(database, gid="nonexistent_remote_task", name="Ghost Task")

    result = engine.reconcile()

    assert "nonexistent_remote_task" in result.missing_remote
    assert result.summary["missing"] >= 1


def test_reconcile_multiple_tasks(
    engine: SyncEngine, database: Database
) -> None:
    """Reconcile checks all unique task GIDs in the DB."""
    # task_001 matches, task_002 has drift
    _insert_snapshot(
        database,
        gid="task_001",
        name="Review project proposal",
        completed=False,
        due_on="2026-02-28",
        start_on="2026-02-25",
    )
    # Mock task_002: completed=False, due_on="2026-02-27", start_on=None
    _insert_snapshot(
        database,
        gid="task_002",
        name="Email team about meeting",
        completed=False,
        due_on="2026-03-01",  # differs from mock (2026-02-27)
        start_on=None,
    )

    result = engine.reconcile()

    assert result.summary["total_checked"] == 2
    assert result.summary["drifted"] >= 1


# --- Rebuild Cache Tests ---


def test_rebuild_cache_clears_and_recreates(
    engine: SyncEngine, database: Database
) -> None:
    """Rebuild cache deletes all snapshots and recreates from mock data."""
    # Insert some existing snapshots
    _insert_snapshot(database, gid="old_task_1", name="Old Task 1")
    _insert_snapshot(database, gid="old_task_2", name="Old Task 2")

    result = engine.rebuild_cache()

    assert result.snapshots_deleted == 2
    # Mock data has 5 tasks
    assert result.snapshots_created == 5

    # Verify DB now has the mock tasks
    with database.session() as session:
        count = session.query(TaskSnapshot).count()
        assert count == 5


def test_rebuild_cache_from_empty_db(
    engine: SyncEngine, database: Database
) -> None:
    """Rebuild cache works on an empty database."""
    result = engine.rebuild_cache()

    assert result.snapshots_deleted == 0
    assert result.snapshots_created == 5


# --- Validate Tests ---


def test_validate_detects_mismatches(
    engine: SyncEngine, database: Database
) -> None:
    """Validate detects field mismatches between org and snapshot."""
    _insert_snapshot(
        database,
        gid="task_v1",
        completed=False,
        due_on="2026-03-15",
        start_on="2026-03-10",
    )

    result = engine.validate([
        {
            "gid": "task_v1",
            "completed": True,
            "due_on": "2026-04-01",
            "start_on": "2026-03-10",
        }
    ])

    # Should detect completed and due_on mismatches
    assert len(result.mismatches) == 2
    fields = {m["field"] for m in result.mismatches}
    assert "completed" in fields
    assert "due_on" in fields
    assert result.summary["mismatched"] >= 1


def test_validate_no_mismatches(
    engine: SyncEngine, database: Database
) -> None:
    """Validate reports no mismatches when org and snapshot agree."""
    _insert_snapshot(
        database,
        gid="task_v2",
        completed=False,
        due_on="2026-03-15",
        start_on="2026-03-10",
    )

    result = engine.validate([
        {
            "gid": "task_v2",
            "completed": False,
            "due_on": "2026-03-15",
            "start_on": "2026-03-10",
        }
    ])

    assert len(result.mismatches) == 0
    assert result.summary["valid"] == 1
    assert result.summary["mismatched"] == 0


def test_validate_orphaned_org(
    engine: SyncEngine, database: Database
) -> None:
    """Validate detects org tasks with no corresponding DB snapshot."""
    result = engine.validate([
        {"gid": "orphan_org_1", "completed": False, "due_on": None, "start_on": None}
    ])

    assert "orphan_org_1" in result.orphaned_org
    assert result.summary["orphaned_org"] == 1


def test_validate_orphaned_db(
    engine: SyncEngine, database: Database
) -> None:
    """Validate detects DB snapshots with no corresponding org entry."""
    _insert_snapshot(database, gid="db_only_task", name="DB Only")
    _insert_snapshot(database, gid="shared_task", name="Shared")

    result = engine.validate([
        {"gid": "shared_task", "completed": False, "due_on": "2026-03-15", "start_on": "2026-03-10"}
    ])

    assert "db_only_task" in result.orphaned_db
    assert result.summary["orphaned_db"] >= 1


def test_validate_empty_org_states(
    engine: SyncEngine, database: Database
) -> None:
    """Validate with empty org states reports all DB tasks as orphaned."""
    _insert_snapshot(database, gid="task_x", name="Task X")
    _insert_snapshot(database, gid="task_y", name="Task Y")

    result = engine.validate([])

    assert result.summary["total"] == 0
    assert len(result.orphaned_db) == 2


# --- CLI JSON Contract Tests ---


def test_cli_reconcile_json_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CLI reconcile command produces valid JSON envelope."""
    from typer.testing import CliRunner

    from asana_org_bridge.commands import app
    from asana_org_bridge.config import reload_settings

    db_path = tmp_path / "cli_reconcile.db"
    monkeypatch.setenv("ASANA_ORG_DB_PATH", str(db_path))
    monkeypatch.setenv("ASANA_ORG_MOCK_DATA", "true")
    monkeypatch.delenv("ASANA_PAT", raising=False)
    reload_settings()

    db = Database(db_path=db_path, echo=False)
    MigrationManager(db).migrate()

    # Insert a snapshot that differs from mock
    _insert_snapshot(
        db,
        gid="task_001",
        name="Review project proposal",
        completed=True,  # differs from mock (False)
        due_on="2026-02-28",
        start_on="2026-02-25",
    )

    runner = CliRunner()
    result = runner.invoke(app, ["reconcile", "--json"])

    assert result.exit_code == 0, f"CLI error: {result.output}"
    response = json.loads(result.output)

    assert response["version"] == "1"
    assert response["command"] == "reconcile"
    assert response["status"] == "success"
    assert "drifted_tasks" in response["data"]
    assert "missing_remote" in response["data"]
    assert "summary" in response["data"]
    assert response["data"]["summary"]["total_checked"] >= 1


def test_cli_rebuild_cache_json_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CLI rebuild-cache command produces valid JSON envelope."""
    from typer.testing import CliRunner

    from asana_org_bridge.commands import app
    from asana_org_bridge.config import reload_settings

    db_path = tmp_path / "cli_rebuild.db"
    monkeypatch.setenv("ASANA_ORG_DB_PATH", str(db_path))
    monkeypatch.setenv("ASANA_ORG_MOCK_DATA", "true")
    monkeypatch.delenv("ASANA_PAT", raising=False)
    reload_settings()

    db = Database(db_path=db_path, echo=False)
    MigrationManager(db).migrate()

    runner = CliRunner()
    result = runner.invoke(app, ["rebuild-cache", "--json", "--no-confirm"])

    assert result.exit_code == 0, f"CLI error: {result.output}"
    response = json.loads(result.output)

    assert response["version"] == "1"
    assert response["command"] == "rebuild-cache"
    assert response["status"] == "success"
    assert "snapshots_deleted" in response["data"]
    assert "snapshots_created" in response["data"]
    assert response["data"]["snapshots_created"] == 5  # mock data has 5 tasks


def test_cli_validate_json_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CLI validate command produces valid JSON envelope from stdin."""
    from typer.testing import CliRunner

    from asana_org_bridge.commands import app
    from asana_org_bridge.config import reload_settings

    db_path = tmp_path / "cli_validate.db"
    monkeypatch.setenv("ASANA_ORG_DB_PATH", str(db_path))
    monkeypatch.setenv("ASANA_ORG_MOCK_DATA", "true")
    monkeypatch.delenv("ASANA_PAT", raising=False)
    reload_settings()

    db = Database(db_path=db_path, echo=False)
    MigrationManager(db).migrate()

    # Insert a snapshot to validate against
    _insert_snapshot(
        db,
        gid="task_v_cli",
        name="CLI Test Task",
        completed=False,
        due_on="2026-03-15",
        start_on="2026-03-10",
    )

    request_json = json.dumps({
        "version": "1",
        "command": "validate",
        "tasks": [
            {
                "gid": "task_v_cli",
                "completed": True,
                "due_on": "2026-03-15",
                "start_on": "2026-03-10",
            }
        ],
    })

    runner = CliRunner()
    result = runner.invoke(app, ["validate", "--json", "-"], input=request_json)

    assert result.exit_code == 0, f"CLI error: {result.output}"
    response = json.loads(result.output)

    assert response["version"] == "1"
    assert response["command"] == "validate"
    assert response["status"] == "success"
    assert "mismatches" in response["data"]
    assert "orphaned_org" in response["data"]
    assert "orphaned_db" in response["data"]
    assert "summary" in response["data"]

    # Should detect the completed mismatch
    mismatches = response["data"]["mismatches"]
    assert len(mismatches) >= 1
    assert any(m["field"] == "completed" for m in mismatches)


def test_cli_validate_invalid_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CLI validate returns error envelope for invalid JSON input."""
    from typer.testing import CliRunner

    from asana_org_bridge.commands import app

    monkeypatch.setenv("ASANA_ORG_MOCK_DATA", "true")
    monkeypatch.delenv("ASANA_PAT", raising=False)

    runner = CliRunner()
    result = runner.invoke(app, ["validate", "--json", "-"], input="not valid json{")

    assert result.exit_code == 1
    response = json.loads(result.output)
    assert response["status"] == "error"
    assert response["error"]["code"] == "INVALID_REQUEST"


def test_cli_validate_wrong_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CLI validate returns error for unsupported version."""
    from typer.testing import CliRunner

    from asana_org_bridge.commands import app

    monkeypatch.setenv("ASANA_ORG_MOCK_DATA", "true")
    monkeypatch.delenv("ASANA_PAT", raising=False)

    request_json = json.dumps(
        {"version": "99", "command": "validate", "tasks": []}
    )

    runner = CliRunner()
    result = runner.invoke(app, ["validate", "--json", "-"], input=request_json)

    assert result.exit_code == 1
    response = json.loads(result.output)
    assert response["status"] == "error"
