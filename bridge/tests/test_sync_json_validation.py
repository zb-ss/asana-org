import json
from pathlib import Path
from typing import Protocol

import pytest

from asana_org_bridge.db import Database, MigrationManager
from asana_org_bridge.models import RequestIdempotency
from asana_org_bridge.sync import SyncEngine


class AuthManagerProto(Protocol):
    def get_pat(self) -> str | None: ...


@pytest.fixture
def temp_db_path(tmp_path: Path) -> Path:
    return tmp_path / "test_bridge.db"


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


def test_missing_or_invalid_mutations_array(sync_engine: SyncEngine) -> None:
    # Top-level is not dict
    res = sync_engine.apply_from_json(["not", "a", "dict"])  # type: ignore[arg-type]
    assert len(res.errors) == 1
    assert "Input must be a JSON object" in res.errors[0]

    # Missing 'mutations' key (with valid version/command)
    res = sync_engine.apply_from_json(
        {"version": "1", "command": "sync-apply", "other": "key"}
    )
    assert len(res.errors) == 1
    assert "Missing 'mutations' array in input" in res.errors[0]

    # 'mutations' is not a list (with valid version/command)
    res = sync_engine.apply_from_json(
        {"version": "1", "command": "sync-apply", "mutations": "not_a_list"}
    )
    assert len(res.errors) == 1
    assert "'mutations' must be an array" in res.errors[0]


def test_invalid_mutation_type(sync_engine: SyncEngine) -> None:
    payload = {
        "version": "1",
        "command": "sync-apply",
        "mutations": [{"type": "invalid_type", "payload": {"task_gid": "123"}}],
    }
    res = sync_engine.apply_from_json(payload)
    assert len(res.errors) == 1
    assert "unknown type 'invalid_type'" in res.errors[0]


def test_missing_required_fields_type_payload(sync_engine: SyncEngine) -> None:
    # Missing type (with valid version/command)
    res = sync_engine.apply_from_json(
        {
            "version": "1",
            "command": "sync-apply",
            "mutations": [{"payload": {"task_gid": "123"}}],
        }
    )
    assert len(res.errors) == 1
    assert "missing 'type' field" in res.errors[0]

    # Missing payload (with valid version/command)
    res = sync_engine.apply_from_json(
        {
            "version": "1",
            "command": "sync-apply",
            "mutations": [{"type": "status_change"}],
        }
    )
    assert len(res.errors) == 1
    assert "missing 'payload' field" in res.errors[0]

    # Payload not dict (with valid version/command)
    res = sync_engine.apply_from_json(
        {
            "version": "1",
            "command": "sync-apply",
            "mutations": [{"type": "status_change", "payload": "not_dict"}],
        }
    )
    assert len(res.errors) == 1
    assert "'payload' must be an object" in res.errors[0]


def test_missing_task_gid_in_payload(sync_engine: SyncEngine) -> None:
    res = sync_engine.apply_from_json(
        {
            "version": "1",
            "command": "sync-apply",
            "mutations": [{"type": "status_change", "payload": {"completed": True}}],
        }
    )
    assert len(res.errors) == 1
    assert "missing 'task_gid'" in res.errors[0]


def test_task_move_missing_to_list(sync_engine: SyncEngine) -> None:
    res = sync_engine.apply_from_json(
        {
            "version": "1",
            "command": "sync-apply",
            "mutations": [{"type": "task_move", "payload": {"task_gid": "123"}}],
        }
    )
    assert any("requires 'to_list' or 'to_section_gid'" in e for e in res.errors)


def test_comment_add_missing_text(sync_engine: SyncEngine) -> None:
    res = sync_engine.apply_from_json(
        {
            "version": "1",
            "command": "sync-apply",
            "mutations": [{"type": "comment_add", "payload": {"task_gid": "123"}}],
        }
    )
    assert any("requires 'text'" in e for e in res.errors)


def test_missing_version_field(sync_engine: SyncEngine) -> None:
    """Test that missing 'version' field returns validation error."""
    res = sync_engine.apply_from_json(
        {
            "command": "sync-apply",
            "mutations": [
                {
                    "type": "status_change",
                    "payload": {"task_gid": "123", "completed": True},
                }
            ],
        }
    )
    assert len(res.errors) >= 1
    assert any("version" in e for e in res.errors)


