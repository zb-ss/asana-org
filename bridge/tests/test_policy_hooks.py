"""Tests for policy hook interfaces and partial failure resume."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import pytest

from asana_org_bridge.db import Database, MigrationManager
from asana_org_bridge.models import PendingMutation
from asana_org_bridge.sync import SyncEngine


class AuthManagerProto(Protocol):
    def get_pat(self) -> str | None: ...


@pytest.fixture
def temp_db_path(tmp_path: Path) -> Path:
    return tmp_path / "policy_hooks.db"


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
def sync_engine(database: Database, auth_manager: AuthManagerProto) -> SyncEngine:
    return SyncEngine(db=database, auth_manager=auth_manager, use_mock=True)  # type: ignore[arg-type]


def _make_apply_payload(
    mutations: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build a valid sync-apply JSON envelope."""
    return {
        "version": "1",
        "command": "sync-apply",
        "mutations": mutations,
    }


# --- pre_apply_guard tests ---


def test_default_pre_apply_guard_allows_all(sync_engine: SyncEngine) -> None:
    """Default pre_apply_guard returns all mutations as allowed."""
    mutations = [
        {"type": "status_change", "payload": {"task_gid": "t1", "completed": True}},
        {"type": "date_change", "payload": {"task_gid": "t2", "due_on": "2026-04-01"}},
    ]
    allowed, blocked, reason = sync_engine.pre_apply_guard(mutations)
    assert allowed == mutations
    assert blocked == []
    assert reason is None


def test_custom_pre_apply_guard_blocks_mutations(
    database: Database, auth_manager: AuthManagerProto
) -> None:
    """Custom subclass can block specific mutations via pre_apply_guard."""

    class RestrictedEngine(SyncEngine):
        def pre_apply_guard(
            self, mutations: list[dict]
        ) -> tuple[list[dict], list[dict], str | None]:
            allowed = []
            blocked = []
            for mut in mutations:
                if mut.get("type") == "comment_add":
                    blocked.append(mut)
                else:
                    allowed.append(mut)
            reason = "Comments are disabled" if blocked else None
            return (allowed, blocked, reason)

    engine = RestrictedEngine(db=database, auth_manager=auth_manager, use_mock=True)  # type: ignore[arg-type]

    payload = _make_apply_payload([
        {
            "idempotency_key": "guard-allow-1",
            "type": "status_change",
            "payload": {"task_gid": "task_001", "completed": True},
        },
        {
            "idempotency_key": "guard-block-1",
            "type": "comment_add",
            "payload": {"task_gid": "task_002", "text": "Hello"},
        },
    ])

    result = engine.apply_from_json(payload)

    # The status_change should be applied, comment_add should be blocked
    assert result.applied == 1
    results = result.results_json["data"]["results"]

    # Find the blocked entry
    blocked_entries = [r for r in results if r.get("status") == "blocked"]
    assert len(blocked_entries) == 1
    assert blocked_entries[0]["idempotency_key"] == "guard-block-1"
    assert "Comments are disabled" in blocked_entries[0]["reason"]

    # Find the applied entry
    applied_entries = [r for r in results if r.get("status") == "applied"]
    assert len(applied_entries) == 1

    # Summary should reflect blocked count
    summary = result.results_json["data"]["summary"]
    assert summary["blocked"] == 1
    assert summary["applied"] == 1


# --- allow_field_write tests ---


def test_default_allow_field_write_returns_true(sync_engine: SyncEngine) -> None:
    """Default allow_field_write returns True for all fields."""
    assert sync_engine.allow_field_write("completed", "task_001") is True
    assert sync_engine.allow_field_write("due_on", "task_002") is True
    assert sync_engine.allow_field_write("start_on", "task_003") is True
    assert sync_engine.allow_field_write("comment", "task_004") is True
    assert sync_engine.allow_field_write("section", "task_005") is True


