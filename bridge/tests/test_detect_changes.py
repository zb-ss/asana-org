"""Tests for the detect-changes command and SyncEngine.detect_changes."""

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
    return tmp_path / "detect_changes.db"


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


@pytest.fixture
def snapshot_now() -> datetime:
    return datetime.now(UTC)


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


# --- Unit tests for SyncEngine.detect_changes ---


def test_completed_change_generates_status_change(
    engine: SyncEngine, database: Database
) -> None:
    """Task with changed completed status generates a status_change."""
    _insert_snapshot(database, gid="task_001", completed=False)

    result = engine.detect_changes(
        [{"gid": "task_001", "completed": True, "due_on": "2026-03-15", "start_on": "2026-03-10"}]
    )

    assert len(result.pending_changes) == 1
    change = result.pending_changes[0]
    assert change["type"] == "status_change"
    assert change["proposed_state"]["completed"] is True
    assert change["current_state"]["completed"] is False
    assert result.summary["status_changes"] == 1


def test_due_on_change_generates_date_change(
    engine: SyncEngine, database: Database
) -> None:
    """Task with changed due_on generates a date_change."""
    _insert_snapshot(database, gid="task_002", due_on="2026-03-15")

    result = engine.detect_changes(
        [{"gid": "task_002", "completed": False, "due_on": "2026-04-01", "start_on": "2026-03-10"}]
    )

    assert len(result.pending_changes) == 1
    change = result.pending_changes[0]
    assert change["type"] == "date_change"
    assert change["proposed_state"]["due_on"] == "2026-04-01"
    assert change["current_state"]["due_on"] == "2026-03-15"
    assert result.summary["date_changes"] == 1


def test_start_on_change_generates_date_change(
    engine: SyncEngine, database: Database
) -> None:
    """Task with changed start_on generates a date_change."""
    _insert_snapshot(database, gid="task_003", start_on="2026-03-10")

    result = engine.detect_changes(
        [{"gid": "task_003", "completed": False, "due_on": "2026-03-15", "start_on": "2026-03-20"}]
    )

    assert len(result.pending_changes) == 1
    change = result.pending_changes[0]
    assert change["type"] == "date_change"
    assert change["proposed_state"]["start_on"] == "2026-03-20"
    assert change["current_state"]["start_on"] == "2026-03-10"
    assert result.summary["date_changes"] == 1


def test_no_changes_generates_nothing(
    engine: SyncEngine, database: Database
) -> None:
    """Task with no changes generates zero mutations."""
    _insert_snapshot(
        database, gid="task_004", completed=False, due_on="2026-03-15", start_on="2026-03-10"
    )

    result = engine.detect_changes(
        [{"gid": "task_004", "completed": False, "due_on": "2026-03-15", "start_on": "2026-03-10"}]
    )

    assert len(result.pending_changes) == 0
    assert result.summary["total"] == 0
    assert result.summary["status_changes"] == 0
    assert result.summary["date_changes"] == 0


def test_task_not_in_db_skipped_with_warning(engine: SyncEngine) -> None:
    """Task not in database is skipped with a warning."""
    result = engine.detect_changes(
        [{"gid": "nonexistent_task", "completed": True, "due_on": "2026-03-15", "start_on": None}]
    )

    assert len(result.pending_changes) == 0
    assert any("nonexistent_task" in w for w in result.warnings)


def test_multiple_tasks_mixed_changes(
    engine: SyncEngine, database: Database
) -> None:
    """Multiple tasks with mixed changes generate correct mutations."""
    _insert_snapshot(database, gid="task_a", completed=False, due_on="2026-03-15", start_on=None)
    _insert_snapshot(database, gid="task_b", completed=False, due_on="2026-04-01", start_on="2026-03-25")
    _insert_snapshot(database, gid="task_c", completed=True, due_on="2026-02-28", start_on="2026-02-20")

    result = engine.detect_changes(
        [
            # task_a: completed changed
            {"gid": "task_a", "completed": True, "due_on": "2026-03-15", "start_on": None},
            # task_b: no changes
            {"gid": "task_b", "completed": False, "due_on": "2026-04-01", "start_on": "2026-03-25"},
            # task_c: due_on and start_on changed
            {"gid": "task_c", "completed": True, "due_on": "2026-03-31", "start_on": "2026-03-01"},
            # task_d: not in DB
            {"gid": "task_d", "completed": False, "due_on": None, "start_on": None},
        ]
    )

    assert result.summary["total"] == 3  # 1 status + 2 date
    assert result.summary["status_changes"] == 1
    assert result.summary["date_changes"] == 2
    assert len(result.warnings) == 1  # task_d not found