def test_missing_command_field(sync_engine: SyncEngine) -> None:
    """Test that missing 'command' field returns validation error."""
    res = sync_engine.apply_from_json(
        {
            "version": "1",
            "mutations": [
                {
                    "type": "status_change",
                    "payload": {"task_gid": "123", "completed": True},
                }
            ],
        }
    )
    assert len(res.errors) >= 1
    assert any("command" in e for e in res.errors)


def test_invalid_version(sync_engine: SyncEngine) -> None:
    """Test that unsupported version returns validation error."""
    res = sync_engine.apply_from_json(
        {
            "version": "2",
            "command": "sync-apply",
            "mutations": [
                {
                    "type": "status_change",
                    "payload": {"task_gid": "123", "completed": True},
                }
            ],
        }
    )
    assert any("version" in e and "unsupported" in e.lower() for e in res.errors)


def test_invalid_command(sync_engine: SyncEngine) -> None:
    """Test that invalid command returns validation error."""
    res = sync_engine.apply_from_json(
        {
            "version": "1",
            "command": "invalid-command",
            "mutations": [
                {
                    "type": "status_change",
                    "payload": {"task_gid": "123", "completed": True},
                }
            ],
        }
    )
    assert any("command" in e for e in res.errors)


def test_valid_request_with_version_and_command(sync_engine: SyncEngine) -> None:
    """Test that a valid request with all required fields succeeds."""
    res = sync_engine.apply_from_json(
        {
            "version": "1",
            "command": "sync-apply",
            "mutations": [
                {
                    "type": "status_change",
                    "payload": {"task_gid": "123", "completed": True},
                }
            ],
            "dry_run": True,
        }
    )
    # Should not have validation errors
    assert len(res.errors) == 0 or not any(
        "version" in e or "command" in e for e in res.errors
    )


# ============= Request-level Idempotency Tests =============


def test_request_idempotency_key_returns_cached_response(
    sync_engine: SyncEngine, database: Database
) -> None:
    """Test that repeating a request with the same idempotency_key returns cached response."""
    request_id = "test-idempotency-key-001"

    # First request - should process normally
    first_response = sync_engine.apply_from_json(
        {
            "version": "1",
            "command": "sync-apply",
            "idempotency_key": request_id,
            "mutations": [
                {
                    "type": "status_change",
                    "payload": {"task_gid": "task_123", "completed": True},
                }
            ],
        }
    )

    assert len(first_response.errors) == 0
    assert first_response.applied == 1
    first_results = first_response.results_json["data"]["results"]

    # Second request with same key - should return cached response
    second_response = sync_engine.apply_from_json(
        {
            "version": "1",
            "command": "sync-apply",
            "idempotency_key": request_id,
            "mutations": [
                {
                    "type": "status_change",
                    "payload": {"task_gid": "task_123", "completed": True},
                }
            ],
        }
    )

    assert len(second_response.errors) == 0
    assert second_response.applied == first_response.applied
    assert second_response.results_json["data"]["results"] == first_results

    # Verify the cached response matches
    assert second_response.results_json == first_response.results_json


def test_request_idempotency_stores_response_in_db(
    sync_engine: SyncEngine, database: Database
) -> None:
    """Test that completed requests are stored in request_idempotency table."""
    request_id = "test-idempotency-key-002"

    # Execute request
    response = sync_engine.apply_from_json(
        {
            "version": "1",
            "command": "sync-apply",
            "idempotency_key": request_id,
            "mutations": [
                {
                    "type": "comment_add",
                    "payload": {"task_gid": "task_456", "text": "Test comment"},
                }
            ],
        }
    )

    assert len(response.errors) == 0

    # Verify stored in database
    with database.session() as session:
        stored = (
            session.query(RequestIdempotency)
            .filter(RequestIdempotency.idempotency_key == request_id)
            .first()
        )

        assert stored is not None
        assert stored.status == "completed"
        assert stored.response_json is not None

        # Verify stored response matches
        stored_response = json.loads(stored.response_json)
        assert stored_response["version"] == "1"
        assert stored_response["command"] == "sync-apply"
        assert stored_response["data"]["summary"]["applied"] == 1


