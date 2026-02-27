"""Tests for schema creation."""

from __future__ import annotations

from datetime import UTC
from pathlib import Path

import pytest

from asana_org_bridge.db import Database, MigrationManager
from asana_org_bridge.models import (
    OrgMirrorState,
    PendingMutation,
    SchemaMeta,
    SyncRun,
    TaskSnapshot,
)


@pytest.fixture
def temp_db_path(tmp_path: Path) -> Path:
    """Create temporary database path."""
    return tmp_path / "test_bridge.db"


@pytest.fixture
def database(temp_db_path: Path) -> Database:
    """Create database instance."""
    return Database(db_path=temp_db_path, echo=False)


def test_database_initialization(database: Database) -> None:
    """Test that database can be created."""
    assert database.db_path.parent.exists()


def test_schema_migration_001(database: Database) -> None:
    """Test applying schema migration 001."""
    migrator = MigrationManager(database)

    # Should need migration
    assert migrator.needs_init() is True

    # Apply migration
    migrator.apply_migration("001")

    # Should no longer need init
    assert migrator.needs_init() is False

    # Check schema version
    assert database.get_schema_version() == "001"

    # Check tables exist by querying
    with database.session() as session:
        # Schema meta should have entry
        meta = session.query(SchemaMeta).first()
        assert meta is not None
        assert meta.version == "001"


def test_full_migration(database: Database) -> None:
    """Test applying all pending migrations."""
    migrator = MigrationManager(database)

    pending = migrator.get_pending_migrations()
    assert "001" in pending

    applied = migrator.migrate()

    assert "001" in applied
    assert migrator.get_pending_migrations() == []


def test_table_creation(database: Database) -> None:
    """Test all tables can be created and queried."""
    # Run migrations
    migrator = MigrationManager(database)
    migrator.migrate()

    with database.session() as session:
        # Test SchemaMeta
        meta = SchemaMeta(version="001", description="Test")
        session.add(meta)
        session.flush()
        assert meta.id is not None

        # Test TaskSnapshot
        from datetime import datetime

        task = TaskSnapshot(
            gid="test_001",
            permalink_url="https://app.asana.com/0/0/test_001",
            name="Test Task",
            completed=False,
            modified_at=datetime.now(UTC),
        )
        session.add(task)
        session.flush()
        assert task.id is not None

        # Test OrgMirrorState
        mirror = OrgMirrorState(
            project_gid="proj_001",
            project_name="Test Project",
            file_path="/tmp/test.org",
        )
        session.add(mirror)
        session.flush()
        assert mirror.id is not None

        # Test PendingMutation
        mutation = PendingMutation(
            task_gid="test_001",
            operation="update_status",
            payload={"completed": True},
            status="pending",
        )
        session.add(mutation)
        session.flush()
        assert mutation.id is not None

        # Test SyncRun
        run = SyncRun(
            run_type="pull",
            status="started",
        )
        session.add(run)
        session.flush()
        assert run.id is not None


def test_foreign_keys_disabled(database: Database) -> None:
    """Test database works without foreign keys (SQLite default)."""
    from datetime import datetime

    migrator = MigrationManager(database)
    migrator.migrate()

    # Should work without FK enforcement
    with database.session() as session:
        task = TaskSnapshot(
            gid="test_001",
            permalink_url="https://app.asana.com/0/0/test_001",
            name="Test",
            modified_at=datetime.now(UTC),
        )
        session.add(task)

        # Add mutation with non-existent task (would fail with FK)
        mutation = PendingMutation(
            task_gid="test_001",  # This exists
            operation="update",
            payload={},
            status="pending",
        )
        session.add(mutation)


def test_idempotency_key_unique(database: Database) -> None:
    """Test that idempotency keys are unique."""
    migrator = MigrationManager(database)
    migrator.migrate()

    with database.session() as session:
        mutation1 = PendingMutation(
            task_gid="test_001",
            operation="update",
            payload={},
            status="pending",
        )
        session.add(mutation1)
        session.flush()
        key1 = mutation1.idempotency_key

    with database.session() as session:
        mutation2 = PendingMutation(
            task_gid="test_001",
            operation="update",
            payload={},
            status="pending",
        )
        session.add(mutation2)
        session.flush()
        key2 = mutation2.idempotency_key

    # Keys should be different
    assert key1 != key2