def test_change_entry_has_required_fields(
    engine: SyncEngine, database: Database
) -> None:
    """Each change entry contains all required fields."""
    _insert_snapshot(database, gid="task_fields", completed=False)

    result = engine.detect_changes(
        [{"gid": "task_fields", "completed": True, "due_on": "2026-03-15", "start_on": "2026-03-10"}]
    )

    change = result.pending_changes[0]
    assert "id" in change
    assert change["id"].startswith("pc_")
    assert "type" in change
    assert "description" in change
    assert "current_state" in change
    assert "proposed_state" in change
    assert "baseline_modified_at" in change


def test_null_dates_handled_correctly(
    engine: SyncEngine, database: Database
) -> None:
    """Null dates in org match null dates in snapshot (no change)."""
    _insert_snapshot(database, gid="task_nulls", due_on=None, start_on=None)

    result = engine.detect_changes(
        [{"gid": "task_nulls", "completed": False, "due_on": None, "start_on": None}]
    )

    assert len(result.pending_changes) == 0


def test_empty_string_dates_treated_as_null(
    engine: SyncEngine, database: Database
) -> None:
    """Empty string dates from org are treated as None (no false diff)."""
    _insert_snapshot(database, gid="task_empty_dates", due_on=None, start_on=None)

    result = engine.detect_changes(
        [{"gid": "task_empty_dates", "completed": False, "due_on": "", "start_on": ""}]
    )

    assert len(result.pending_changes) == 0


# --- CLI contract test ---


def test_cli_detect_changes_json_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CLI command produces valid JSON envelope from stdin."""
    from typer.testing import CliRunner

    from asana_org_bridge.commands import app
    from asana_org_bridge.config import reload_settings

    # Set up temp DB and mock mode
    db_path = tmp_path / "cli_test.db"
    monkeypatch.setenv("ASANA_ORG_DB_PATH", str(db_path))
    monkeypatch.setenv("ASANA_ORG_MOCK_DATA", "true")
    monkeypatch.delenv("ASANA_PAT", raising=False)

    # Force settings reload so the new DB path takes effect
    reload_settings()

    # Initialize DB
    db = Database(db_path=db_path, echo=False)
    MigrationManager(db).migrate()

    # Use a mock task GID that exists in MockDataGenerator.MOCK_TASKS
    # task_001 has completed=False, due_on="2026-02-28", start_on="2026-02-25"
    request_json = json.dumps(
        {
            "version": "1",
            "command": "detect-changes",
            "tasks": [
                {
                    "gid": "task_001",
                    "completed": True,
                    "due_on": "2026-04-01",
                    "start_on": "2026-02-25",
                    "local_hash": "abc123",
                }
            ],
        }
    )

    runner = CliRunner()
    result = runner.invoke(app, ["detect-changes", "--json", "-"], input=request_json)

    assert result.exit_code == 0, f"CLI error: {result.output}"
    response = json.loads(result.output)

    assert response["version"] == "1"
    assert response["command"] == "detect-changes"
    assert response["status"] == "success"
    assert "pending_changes" in response["data"]
    assert "summary" in response["data"]

    # Should detect the completed change and the due_on change
    changes = response["data"]["pending_changes"]
    assert len(changes) >= 2

    types = {c["type"] for c in changes}
    assert "status_change" in types
    assert "date_change" in types


def test_cli_detect_changes_invalid_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CLI returns error envelope for invalid JSON input."""
    from typer.testing import CliRunner

    from asana_org_bridge.commands import app

    monkeypatch.setenv("ASANA_ORG_MOCK_DATA", "true")
    monkeypatch.delenv("ASANA_PAT", raising=False)

    runner = CliRunner()
    result = runner.invoke(app, ["detect-changes", "--json", "-"], input="not valid json{")

    assert result.exit_code == 1
    response = json.loads(result.output)
    assert response["status"] == "error"
    assert response["error"]["code"] == "INVALID_REQUEST"


def test_cli_detect_changes_wrong_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CLI returns error for unsupported version."""
    from typer.testing import CliRunner

    from asana_org_bridge.commands import app

    monkeypatch.setenv("ASANA_ORG_MOCK_DATA", "true")
    monkeypatch.delenv("ASANA_PAT", raising=False)

    request_json = json.dumps(
        {"version": "99", "command": "detect-changes", "tasks": []}
    )

    runner = CliRunner()
    result = runner.invoke(app, ["detect-changes", "--json", "-"], input=request_json)

    assert result.exit_code == 1
    response = json.loads(result.output)
    assert response["status"] == "error"