def test_deterministic_response_for_repeated_key(
    sync_engine: SyncEngine, database: Database
) -> None:
    """Test that repeated requests with same key return identical response structure."""
    request_id = "test-idempotency-key-003"

    # Execute first request
    first = sync_engine.apply_from_json(
        {
            "version": "1",
            "command": "sync-apply",
            "idempotency_key": request_id,
            "mutations": [
                {
                    "type": "status_change",
                    "payload": {"task_gid": "task_789", "completed": True},
                },
            ],
        }
    )

    # Execute second request with same key
    second = sync_engine.apply_from_json(
        {
            "version": "1",
            "command": "sync-apply",
            "idempotency_key": request_id,
            "mutations": [
                {
                    "type": "status_change",
                    "payload": {"task_gid": "task_789", "completed": True},
                },
            ],
        }
    )

    # Responses should be identical (including all fields)
    assert first.results_json == second.results_json
    assert first.applied == second.applied
    assert first.failed == second.failed

    # Status should indicate success
    assert first.results_json["status"] == "success"


def test_different_idempotency_keys_processed_separately(
    sync_engine: SyncEngine, database: Database
) -> None:
    """Test that different idempotency keys result in separate processings."""

    # First request with key A
    response_a = sync_engine.apply_from_json(
        {
            "version": "1",
            "command": "sync-apply",
            "idempotency_key": "key-alpha",
            "mutations": [
                {
                    "type": "status_change",
                    "payload": {"task_gid": "task_a", "completed": True},
                }
            ],
        }
    )

    # Second request with key B (different)
    response_b = sync_engine.apply_from_json(
        {
            "version": "1",
            "command": "sync-apply",
            "idempotency_key": "key-beta",
            "mutations": [
                {
                    "type": "status_change",
                    "payload": {"task_gid": "task_b", "completed": False},
                }
            ],
        }
    )

    # Both should succeed
    assert len(response_a.errors) == 0
    assert len(response_b.errors) == 0

    # Results should be different (different task_gids)
    results_a = response_a.results_json["data"]["results"]
    results_b = response_b.results_json["data"]["results"]

    assert results_a[0]["idempotency_key"] != results_b[0]["idempotency_key"]

    # Both should be stored in database
    with database.session() as session:
        count = (
            session.query(RequestIdempotency)
            .filter(RequestIdempotency.idempotency_key.in_(["key-alpha", "key-beta"]))
            .count()
        )
        assert count == 2


def test_request_without_idempotency_key_always_processes(
    sync_engine: SyncEngine, database: Database
) -> None:
    """Test that requests without idempotency_key are always processed fresh."""

    # First request without key
    first = sync_engine.apply_from_json(
        {
            "version": "1",
            "command": "sync-apply",
            "mutations": [
                {
                    "type": "status_change",
                    "payload": {"task_gid": "task_no_key", "completed": True},
                }
            ],
        }
    )

    assert len(first.errors) == 0
    assert first.applied == 1

    # Second request without key - should still process and not be cached
    second = sync_engine.apply_from_json(
        {
            "version": "1",
            "command": "sync-apply",
            "mutations": [
                {
                    "type": "status_change",
                    "payload": {"task_gid": "task_no_key2", "completed": True},
                }
            ],
        }
    )

    assert len(second.errors) == 0
    assert second.applied == 1

    # Results should be different (different tasks)
    assert (
        first.results_json["data"]["results"][0]
        != second.results_json["data"]["results"][0]
    )