def test_custom_allow_field_write_blocks_mutation(
    database: Database, auth_manager: AuthManagerProto
) -> None:
    """Custom subclass blocking a field write causes mutation to be blocked."""

    class FieldRestrictedEngine(SyncEngine):
        def allow_field_write(self, field_name: str, task_gid: str) -> bool:
            # Block all date changes for task_locked_001
            return not (
                task_gid == "task_locked_001"
                and field_name in ("due_on", "start_on")
            )

    engine = FieldRestrictedEngine(
        db=database, auth_manager=auth_manager, use_mock=True
    )  # type: ignore[arg-type]

    payload = _make_apply_payload([
        {
            "idempotency_key": "field-block-1",
            "type": "date_change",
            "payload": {
                "task_gid": "task_locked_001",
                "due_on": "2026-05-01",
            },
        },
        {
            "idempotency_key": "field-allow-1",
            "type": "date_change",
            "payload": {
                "task_gid": "task_unlocked_001",
                "due_on": "2026-05-01",
            },
        },
    ])

    result = engine.apply_from_json(payload)
    results = result.results_json["data"]["results"]

    # The locked task should be blocked
    blocked = [r for r in results if r.get("status") == "blocked"]
    assert len(blocked) == 1
    assert blocked[0]["idempotency_key"] == "field-block-1"
    assert "Field write denied by policy" in blocked[0]["reason"]

    # The unlocked task should be applied
    applied = [r for r in results if r.get("status") == "applied"]
    assert len(applied) == 1
    assert applied[0]["idempotency_key"] == "field-allow-1"


# --- redact_outbound_payload tests ---


def test_default_redact_outbound_payload_returns_unchanged(
    sync_engine: SyncEngine,
) -> None:
    """Default redact_outbound_payload returns the record unchanged."""
    record = {
        "gid": "task_001",
        "name": "Secret Task",
        "notes": "Very sensitive information",
        "comments": ["private comment"],
    }
    result = sync_engine.redact_outbound_payload(record)
    assert result == record
    assert result is record  # same object, not a copy


def test_custom_redact_outbound_payload_strips_fields(
    database: Database, auth_manager: AuthManagerProto
) -> None:
    """Custom subclass can strip sensitive fields."""

    class RedactingEngine(SyncEngine):
        def redact_outbound_payload(self, record: dict) -> dict:
            redacted = record.copy()
            redacted.pop("notes", None)
            redacted.pop("comments", None)
            return redacted

    engine = RedactingEngine(db=database, auth_manager=auth_manager, use_mock=True)  # type: ignore[arg-type]

    record = {
        "gid": "task_001",
        "name": "Task",
        "notes": "Sensitive notes",
        "comments": ["private"],
    }
    result = engine.redact_outbound_payload(record)
    assert "gid" in result
    assert "name" in result
    assert "notes" not in result
    assert "comments" not in result


# --- Partial failure resume tests ---


def test_partial_failure_resume_skips_completed_mutations(
    database: Database, auth_manager: AuthManagerProto
) -> None:
    """A completed PendingMutation is skipped on re-submission."""
    # Create a completed mutation in the DB
    with database.session() as session:
        session.add(
            PendingMutation(
                task_gid="task_resume_001",
                operation="update_status",
                payload={"task_gid": "task_resume_001", "completed": True},
                idempotency_key="resume-key-completed",
                status="completed",
                applied_at=datetime.now(UTC),
            )
        )

    engine = SyncEngine(db=database, auth_manager=auth_manager, use_mock=True)  # type: ignore[arg-type]

    # Submit a batch containing the already-completed key and a new one
    payload = _make_apply_payload([
        {
            "idempotency_key": "resume-key-completed",
            "type": "status_change",
            "payload": {"task_gid": "task_resume_001", "completed": True},
        },
        {
            "idempotency_key": "resume-key-new",
            "type": "status_change",
            "payload": {"task_gid": "task_resume_002", "completed": False},
        },
    ])

    result = engine.apply_from_json(payload)
    results = result.results_json["data"]["results"]

    # The completed mutation should be skipped
    skipped = [r for r in results if r.get("status") == "skipped"]
    assert len(skipped) == 1
    assert skipped[0]["idempotency_key"] == "resume-key-completed"
    assert skipped[0]["reason"] == "already_applied"

    # The new mutation should be applied
    applied = [r for r in results if r.get("status") == "applied"]
    assert len(applied) == 1
    assert applied[0]["idempotency_key"] == "resume-key-new"

    # Summary should include skipped count
    summary = result.results_json["data"]["summary"]
    assert summary["skipped"] == 1
    assert summary["applied"] == 1


