"""Tests for write safety caps (max writes per invocation)."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol
from unittest.mock import patch

import pytest

from asana_org_bridge.config import Settings, SyncConfig
from asana_org_bridge.db import Database, MigrationManager
from asana_org_bridge.models import PendingMutation
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


def _make_mutations(count: int) -> list[dict]:
    """Generate a list of valid mutation dicts for testing."""
    return [
        {
            "type": "status_change",
            "idempotency_key": f"key-{i}",
            "payload": {"task_gid": f"task_{i}", "completed": True},
        }
        for i in range(count)
    ]


def _make_apply_request(count: int) -> dict:
    """Build a full apply request with the given number of mutations."""
    return {
        "version": "1",
        "command": "sync-apply",
        "mutations": _make_mutations(count),
    }


def _settings_with_cap(cap: int) -> Settings:
    """Create a Settings instance with a specific write cap."""
    return Settings(
        sync=SyncConfig(max_writes=cap),
    )


# ============= apply_from_json tests =============


def test_exactly_at_default_cap_accepted(sync_engine: SyncEngine) -> None:
    """60 mutations (default cap) should be accepted."""
    with patch(
        "asana_org_bridge.sync.get_settings",
        return_value=_settings_with_cap(60),
    ):
        result = sync_engine.apply_from_json(_make_apply_request(60))
    assert len(result.errors) == 0
    assert result.applied == 60


def test_one_over_default_cap_rejected(sync_engine: SyncEngine) -> None:
    """61 mutations should be rejected with WRITE_LIMIT_EXCEEDED."""
    with patch(
        "asana_org_bridge.sync.get_settings",
        return_value=_settings_with_cap(60),
    ):
        result = sync_engine.apply_from_json(_make_apply_request(61))
    assert len(result.errors) == 1
    assert "WRITE_LIMIT_EXCEEDED" in result.results_json["error"]["code"]


def test_custom_cap_enforced(sync_engine: SyncEngine) -> None:
    """Cap set to 5: 6 mutations should be rejected."""
    with patch(
        "asana_org_bridge.sync.get_settings",
        return_value=_settings_with_cap(5),
    ):
        result = sync_engine.apply_from_json(_make_apply_request(6))
    assert len(result.errors) == 1
    assert "WRITE_LIMIT_EXCEEDED" in result.results_json["error"]["code"]

    # 5 should pass
    with patch(
        "asana_org_bridge.sync.get_settings",
        return_value=_settings_with_cap(5),
    ):
        result = sync_engine.apply_from_json(_make_apply_request(5))
    assert len(result.errors) == 0
    assert result.applied == 5


def test_cap_zero_means_unlimited(sync_engine: SyncEngine) -> None:
    """Cap of 0 disables the limit entirely."""
    with patch(
        "asana_org_bridge.sync.get_settings",
        return_value=_settings_with_cap(0),
    ):
        result = sync_engine.apply_from_json(_make_apply_request(100))
    assert len(result.errors) == 0
    assert result.applied == 100


def test_error_message_includes_counts(sync_engine: SyncEngine) -> None:
    """Error message should contain the actual count and the limit."""
    with patch(
        "asana_org_bridge.sync.get_settings",
        return_value=_settings_with_cap(10),
    ):
        result = sync_engine.apply_from_json(_make_apply_request(15))
    assert len(result.errors) == 1
    error_msg = result.results_json["error"]["message"]
    assert "15" in error_msg  # actual count
    assert "10" in error_msg  # limit
    assert "ASANA_ORG_MAX_WRITES" in error_msg


def test_error_envelope_structure(sync_engine: SyncEngine) -> None:
    """Error response should follow standard error envelope."""
    with patch(
        "asana_org_bridge.sync.get_settings",
        return_value=_settings_with_cap(5),
    ):
        result = sync_engine.apply_from_json(_make_apply_request(10))
    envelope = result.results_json
    assert envelope["status"] == "error"
    assert envelope["error"]["code"] == "WRITE_LIMIT_EXCEEDED"
    assert isinstance(envelope["error"]["message"], str)
    assert envelope["version"] == "1"
    assert envelope["command"] == "sync-apply"


# ============= apply() (DB path) tests =============


def test_apply_caps_processing_from_db(
    sync_engine: SyncEngine, database: Database
) -> None:
    """apply() should process at most max_writes mutations from DB."""
    # Seed 10 pending mutations in the database
    with database.session() as session:
        for i in range(10):
            mutation = PendingMutation(
                task_gid=f"task_db_{i}",
                operation="update_status",
                payload={"task_gid": f"task_db_{i}", "completed": True},
                idempotency_key=f"db-key-{i}",
                status="pending",
            )
            session.add(mutation)

    with patch(
        "asana_org_bridge.sync.get_settings",
        return_value=_settings_with_cap(3),
    ):
        result = sync_engine.apply()

    # Should have processed only 3 out of 10
    assert result.applied == 3

    # Remaining 7 should still be pending
    with database.session() as session:
        remaining = (
            session.query(PendingMutation)
            .filter(PendingMutation.status == "pending")
            .count()
        )
    assert remaining == 7


def test_apply_unlimited_cap_processes_all(
    sync_engine: SyncEngine, database: Database
) -> None:
    """apply() with cap=0 processes all pending mutations."""
    with database.session() as session:
        for i in range(8):
            mutation = PendingMutation(
                task_gid=f"task_unlim_{i}",
                operation="update_status",
                payload={"task_gid": f"task_unlim_{i}", "completed": True},
                idempotency_key=f"unlim-key-{i}",
                status="pending",
            )
            session.add(mutation)

    with patch(
        "asana_org_bridge.sync.get_settings",
        return_value=_settings_with_cap(0),
    ):
        result = sync_engine.apply()

    assert result.applied == 8


# ============= Config / env-var integration tests =============


def test_max_writes_config_default() -> None:
    """Default max_writes should be 60."""
    config = SyncConfig()
    assert config.max_writes == 60


def test_max_writes_config_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """max_writes should be configurable via ASANA_ORG_MAX_WRITES env var."""
    monkeypatch.setenv("ASANA_ORG_MAX_WRITES", "25")
    config = SyncConfig()
    assert config.max_writes == 25
