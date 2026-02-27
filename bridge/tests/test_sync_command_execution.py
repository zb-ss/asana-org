from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import pytest

from asana_org_bridge.asana_client import AsanaResult
from asana_org_bridge.db import Database, MigrationManager
from asana_org_bridge.models import PendingMutation, TaskSnapshot
from asana_org_bridge.sync import SyncEngine


class AuthManagerProto(Protocol):
    def get_pat(self) -> str | None: ...


@pytest.fixture
def temp_db_path(tmp_path: Path) -> Path:
    return tmp_path / "sync_commands.db"


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


def test_apply_executes_pending_mutation_via_api(
    database: Database, auth_manager: AuthManagerProto
) -> None:
    class FakeClient:
        def update_task(
            self,
            task_gid: str,
            completed: bool | None = None,
            due_on: str | None = None,
            start_on: str | None = None,
        ) -> AsanaResult:
            return AsanaResult(
                success=True,
                data={
                    "gid": task_gid,
                    "completed": completed,
                    "due_on": due_on,
                    "start_on": start_on,
                },
            )

    engine = SyncEngine(db=database, auth_manager=auth_manager, use_mock=False)  # type: ignore[arg-type]
    engine._asana_client = FakeClient()  # type: ignore[assignment]

    with database.session() as session:
        session.add(
            PendingMutation(
                task_gid="task_apply_001",
                operation="update_status",
                payload={"task_gid": "task_apply_001", "completed": True},
                idempotency_key="apply-key-001",
                status="pending",
            )
        )

    result = engine.apply()
    assert result.applied == 1
    assert result.failed == 0

    with database.session() as session:
        mutation = (
            session.query(PendingMutation)
            .filter(PendingMutation.idempotency_key == "apply-key-001")
            .first()
        )
        assert mutation is not None
        assert mutation.status == "completed"


def test_execute_move_task_returns_error_envelope_on_api_failure(
    database: Database, auth_manager: AuthManagerProto
) -> None:
    class FakeClient:
        def get_sections(self, project_gid: str) -> list[dict[str, str]]:  # noqa: ARG002
            raise AssertionError("gid path must not attempt name resolution")

        def move_task_to_section(self, task_gid: str, section_gid: str) -> AsanaResult:
            return AsanaResult(
                success=False, error="Section not found", status_code=404
            )

    engine = SyncEngine(db=database, auth_manager=auth_manager, use_mock=False)  # type: ignore[arg-type]
    engine._asana_client = FakeClient()  # type: ignore[assignment]

    response = engine.execute_move_task(
        task_gid="task_move_001",
        from_list="Backlog",
        to_list="sect_missing",
        idempotency_key="move-key-001",
    )

    assert response["status"] == "error"
    assert response["error"]["code"] == "NOT_FOUND"
    assert "Section not found" in response["error"]["message"]


def test_apply_from_json_maps_rate_limited_error_code(
    database: Database, auth_manager: AuthManagerProto
) -> None:
    class FakeClient:
        def add_comment(self, task_gid: str, text: str) -> AsanaResult:  # noqa: ARG002
            return AsanaResult(
                success=False, error="Too many requests", status_code=429
            )

    engine = SyncEngine(db=database, auth_manager=auth_manager, use_mock=False)  # type: ignore[arg-type]
    engine._asana_client = FakeClient()  # type: ignore[assignment]

    result = engine.apply_from_json(
        {
            "version": "1",
            "command": "sync-apply",
            "mutations": [
                {
                    "idempotency_key": "mut_rate_limit_001",
                    "type": "comment_add",
                    "payload": {
                        "task_gid": "task_comment_rate_limited",
                        "text": "retry later",
                    },
                }
            ],
        }
    )

    assert result.failed == 1
    assert (
        result.results_json["data"]["results"][0]["details"]["code"] == "RATE_LIMITED"
    )


