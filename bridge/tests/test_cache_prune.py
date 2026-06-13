"""Tests for cache pruning (retention policy)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from asana_org_bridge.db import Database, MigrationManager
from asana_org_bridge.models import PendingMutation, SyncRun, TaskSnapshot
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
    from asana_org_bridge.auth import AuthManager

    auth = AuthManager.__new__(AuthManager)
    auth._pat = None
    return SyncEngine(db=temp_db, auth_manager=auth, use_mock=True)


def _make_snapshot(
    session,
    gid: str,
    name: str = "Task",
    snapshot_at: datetime | None = None,
) -> TaskSnapshot:
    """Helper to insert a TaskSnapshot with a specific snapshot_at."""
    snap = TaskSnapshot(
        gid=gid,
        permalink_url=f"https://app.asana.com/0/0/{gid}",
        name=name,
        completed=False,
        modified_at=snapshot_at or datetime.now(UTC),
        snapshot_at=snapshot_at or datetime.now(UTC),
    )
    session.add(snap)
    session.flush()
    return snap


def _make_sync_run(
    session,
    started_at: datetime | None = None,
) -> SyncRun:
    """Helper to insert a SyncRun with a specific started_at."""
    run = SyncRun(
        run_type="pull",
        status="completed",
        started_at=started_at or datetime.now(UTC),
    )
    session.add(run)
    session.flush()
    return run


def _make_mutation(
    session,
    task_gid: str,
    status: str = "completed",
    created_at: datetime | None = None,
) -> PendingMutation:
    """Helper to insert a PendingMutation with a specific created_at."""
    mut = PendingMutation(
        task_gid=task_gid,
        operation="update_status",
        payload={"completed": True},
        status=status,
        created_at=created_at or datetime.now(UTC),
    )
    session.add(mut)
    session.flush()
    return mut


class TestPruneCacheSnapshots:
    """Tests for snapshot pruning logic."""

    def test_old_snapshots_deleted(self, engine, temp_db):
        """Old snapshots beyond retention are deleted, recent ones kept."""
        now = datetime.now(UTC)
        old = now - timedelta(days=60)
        recent = now - timedelta(days=5)

        with temp_db.session() as session:
            _make_snapshot(session, "task_a", snapshot_at=old)
            _make_snapshot(session, "task_b", snapshot_at=recent)
            # task_a also has a recent snapshot (so the old one is not the latest)
            _make_snapshot(session, "task_a", snapshot_at=recent)

        result = engine.prune_cache(dry_run=False)

        assert result.snapshots_deleted == 1
        # Verify only 2 snapshots remain
        with temp_db.session() as session:
            remaining = session.query(TaskSnapshot).count()
            assert remaining == 2

    def test_most_recent_snapshot_always_kept(self, engine, temp_db):
        """The most recent snapshot per task is never deleted, even if old."""
        old = datetime.now(UTC) - timedelta(days=60)

        with temp_db.session() as session:
            # Only one snapshot for task_x, and it's old
            _make_snapshot(session, "task_x", snapshot_at=old)

        result = engine.prune_cache(dry_run=False)

        # Should NOT delete it because it's the only (most recent) snapshot
        assert result.snapshots_deleted == 0
        with temp_db.session() as session:
            assert session.query(TaskSnapshot).count() == 1

    def test_snapshots_referenced_by_pending_mutations_not_deleted(
        self, engine, temp_db
    ):
        """Snapshots for tasks with pending mutations are never deleted."""
        old = datetime.now(UTC) - timedelta(days=60)
        very_old = datetime.now(UTC) - timedelta(days=120)

        with temp_db.session() as session:
            # Two snapshots for task_p, both old. One is the latest.
            _make_snapshot(session, "task_p", snapshot_at=very_old)
            _make_snapshot(session, "task_p", snapshot_at=old)
            # A pending mutation references task_p
            _make_mutation(session, "task_p", status="pending", created_at=old)

            # An unrelated old snapshot for task_q (no pending mutation)
            _make_snapshot(session, "task_q", snapshot_at=very_old)
            _make_snapshot(session, "task_q", snapshot_at=old - timedelta(days=1))

        result = engine.prune_cache(dry_run=False)

        # task_p snapshots are all protected (pending mutation)
        # task_q: latest is protected, but the very_old one should be deleted
        assert result.snapshots_deleted == 1
        with temp_db.session() as session:
            remaining = session.query(TaskSnapshot).all()
            remaining_gids = [s.gid for s in remaining]
            assert remaining_gids.count("task_p") == 2
            assert remaining_gids.count("task_q") == 1

    def test_snapshots_referenced_by_failed_mutations_not_deleted(
        self, engine, temp_db
    ):
        """Snapshots for tasks with failed mutations are never deleted."""
        old = datetime.now(UTC) - timedelta(days=60)

        with temp_db.session() as session:
            _make_snapshot(session, "task_f", snapshot_at=old)
            _make_snapshot(session, "task_f", name="newer", snapshot_at=old + timedelta(days=1))
            _make_mutation(session, "task_f", status="failed", created_at=old)

        result = engine.prune_cache(dry_run=False)

        assert result.snapshots_deleted == 0
        with temp_db.session() as session:
            assert session.query(TaskSnapshot).count() == 2


class TestPruneCacheSyncRuns:
    """Tests for sync run pruning logic."""

    def test_old_sync_runs_deleted(self, engine, temp_db):
        """Sync runs older than journal_retention_days are deleted."""
        now = datetime.now(UTC)
        old = now - timedelta(days=120)
        recent = now - timedelta(days=10)

        with temp_db.session() as session:
            _make_sync_run(session, started_at=old)
            _make_sync_run(session, started_at=recent)

        result = engine.prune_cache(dry_run=False)

        assert result.sync_runs_deleted == 1
        with temp_db.session() as session:
            assert session.query(SyncRun).count() == 1


class TestPruneCacheMutations:
    """Tests for mutation pruning logic."""

    def test_old_completed_mutations_deleted(self, engine, temp_db):
        """Old completed mutations are deleted."""
        old = datetime.now(UTC) - timedelta(days=200)
        recent = datetime.now(UTC) - timedelta(days=10)

        with temp_db.session() as session:
            _make_mutation(session, "task_a", status="completed", created_at=old)
            _make_mutation(session, "task_b", status="completed", created_at=recent)

        result = engine.prune_cache(dry_run=False)

        assert result.mutations_deleted == 1
        with temp_db.session() as session:
            assert session.query(PendingMutation).count() == 1

    def test_pending_mutations_never_deleted(self, engine, temp_db):
        """Pending mutations are never deleted regardless of age."""
        very_old = datetime.now(UTC) - timedelta(days=365)

        with temp_db.session() as session:
            _make_mutation(session, "task_old", status="pending", created_at=very_old)

        result = engine.prune_cache(dry_run=False)

        assert result.mutations_deleted == 0
        with temp_db.session() as session:
            assert session.query(PendingMutation).count() == 1

    def test_failed_mutations_never_deleted(self, engine, temp_db):
        """Failed mutations are never deleted regardless of age."""
        very_old = datetime.now(UTC) - timedelta(days=365)

        with temp_db.session() as session:
            _make_mutation(session, "task_fail", status="failed", created_at=very_old)

        result = engine.prune_cache(dry_run=False)

        assert result.mutations_deleted == 0
        with temp_db.session() as session:
            assert session.query(PendingMutation).count() == 1


class TestPruneCacheDryRun:
    """Tests for dry_run mode."""

    def test_dry_run_counts_but_does_not_delete(self, engine, temp_db):
        """Dry run reports counts without actually deleting anything."""
        old = datetime.now(UTC) - timedelta(days=200)

        with temp_db.session() as session:
            # Create old data that would be pruned
            _make_snapshot(session, "task_a", snapshot_at=old)
            _make_snapshot(session, "task_a", snapshot_at=old + timedelta(days=1))
            _make_sync_run(session, started_at=old)
            _make_mutation(session, "task_b", status="completed", created_at=old)

        result = engine.prune_cache(dry_run=True)

        assert result.dry_run is True
        # The old, non-latest snapshot should be counted
        assert result.snapshots_deleted == 1
        assert result.sync_runs_deleted == 1
        assert result.mutations_deleted == 1

        # Nothing actually deleted
        with temp_db.session() as session:
            assert session.query(TaskSnapshot).count() == 2
            assert session.query(SyncRun).count() == 1
            assert session.query(PendingMutation).count() == 1


class TestPruneCacheEmptyDB:
    """Tests for edge cases."""

    def test_empty_database_no_errors(self, engine, temp_db):
        """Pruning an empty database produces zeros without errors."""
        result = engine.prune_cache(dry_run=False)

        assert result.snapshots_deleted == 0
        assert result.sync_runs_deleted == 0
        assert result.mutations_deleted == 0
        assert result.dry_run is False

    def test_empty_database_dry_run_no_errors(self, engine, temp_db):
        """Dry-run pruning an empty database produces zeros without errors."""
        result = engine.prune_cache(dry_run=True)

        assert result.snapshots_deleted == 0
        assert result.sync_runs_deleted == 0
        assert result.mutations_deleted == 0
        assert result.dry_run is True
