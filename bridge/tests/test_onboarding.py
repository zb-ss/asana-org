"""Tests for first-run onboarding detection and relink command."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from asana_org_bridge.auth import AuthManager
from asana_org_bridge.commands import app
from asana_org_bridge.db import Database, MigrationManager
from asana_org_bridge.models import TaskSnapshot
from asana_org_bridge.sync import SyncEngine

runner = CliRunner()


# --- Fixtures ---


@pytest.fixture
def temp_db_path(tmp_path: Path) -> Path:
    """Create temporary database path."""
    return tmp_path / "test_onboarding.db"


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


@pytest.fixture
def engine_with_task(engine: SyncEngine) -> SyncEngine:
    """Create engine with a sample task snapshot."""
    now = datetime.now(UTC)
    with engine.db.session() as session:
        session.add(
            TaskSnapshot(
                gid="task_001",
                permalink_url="https://app.asana.com/0/0/task_001",
                name="Test Task",
                completed=False,
                modified_at=now,
            )
        )
    return engine


# --- Doctor first-run detection tests ---


class TestDoctorOnboarding:
    """Tests for doctor command first-run and partial setup detection."""

    def test_doctor_first_run_no_db_no_pat(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Doctor detects first-run when no DB and no PAT are configured."""
        monkeypatch.delenv("ASANA_PAT", raising=False)
        monkeypatch.setenv("ASANA_ORG_DB_PATH", str(tmp_path / "nonexistent.db"))

        result = runner.invoke(app, ["doctor"])
        assert result.exit_code == 0
        assert "First-time Setup Guide" in result.output
        assert "export ASANA_PAT" in result.output
        assert "db-init" in result.output

    def test_doctor_partial_setup_db_exists_no_pat(
        self, monkeypatch: pytest.MonkeyPatch, database: Database
    ) -> None:
        """Doctor detects partial setup: DB exists but no PAT."""
        monkeypatch.delenv("ASANA_PAT", raising=False)
        monkeypatch.setenv("ASANA_ORG_DB_PATH", str(database.db_path))

        result = runner.invoke(app, ["doctor"])
        assert result.exit_code == 0
        assert "Missing PAT" in result.output
        assert "export ASANA_PAT" in result.output

    def test_doctor_partial_setup_pat_exists_no_db(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Doctor detects partial setup: PAT configured but no DB."""
        monkeypatch.setenv("ASANA_PAT", "test_pat_12345")
        monkeypatch.setenv("ASANA_ORG_DB_PATH", str(tmp_path / "nonexistent.db"))

        result = runner.invoke(app, ["doctor"])
        assert result.exit_code == 0
        assert "Database Not Initialized" in result.output
        assert "db-init" in result.output

    def test_doctor_fully_configured_no_setup_guide(
        self, monkeypatch: pytest.MonkeyPatch, database: Database
    ) -> None:
        """Doctor does not show setup guide when fully configured."""
        monkeypatch.setenv("ASANA_PAT", "test_pat_12345")
        monkeypatch.setenv("ASANA_ORG_DB_PATH", str(database.db_path))

        result = runner.invoke(app, ["doctor"])
        assert result.exit_code == 0
        assert "First-time Setup Guide" not in result.output
        assert "Missing PAT" not in result.output
        assert "Database Not Initialized" not in result.output


# --- Relink command tests ---


class TestRelinkEngine:
    """Tests for SyncEngine.relink_task()."""

    def test_relink_valid_task_and_permalink(self, engine_with_task: SyncEngine) -> None:
        """Relink succeeds with valid task GID and permalink."""
        result = engine_with_task.relink_task(
            task_gid="task_001",
            new_permalink="https://app.asana.com/0/project_new/task_001",
        )

        assert "error" not in result
        assert result["task_gid"] == "task_001"
        assert result["old_permalink"] == "https://app.asana.com/0/0/task_001"
        assert result["new_permalink"] == "https://app.asana.com/0/project_new/task_001"
        assert result["task_name"] == "Test Task"

    def test_relink_updates_snapshot_in_db(self, engine_with_task: SyncEngine) -> None:
        """Relink actually updates the permalink in the database."""
        new_url = "https://app.asana.com/0/project_new/task_001"
        engine_with_task.relink_task(task_gid="task_001", new_permalink=new_url)

        # Verify in DB
        with engine_with_task.db.session() as session:
            snapshot = (
                session.query(TaskSnapshot)
                .filter(TaskSnapshot.gid == "task_001")
                .first()
            )
            assert snapshot is not None
            assert snapshot.permalink_url == new_url

    def test_relink_unknown_task_gid(self, engine: SyncEngine) -> None:
        """Relink returns error for unknown task GID."""
        result = engine.relink_task(
            task_gid="nonexistent_task",
            new_permalink="https://app.asana.com/0/0/nonexistent",
        )

        assert "error" in result
        assert result["code"] == "NOT_FOUND"
        assert "nonexistent_task" in result["error"]

    def test_relink_invalid_permalink_format(self, engine_with_task: SyncEngine) -> None:
        """Relink returns error for invalid permalink format."""
        result = engine_with_task.relink_task(
            task_gid="task_001",
            new_permalink="https://example.com/not-asana",
        )

        assert "error" in result
        assert result["code"] == "INVALID_REQUEST"
        assert "https://app.asana.com/" in result["error"]

    def test_relink_empty_permalink(self, engine_with_task: SyncEngine) -> None:
        """Relink returns error for empty permalink."""
        result = engine_with_task.relink_task(
            task_gid="task_001",
            new_permalink="",
        )

        assert "error" in result
        assert result["code"] == "INVALID_REQUEST"

    def test_relink_picks_latest_snapshot(self, engine: SyncEngine) -> None:
        """Relink updates only the latest snapshot when multiple exist."""
        now = datetime.now(UTC)
        earlier = datetime(2026, 1, 1, tzinfo=UTC)

        with engine.db.session() as session:
            session.add(
                TaskSnapshot(
                    gid="task_multi",
                    permalink_url="https://app.asana.com/0/0/old",
                    name="Old Snapshot",
                    completed=False,
                    modified_at=earlier,
                    snapshot_at=earlier,
                )
            )
            session.add(
                TaskSnapshot(
                    gid="task_multi",
                    permalink_url="https://app.asana.com/0/0/current",
                    name="Current Snapshot",
                    completed=False,
                    modified_at=now,
                    snapshot_at=now,
                )
            )

        result = engine.relink_task(
            task_gid="task_multi",
            new_permalink="https://app.asana.com/0/project_new/task_multi",
        )

        assert "error" not in result
        assert result["old_permalink"] == "https://app.asana.com/0/0/current"
        assert result["task_name"] == "Current Snapshot"


class TestRelinkCommand:
    """Tests for the relink CLI command."""

    def test_relink_json_output_success(
        self, monkeypatch: pytest.MonkeyPatch, database: Database
    ) -> None:
        """Relink command outputs correct JSON envelope on success."""
        now = datetime.now(UTC)
        with database.session() as session:
            session.add(
                TaskSnapshot(
                    gid="task_cmd",
                    permalink_url="https://app.asana.com/0/0/task_cmd",
                    name="Command Test",
                    completed=False,
                    modified_at=now,
                )
            )

        monkeypatch.setenv("ASANA_ORG_DB_PATH", str(database.db_path))
        monkeypatch.setenv("ASANA_ORG_MOCK_DATA", "true")

        result = runner.invoke(
            app,
            [
                "relink",
                "task_cmd",
                "--permalink",
                "https://app.asana.com/0/new/task_cmd",
                "--json",
            ],
        )

        assert result.exit_code == 0
        import json

        response = json.loads(result.output)
        assert response["version"] == "1"
        assert response["command"] == "relink"
        assert response["status"] == "success"
        assert response["data"]["task_gid"] == "task_cmd"
        assert response["data"]["old_permalink"] == "https://app.asana.com/0/0/task_cmd"
        assert response["data"]["new_permalink"] == "https://app.asana.com/0/new/task_cmd"

    def test_relink_json_output_not_found(
        self, monkeypatch: pytest.MonkeyPatch, database: Database
    ) -> None:
        """Relink command outputs error envelope for unknown task."""
        monkeypatch.setenv("ASANA_ORG_DB_PATH", str(database.db_path))
        monkeypatch.setenv("ASANA_ORG_MOCK_DATA", "true")

        result = runner.invoke(
            app,
            [
                "relink",
                "nonexistent",
                "--permalink",
                "https://app.asana.com/0/0/nonexistent",
                "--json",
            ],
        )

        assert result.exit_code == 1
        import json

        response = json.loads(result.output)
        assert response["status"] == "error"
        assert response["error"]["code"] == "NOT_FOUND"