def test_request_hash_mismatch_with_reused_key(
    sync_engine: SyncEngine, database: Database
) -> None:
    """Test that reusing idempotency key with different payload returns error.

    This protects against accidental key reuse with different mutations,
    ensuring idempotency keys are used for exactly one request.
    """
    request_id = "test-hash-mismatch-001"

    # First request with idempotency key
    first_response = sync_engine.apply_from_json(
        {
            "version": "1",
            "command": "sync-apply",
            "idempotency_key": request_id,
            "mutations": [
                {
                    "type": "status_change",
                    "payload": {"task_gid": "task_hash_1", "completed": True},
                }
            ],
        }
    )

    assert len(first_response.errors) == 0
    assert first_response.applied == 1
    first_results = first_response.results_json["data"]["results"]
    assert len(first_results) == 1
    assert (
        first_results[0]["idempotency_key"] != request_id
    )  # Mutation key is different

    # Second request with SAME idempotency key but DIFFERENT payload
    second_response = sync_engine.apply_from_json(
        {
            "version": "1",
            "command": "sync-apply",
            "idempotency_key": request_id,  # Same key
            "mutations": [
                {
                    "type": "status_change",
                    "payload": {"task_gid": "task_hash_2", "completed": False},
                }
            ],  # Different task
        }
    )

    # Should return error due to hash mismatch
    assert len(second_response.errors) >= 1
    assert any("hash mismatch" in e.lower() for e in second_response.errors), (
        f"Expected hash mismatch error, got: {second_response.errors}"
    )

    # Verify error message mentions the idempotency key and suggests new key
    error_msg = second_response.errors[0]
    assert request_id in error_msg
    assert (
        "different" in error_msg.lower() or "new idempotency_key" in error_msg.lower()
    )

    # Verify the original response was not overwritten
    with database.session() as session:
        stored = (
            session.query(RequestIdempotency)
            .filter(RequestIdempotency.idempotency_key == request_id)
            .first()
        )

        assert stored is not None
        assert stored.status == "completed"
        assert stored.response_json is not None
        # The stored response should be from the first request
        stored_response = json.loads(stored.response_json)
        first_task_gid = first_results[0]["details"]["task_gid"]
        assert (
            stored_response["data"]["results"][0]["details"]["task_gid"]
            == first_task_gid
        )


def test_rejects_invalid_request_and_mutation_idempotency_keys(
    sync_engine: SyncEngine,
) -> None:
    long_key = "k" * 129
    result = sync_engine.apply_from_json(
        {
            "version": "1",
            "command": "sync-apply",
            "idempotency_key": long_key,
            "mutations": [
                {
                    "idempotency_key": 123,
                    "type": "status_change",
                    "payload": {"task_gid": "123", "completed": True},
                }
            ],
        }
    )

    assert any("idempotency_key" in error for error in result.errors)


def test_rejects_invalid_task_gid_type_and_length(sync_engine: SyncEngine) -> None:
    result_type = sync_engine.apply_from_json(
        {
            "version": "1",
            "command": "sync-apply",
            "mutations": [
                {
                    "type": "status_change",
                    "payload": {"task_gid": 12345, "completed": True},
                }
            ],
        }
    )
    assert any("missing 'task_gid'" in error for error in result_type.errors)

    result_length = sync_engine.apply_from_json(
        {
            "version": "1",
            "command": "sync-apply",
            "mutations": [
                {
                    "type": "status_change",
                    "payload": {"task_gid": "t" * 65, "completed": True},
                }
            ],
        }
    )
    assert any(
        "task_gid" in error and "max length" in error for error in result_length.errors
    )


def test_rejects_invalid_comment_text_type_and_length(sync_engine: SyncEngine) -> None:
    result_type = sync_engine.apply_from_json(
        {
            "version": "1",
            "command": "sync-apply",
            "mutations": [
                {
                    "type": "comment_add",
                    "payload": {"task_gid": "task_1", "text": 42},
                }
            ],
        }
    )
    assert any("'text' must be a string" in error for error in result_type.errors)

    result_length = sync_engine.apply_from_json(
        {
            "version": "1",
            "command": "sync-apply",
            "mutations": [
                {
                    "type": "comment_add",
                    "payload": {"task_gid": "task_1", "text": "x" * 5001},
                }
            ],
        }
    )
    assert any("'text' exceeds max length" in error for error in result_length.errors)


def test_rejects_payload_too_large(sync_engine: SyncEngine) -> None:
    huge_text = "x" * 40000
    result = sync_engine.apply_from_json(
        {
            "version": "1",
            "command": "sync-apply",
            "mutations": [
                {
                    "type": "comment_add",
                    "payload": {"task_gid": "task_big", "text": huge_text},
                }
            ],
        }
    )

    assert any("payload exceeds size limit" in error for error in result.errors)
