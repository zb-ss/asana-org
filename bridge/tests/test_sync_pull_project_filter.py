"""Tests for sync-pull project filtering and auto-prune on 10th pull."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from asana_org_bridge.auth import AuthManager
from asana_org_bridge.db import Database, MigrationManager
from asana_org_bridge.models import SyncRun
from asana_org_bridge.sync import SyncEngine


@pytest.fixture
def temp_db(tmp_path):
    """Create a temporary database with schema applied."""
    db = Database(db_path=tmp_path / "test.db")
    migrator = MigrationManager(db)
    migrator.migrate()
    return db


@pytest.fixture
def engine(temp_db):
    """Create a SyncEngine backed by a temp database in mock mode."""
    auth = AuthManager.__new__(AuthManager)
    auth._pat = None
    return SyncEngine(db=temp_db, auth_manager=auth, use_mock=True)


class TestProjectFilter:
    """Tests for project_gid filtering in pull()."""

    def test_pull_with_project_gid_filters_matching_tasks(self, engine):
        """Pull with project_gid returns only tasks belonging to that project."""
        result = engine.pull(project_gid="proj_001")

        # Only task_001 belongs to proj_001
        assert result.tasks_pulled == 1
        assert len(result.tasks) == 1
        assert result.tasks[0]["gid"] == "task_001"

    def test_pull_without_project_gid_returns_all_tasks(self, engine):
        """Pull without project_gid returns all tasks."""
        result = engine.pull()

        # Mock data has 5 tasks
        assert result.tasks_pulled == 5
        assert len(result.tasks) == 5

    def test_pull_with_nonexistent_project_gid_returns_empty(self, engine):
        """Pull with a non-existent project_gid returns empty results."""
        result = engine.pull(project_gid="proj_nonexistent")

        assert result.tasks_pulled == 0
        assert len(result.tasks) == 0

    def test_pull_filters_to_correct_project(self, engine):
        """Pull with project_gid for proj_002 returns only its tasks."""
        result = engine.pull(project_gid="proj_002")

        # task_002 and task_004 belong to proj_002
        gids = {t["gid"] for t in result.tasks}
        assert gids == {"task_002", "task_004"}
        assert result.tasks_pulled == 2

    def test_pull_filters_to_project_003(self, engine):
        """Pull with project_gid for proj_003 returns only its tasks."""
        result = engine.pull(project_gid="proj_003")

        # task_003 and task_005 belong to proj_003
        gids = {t["gid"] for t in result.tasks}
        assert gids == {"task_003", "task_005"}
        assert result.tasks_pulled == 2

    def test_pull_with_project_gid_and_incomplete_only(self, engine):
        """Project filter works together with incomplete_only."""
        # proj_003 has task_003 (completed) and task_005 (incomplete).
        # Mock mode ignores incomplete_only, so both are returned.
        result = engine.pull(project_gid="proj_003", incomplete_only=True)

        gids = {t["gid"] for t in result.tasks}
        assert gids == {"task_003", "task_005"}
        # All returned tasks must belong to proj_003
        for task in result.tasks:
            project_gids = [
                m.get("project", {}).get("gid")
                for m in task.get("memberships", [])
            ]
            assert "proj_003" in project_gids


class TestAutoPruneOnPull:
    """Tests for auto-prune triggering on every 10th completed pull."""

    def _insert_completed_pull_runs(self, db, count):
        """Insert N completed pull SyncRun records."""
        with db.session() as session:
            for _ in range(count):
                run = SyncRun(
                    run_type="pull",
                    status="completed",
                    started_at=datetime.now(UTC),
                    completed_at=datetime.now(UTC),
                )
                session.add(run)

    def test_auto_prune_triggers_on_10th_pull(self, engine, temp_db):
        """Auto-prune is called when the completed pull count reaches 10."""
        # Insert 9 completed pull runs so the next pull will be the 10th
        self._insert_completed_pull_runs(temp_db, 9)

        with patch.object(engine, "prune_cache") as mock_prune:
            engine.pull()
            mock_prune.assert_called_once_with(dry_run=False)

    def test_auto_prune_does_not_trigger_on_9th_pull(self, engine, temp_db):
        """Auto-prune is NOT called when count is not a multiple of 10."""
        # Insert 8 completed pull runs so the next pull will be the 9th
        self._insert_completed_pull_runs(temp_db, 8)

        with patch.object(engine, "prune_cache") as mock_prune:
            engine.pull()
            mock_prune.assert_not_called()

    def test_auto_prune_triggers_on_20th_pull(self, engine, temp_db):
        """Auto-prune triggers again on the 20th pull."""
        self._insert_completed_pull_runs(temp_db, 19)

        with patch.object(engine, "prune_cache") as mock_prune:
            engine.pull()
            mock_prune.assert_called_once_with(dry_run=False)

    def test_auto_prune_does_not_trigger_on_11th_pull(self, engine, temp_db):
        """Auto-prune does NOT trigger on 11th pull (not a multiple of 10)."""
        self._insert_completed_pull_runs(temp_db, 10)

        with patch.object(engine, "prune_cache") as mock_prune:
            engine.pull()
            mock_prune.assert_not_called()

    def test_auto_prune_failure_does_not_fail_pull(self, engine, temp_db):
        """If auto-prune raises an exception, the pull still succeeds."""
        self._insert_completed_pull_runs(temp_db, 9)

        with patch.object(
            engine, "prune_cache", side_effect=RuntimeError("prune exploded")
        ):
            # Should not raise
            result = engine.pull()
            assert result.tasks_pulled > 0
            assert len(result.errors) == 0

    def test_auto_prune_does_not_trigger_on_first_pull(self, engine, temp_db):
        """Auto-prune does NOT trigger on the very first pull."""
        with patch.object(engine, "prune_cache") as mock_prune:
            engine.pull()
            mock_prune.assert_not_called()
