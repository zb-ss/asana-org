"""Database models for Asana Org Bridge."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    TypeDecorator,
)
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql.type_api import TypeEngine


class UuidColumn(TypeDecorator[UUID]):
    """Platform-independent UUID type that uses CHAR(36) for storage.

    This works with SQLite which doesn't have a native UUID type.
    """

    impl = String(36)
    cache_ok = True

    def process_bind_param(
        self, value: UUID | str | None, dialect: Dialect
    ) -> str | None:
        if value is None:
            return value
        return str(value)

    def process_result_value(self, value: str | None, dialect: Dialect) -> UUID | None:
        if value is None:
            return value
        return UUID(value)

    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine[str]:
        return dialect.type_descriptor(String(36))


class Base(DeclarativeBase):
    """Base class for all database models."""

    pass


class SchemaMeta(Base):
    """Tracks schema version and migrations."""

    __tablename__ = "schema_meta"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version: Mapped[str] = mapped_column(String(16), nullable=False)
    applied_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)


class TaskSnapshot(Base):
    """Snapshot of an Asana task at a point in time."""

    __tablename__ = "tasks_snapshot"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    gid: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    permalink_url: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    completed: Mapped[bool] = mapped_column(Boolean, default=False)
    start_on: Mapped[str | None] = mapped_column(String(16), nullable=True)
    due_on: Mapped[str | None] = mapped_column(String(16), nullable=True)
    due_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    project_gid: Mapped[str | None] = mapped_column(String(64), nullable=True)
    project_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    section_gid: Mapped[str | None] = mapped_column(String(64), nullable=True)
    section_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    memberships_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    stories_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    custom_fields_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    modified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    local_hash: Mapped[str] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )
    snapshot_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )

    __table_args__ = (Index("ix_tasks_snapshot_gid_snapshot", "gid", "snapshot_at"),)


class OrgMirrorState(Base):
    """Current state of Org file mirrors."""

    __tablename__ = "org_mirror_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_gid: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    project_name: Mapped[str] = mapped_column(Text, nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    file_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    baseline_snapshot_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class PendingMutation(Base):
    """Pending mutations to be applied to Asana."""

    __tablename__ = "pending_mutations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_gid: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    operation: Mapped[str] = mapped_column(String(32), nullable=False)
    # operation types: update_status, update_dates, update_project, update_section, append_comment
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(
        String(128),
        unique=True,
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(16),
        default="pending",
        nullable=False,
    )
    # status: pending, applying, completed, failed
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )
    applied_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    def __init__(self, **kwargs: Any) -> None:
        """Initialize with auto-generated idempotency_key if not provided."""
        if "idempotency_key" not in kwargs:
            # Generate unique idempotency key based on task_gid, operation, payload, and timestamp
            task_gid = kwargs.get("task_gid", "")
            operation = kwargs.get("operation", "")
            payload = kwargs.get("payload", {})
            timestamp = datetime.now(UTC).isoformat()
            content = f"{task_gid}:{operation}:{json.dumps(payload, sort_keys=True)}:{timestamp}"
            kwargs["idempotency_key"] = hashlib.sha256(content.encode()).hexdigest()[
                :64
            ]
        super().__init__(**kwargs)


class SyncRun(Base):
    """Journal of sync operations."""

    __tablename__ = "sync_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_type: Mapped[str] = mapped_column(String(16), nullable=False)
    # run_type: pull, preview, apply, move, comment
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    # status: started, completed, failed
    tasks_pulled: Mapped[int] = mapped_column(Integer, default=0)
    tasks_updated: Mapped[int] = mapped_column(Integer, default=0)
    mutations_generated: Mapped[int] = mapped_column(Integer, default=0)
    mutations_applied: Mapped[int] = mapped_column(Integer, default=0)
    conflicts_detected: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class RequestIdempotency(Base):
    """Tracks request-level idempotency keys to prevent duplicate requests."""

    __tablename__ = "request_idempotency"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(
        String(128), nullable=False, unique=True, index=True
    )
    request_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    # status: completed, failed
    response_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


# Type aliases for cleaner code
def get_current_time() -> datetime:
    """Get current UTC time."""
    return datetime.now(UTC)