def test_resume_with_no_prior_completions(
    database: Database, auth_manager: AuthManagerProto
) -> None:
    """When no prior completions exist, all mutations are processed normally."""
    engine = SyncEngine(db=database, auth_manager=auth_manager, use_mock=True)  # type: ignore[arg-type]

    payload = _make_apply_payload([
        {
            "idempotency_key": "fresh-key-1",
            "type": "status_change",
            "payload": {"task_gid": "task_fresh_001", "completed": True},
        },
        {
            "idempotency_key": "fresh-key-2",
            "type": "date_change",
            "payload": {"task_gid": "task_fresh_002", "due_on": "2026-06-01"},
        },
    ])

    result = engine.apply_from_json(payload)
    results = result.results_json["data"]["results"]

    # No skipped mutations
    skipped = [r for r in results if r.get("status") == "skipped"]
    assert len(skipped) == 0

    # All applied
    applied = [r for r in results if r.get("status") == "applied"]
    assert len(applied) == 2
    assert result.applied == 2
    assert result.failed == 0


# --- Integration: hooks called during apply_from_json ---


def test_hooks_called_during_apply_from_json_flow(
    database: Database, auth_manager: AuthManagerProto
) -> None:
    """Verify that policy hooks are actually invoked during apply_from_json."""
    call_log: list[str] = []

    class TrackedEngine(SyncEngine):
        def pre_apply_guard(
            self, mutations: list[dict]
        ) -> tuple[list[dict], list[dict], str | None]:
            call_log.append("pre_apply_guard")
            return (mutations, [], None)

        def allow_field_write(self, field_name: str, task_gid: str) -> bool:
            call_log.append(f"allow_field_write:{field_name}:{task_gid}")
            return True

    engine = TrackedEngine(db=database, auth_manager=auth_manager, use_mock=True)  # type: ignore[arg-type]

    payload = _make_apply_payload([
        {
            "idempotency_key": "track-1",
            "type": "status_change",
            "payload": {"task_gid": "task_track_001", "completed": True},
        },
        {
            "idempotency_key": "track-2",
            "type": "date_change",
            "payload": {
                "task_gid": "task_track_002",
                "due_on": "2026-07-01",
                "start_on": "2026-06-15",
            },
        },
    ])

    result = engine.apply_from_json(payload)
    assert result.applied == 2

    # pre_apply_guard should be called once
    assert call_log.count("pre_apply_guard") == 1

    # allow_field_write should be called for each field in each mutation
    assert "allow_field_write:completed:task_track_001" in call_log
    assert "allow_field_write:due_on:task_track_002" in call_log
    assert "allow_field_write:start_on:task_track_002" in call_log


def test_get_field_names_for_operation() -> None:
    """Verify _get_field_names_for_operation maps operations correctly."""
    assert SyncEngine._get_field_names_for_operation(
        "update_status", {}
    ) == ["completed"]
    assert SyncEngine._get_field_names_for_operation(
        "complete_task", {}
    ) == ["completed"]
    assert SyncEngine._get_field_names_for_operation(
        "uncomplete_task", {}
    ) == ["completed"]

    assert SyncEngine._get_field_names_for_operation(
        "update_dates", {"due_on": "2026-01-01", "start_on": "2025-12-01"}
    ) == ["due_on", "start_on"]
    assert SyncEngine._get_field_names_for_operation(
        "update_due_on", {"due_on": "2026-01-01"}
    ) == ["due_on"]
    assert SyncEngine._get_field_names_for_operation(
        "update_start_on", {"start_on": "2026-01-01"}
    ) == ["start_on"]

    assert SyncEngine._get_field_names_for_operation(
        "append_comment", {"text": "hello"}
    ) == ["comment"]
    assert SyncEngine._get_field_names_for_operation(
        "update_section", {"to_list": "Done"}
    ) == ["section"]