def test_apply_from_json_handles_null_attempts_and_preserves_error_code(
    database: Database, auth_manager: AuthManagerProto
) -> None:
    class FakeClient:
        def add_comment(self, task_gid: str, text: str) -> AsanaResult:  # noqa: ARG002
            return AsanaResult(
                success=False, error="Resource conflict", status_code=409
            )

    with database.session() as session:
        session.add(
            PendingMutation(
                task_gid="task_null_attempts",
                operation="append_comment",
                payload={"task_gid": "task_null_attempts", "text": "old"},
                idempotency_key="mut_null_attempts_001",
                status="failed",
                attempts=None,
            )
        )

    engine = SyncEngine(db=database, auth_manager=auth_manager, use_mock=False)  # type: ignore[arg-type]
    engine._asana_client = FakeClient()  # type: ignore[assignment]

    result = engine.apply_from_json(
        {
            "version": "1",
            "command": "sync-apply",
            "mutations": [
                {
                    "idempotency_key": "mut_null_attempts_001",
                    "type": "comment_add",
                    "payload": {
                        "task_gid": "task_null_attempts",
                        "text": "new",
                    },
                }
            ],
        }
    )

    assert result.failed == 1
    assert result.results_json["data"]["results"][0]["details"]["code"] == "CONFLICT"

    with database.session() as session:
        mutation = (
            session.query(PendingMutation)
            .filter(PendingMutation.idempotency_key == "mut_null_attempts_001")
            .first()
        )
        assert mutation is not None
        assert mutation.attempts == 2


def test_execute_move_task_resolves_to_list_name_before_api_call(
    database: Database, auth_manager: AuthManagerProto
) -> None:
    calls: list[tuple[str, str]] = []

    class FakeClient:
        def get_sections(self, project_gid: str) -> list[dict[str, str]]:
            assert project_gid == "proj_123"
            return [
                {"gid": "sect_target", "name": "In Progress"},
                {"gid": "sect_other", "name": "Done"},
            ]

        def move_task_to_section(self, task_gid: str, section_gid: str) -> AsanaResult:
            calls.append((task_gid, section_gid))
            return AsanaResult(success=True, data={"gid": task_gid}, status_code=200)

    with database.session() as session:
        session.add(
            TaskSnapshot(
                gid="task_move_name_001",
                permalink_url="https://app.asana.com/0/0/task_move_name_001",
                name="Move me",
                completed=False,
                modified_at=datetime.now(UTC),
                project_gid="proj_123",
                section_gid="sect_source",
                section_name="Backlog",
            )
        )

    engine = SyncEngine(db=database, auth_manager=auth_manager, use_mock=False)  # type: ignore[arg-type]
    engine._asana_client = FakeClient()  # type: ignore[assignment]

    response = engine.execute_move_task(
        task_gid="task_move_name_001",
        from_list="Backlog",
        to_list="In Progress",
        idempotency_key="move-key-resolve-name",
    )

    assert response["status"] == "success"
    assert calls == [("task_move_name_001", "sect_target")]


def test_apply_from_json_maps_api_error_code(
    database: Database, auth_manager: AuthManagerProto
) -> None:
    class FakeClient:
        def add_comment(self, task_gid: str, text: str) -> AsanaResult:
            return AsanaResult(
                success=False, error="Resource conflict", status_code=409
            )

    engine = SyncEngine(db=database, auth_manager=auth_manager, use_mock=False)  # type: ignore[arg-type]
    engine._asana_client = FakeClient()  # type: ignore[assignment]

    result = engine.apply_from_json(
        {
            "version": "1",
            "command": "sync-apply",
            "mutations": [
                {
                    "idempotency_key": "mut_conflict_001",
                    "type": "comment_add",
                    "payload": {
                        "task_gid": "task_comment_conflict",
                        "text": "conflicting update",
                    },
                }
            ],
        }
    )

    assert result.failed == 1
    assert result.results_json["data"]["results"][0]["details"]["code"] == "CONFLICT"


def test_execute_comment_append_is_idempotent_in_mock_mode(
    database: Database, auth_manager: AuthManagerProto
) -> None:
    engine = SyncEngine(db=database, auth_manager=auth_manager, use_mock=True)  # type: ignore[arg-type]

    first = engine.execute_comment_append(
        task_gid="task_comment_001",
        text="Looks good",
        idempotency_key="comment-key-001",
    )
    second = engine.execute_comment_append(
        task_gid="task_comment_001",
        text="Looks good",
        idempotency_key="comment-key-001",
    )

    assert first["status"] == "success"
    assert first["data"]["result"]["status"] == "applied"
    assert second["status"] == "success"
    assert second["data"]["result"]["message"] == "Already applied (idempotent)"

    with database.session() as session:
        count = (
            session.query(PendingMutation)
            .filter(PendingMutation.idempotency_key == "comment-key-001")
            .count()
        )
        assert count == 1
