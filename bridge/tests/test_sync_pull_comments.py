"""Tests for sync-pull --include-comments functionality."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol, cast
from unittest.mock import MagicMock, patch

import pytest

from asana_org_bridge.asana_client import AsanaClient
from asana_org_bridge.db import Database, MigrationManager
from asana_org_bridge.models import TaskSnapshot
from asana_org_bridge.sync import MockDataGenerator, SyncEngine


# ---------- helpers / fakes ----------


class FakeResponse:
    """Minimal fake for requests.Response."""

    def __init__(
        self,
        status_code: int,
        json_data: Any = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self._json_data = json_data
        self.headers = headers or {}
        self.text = ""

    def json(self) -> Any:
        return self._json_data


class AuthManagerProto(Protocol):
    def get_pat(self) -> str | None: ...


class MockAuthManager:
    def get_pat(self) -> str:
        return "mock_pat"


# ---------- fixtures ----------


@pytest.fixture
def temp_db_path(tmp_path: Path) -> Path:
    return tmp_path / "comments_test.db"


@pytest.fixture
def database(temp_db_path: Path) -> Database:
    db = Database(db_path=temp_db_path, echo=False)
    MigrationManager(db).migrate()
    return db


@pytest.fixture
def auth_manager() -> AuthManagerProto:
    return MockAuthManager()


# ---------- AsanaClient.get_stories tests ----------


class TestGetStories:
    """Tests for AsanaClient.get_stories()."""

    def test_get_stories_returns_only_comments(self) -> None:
        """Only comment-type stories should be returned, not system events."""
        client = AsanaClient(pat="test_pat", max_retries=1)
        api_response = {
            "data": [
                {
                    "gid": "story_1",
                    "resource_subtype": "comment_added",
                    "type": "comment",
                    "text": "Great progress!",
                    "created_by": {"gid": "user_1", "name": "Alice"},
                    "created_at": "2026-02-20T10:00:00.000Z",
                },
                {
                    "gid": "story_2",
                    "resource_subtype": "added_to_project",
                    "type": "system",
                    "text": "added to Project X",
                    "created_by": {"gid": "user_1", "name": "Alice"},
                    "created_at": "2026-02-20T09:00:00.000Z",
                },
                {
                    "gid": "story_3",
                    "resource_subtype": "comment_added",
                    "type": "comment",
                    "text": "Let me review this.",
                    "created_by": {"gid": "user_2", "name": "Bob"},
                    "created_at": "2026-02-21T14:00:00.000Z",
                },
                {
                    "gid": "story_4",
                    "resource_subtype": "assigned",
                    "type": "system",
                    "text": "assigned to Alice",
                    "created_by": {"gid": "user_2", "name": "Bob"},
                    "created_at": "2026-02-19T08:00:00.000Z",
                },
            ]
        }

        cast(Any, client._session).request = lambda **_: FakeResponse(
            status_code=200, json_data=api_response
        )

        stories = client.get_stories("task_123")

        assert len(stories) == 2
        assert stories[0]["gid"] == "story_1"
        assert stories[0]["text"] == "Great progress!"
        assert stories[0]["created_by"]["name"] == "Alice"
        assert stories[1]["gid"] == "story_3"
        assert stories[1]["text"] == "Let me review this."

    def test_get_stories_empty_response(self) -> None:
        """An empty stories response should return an empty list."""
        client = AsanaClient(pat="test_pat", max_retries=1)
        cast(Any, client._session).request = lambda **_: FakeResponse(
            status_code=200, json_data={"data": []}
        )

        stories = client.get_stories("task_no_comments")
        assert stories == []

    def test_get_stories_uses_correct_endpoint(self) -> None:
        """Verify the correct API endpoint and opt_fields are used."""
        client = AsanaClient(pat="test_pat", max_retries=1)
        captured_kwargs: dict[str, Any] = {}

        def capture_request(**kwargs: Any) -> FakeResponse:
            captured_kwargs.update(kwargs)
            return FakeResponse(status_code=200, json_data={"data": []})

        cast(Any, client._session).request = capture_request

        client.get_stories("task_xyz")

        assert "/tasks/task_xyz/stories" in captured_kwargs["url"]
        params = captured_kwargs.get("params", {})
        assert "created_by" in params.get("opt_fields", "")
        assert "text" in params.get("opt_fields", "")
        assert "resource_subtype" in params.get("opt_fields", "")

    def test_get_stories_with_custom_opt_fields(self) -> None:
        """Custom opt_fields should override the defaults."""
        client = AsanaClient(pat="test_pat", max_retries=1)
        captured_kwargs: dict[str, Any] = {}

        def capture_request(**kwargs: Any) -> FakeResponse:
            captured_kwargs.update(kwargs)
            return FakeResponse(status_code=200, json_data={"data": []})

        cast(Any, client._session).request = capture_request

        client.get_stories("task_xyz", opt_fields="text,type")

        params = captured_kwargs.get("params", {})
        assert params.get("opt_fields") == "text,type"

    def test_get_stories_filters_by_type_comment(self) -> None:
        """Stories with type=comment should be included even without resource_subtype."""
        client = AsanaClient(pat="test_pat", max_retries=1)
        api_response = {
            "data": [
                {
                    "gid": "story_legacy",
                    "type": "comment",
                    "text": "Legacy comment format",
                    "created_by": {"gid": "user_1", "name": "Alice"},
                    "created_at": "2026-02-20T10:00:00.000Z",
                },
            ]
        }
        cast(Any, client._session).request = lambda **_: FakeResponse(
            status_code=200, json_data=api_response
        )

        stories = client.get_stories("task_legacy")
        assert len(stories) == 1
        assert stories[0]["gid"] == "story_legacy"


# ---------- MockDataGenerator.generate_stories tests ----------


class TestMockStories:
    """Tests for MockDataGenerator.generate_stories()."""

    def test_generates_deterministic_stories(self) -> None:
        """Same task_gid should produce same stories every time."""
        stories_a = MockDataGenerator.generate_stories("task_001")
        stories_b = MockDataGenerator.generate_stories("task_001")
        assert stories_a == stories_b

    def test_generates_two_or_three_stories(self) -> None:
        """Each task should get 2 or 3 mock stories."""
        for task_gid in ["task_001", "task_002", "task_003", "task_004", "task_005"]:
            stories = MockDataGenerator.generate_stories(task_gid)
            assert 2 <= len(stories) <= 3, f"Unexpected count for {task_gid}"

    def test_stories_have_required_fields(self) -> None:
        """Each story must have gid, created_by, text, and created_at."""
        stories = MockDataGenerator.generate_stories("task_001")
        for story in stories:
            assert "gid" in story
            assert "created_by" in story
            assert "name" in story["created_by"]
            assert "text" in story
            assert "created_at" in story

    def test_different_tasks_can_produce_different_stories(self) -> None:
        """Different task_gids should produce different story content."""
        stories_1 = MockDataGenerator.generate_stories("task_001")
        stories_2 = MockDataGenerator.generate_stories("task_002")
        # At minimum, the gids should differ
        assert stories_1[0]["gid"] != stories_2[0]["gid"]


# ---------- Pull with include_comments tests ----------


class TestPullWithComments:
    """Tests for SyncEngine.pull() with include_comments=True."""

    def test_pull_mock_mode_includes_stories(
        self,
        database: Database,
        auth_manager: AuthManagerProto,
    ) -> None:
        """In mock mode with include_comments=True, tasks should have stories."""
        engine = SyncEngine(db=database, auth_manager=auth_manager, use_mock=True)
        result = engine.pull(include_comments=True)

        assert result.tasks_pulled > 0
        for task in result.tasks:
            assert "stories" in task, f"Task {task['gid']} missing stories"
            assert len(task["stories"]) >= 2

    def test_pull_mock_mode_without_comments_has_no_stories(
        self,
        database: Database,
        auth_manager: AuthManagerProto,
    ) -> None:
        """In mock mode without include_comments, tasks should NOT have stories."""
        engine = SyncEngine(db=database, auth_manager=auth_manager, use_mock=True)
        result = engine.pull(include_comments=False)

        assert result.tasks_pulled > 0
        for task in result.tasks:
            assert "stories" not in task, (
                f"Task {task['gid']} should not have stories"
            )

    def test_pull_stores_stories_in_snapshot(
        self,
        database: Database,
        auth_manager: AuthManagerProto,
    ) -> None:
        """stories_json column should be populated when include_comments=True."""
        engine = SyncEngine(db=database, auth_manager=auth_manager, use_mock=True)
        engine.pull(include_comments=True)

        with database.session() as session:
            snapshots = session.query(TaskSnapshot).all()
            assert len(snapshots) > 0

            for snapshot in snapshots:
                assert snapshot.stories_json is not None, (
                    f"Snapshot for {snapshot.gid} has null stories_json"
                )
                stories = json.loads(snapshot.stories_json)
                assert isinstance(stories, list)
                assert len(stories) >= 2

    def test_pull_without_comments_has_null_stories_json(
        self,
        database: Database,
        auth_manager: AuthManagerProto,
    ) -> None:
        """stories_json column should be null when include_comments is not set."""
        engine = SyncEngine(db=database, auth_manager=auth_manager, use_mock=True)
        engine.pull(include_comments=False)

        with database.session() as session:
            snapshots = session.query(TaskSnapshot).all()
            assert len(snapshots) > 0

            for snapshot in snapshots:
                assert snapshot.stories_json is None, (
                    f"Snapshot for {snapshot.gid} should have null stories_json"
                )

    def test_pull_live_mode_calls_get_stories(
        self,
        database: Database,
        auth_manager: AuthManagerProto,
    ) -> None:
        """In live mode, get_stories should be called for each task."""
        engine = SyncEngine(db=database, auth_manager=auth_manager, use_mock=True)
        # Override to use a fake client
        engine.use_mock = False

        fake_client = MagicMock()
        fake_client.get_user_task_list.return_value = {"gid": "utl_1"}
        fake_client.get_sections.return_value = [
            {"gid": "sect_1", "name": "To Do"},
        ]

        # Create a fake AsanaTask-like object
        fake_task = MagicMock()
        fake_task.gid = "task_live_001"
        fake_task.name = "Live Task"
        fake_task.completed = False
        fake_task.permalink_url = "https://app.asana.com/0/0/task_live_001"
        from datetime import UTC, datetime
        fake_task.modified_at = datetime.now(UTC)
        fake_task.due_on = None
        fake_task.due_at = None
        fake_task.start_on = None
        fake_task.notes = "Test notes"
        fake_task.memberships = []

        fake_client.get_tasks_for_section.return_value = [fake_task]
        fake_client.get_stories.return_value = [
            {
                "gid": "story_live_1",
                "created_by": {"name": "Alice"},
                "text": "Test comment",
                "created_at": "2026-02-20T10:00:00.000Z",
            }
        ]

        engine._asana_client = fake_client

        with patch(
            "asana_org_bridge.sync.get_settings"
        ) as mock_settings:
            mock_settings.return_value.sync.workspace_gid = "ws_123"
            result = engine.pull(include_comments=True)

        fake_client.get_stories.assert_called_once_with("task_live_001")
        assert len(result.tasks) == 1
        assert result.tasks[0]["stories"][0]["text"] == "Test comment"

    def test_pull_live_mode_no_stories_without_flag(
        self,
        database: Database,
        auth_manager: AuthManagerProto,
    ) -> None:
        """In live mode without include_comments, get_stories should NOT be called."""
        engine = SyncEngine(db=database, auth_manager=auth_manager, use_mock=True)
        engine.use_mock = False

        fake_client = MagicMock()
        fake_client.get_user_task_list.return_value = {"gid": "utl_1"}
        fake_client.get_sections.return_value = [
            {"gid": "sect_1", "name": "To Do"},
        ]

        fake_task = MagicMock()
        fake_task.gid = "task_live_002"
        fake_task.name = "Another Live Task"
        fake_task.completed = False
        fake_task.permalink_url = "https://app.asana.com/0/0/task_live_002"
        from datetime import UTC, datetime
        fake_task.modified_at = datetime.now(UTC)
        fake_task.due_on = None
        fake_task.due_at = None
        fake_task.start_on = None
        fake_task.notes = None
        fake_task.memberships = []

        fake_client.get_tasks_for_section.return_value = [fake_task]
        engine._asana_client = fake_client

        with patch(
            "asana_org_bridge.sync.get_settings"
        ) as mock_settings:
            mock_settings.return_value.sync.workspace_gid = "ws_123"
            engine.pull(include_comments=False)

        fake_client.get_stories.assert_not_called()

    def test_pull_stories_fetch_failure_graceful(
        self,
        database: Database,
        auth_manager: AuthManagerProto,
    ) -> None:
        """If fetching stories fails for a task, it should not break the pull."""
        engine = SyncEngine(db=database, auth_manager=auth_manager, use_mock=True)
        engine.use_mock = False

        fake_client = MagicMock()
        fake_client.get_user_task_list.return_value = {"gid": "utl_1"}
        fake_client.get_sections.return_value = [
            {"gid": "sect_1", "name": "To Do"},
        ]

        fake_task = MagicMock()
        fake_task.gid = "task_fail_stories"
        fake_task.name = "Task with failing stories"
        fake_task.completed = False
        fake_task.permalink_url = "https://app.asana.com/0/0/task_fail_stories"
        from datetime import UTC, datetime
        fake_task.modified_at = datetime.now(UTC)
        fake_task.due_on = None
        fake_task.due_at = None
        fake_task.start_on = None
        fake_task.notes = None
        fake_task.memberships = []

        fake_client.get_tasks_for_section.return_value = [fake_task]
        fake_client.get_stories.side_effect = RuntimeError("API error")
        engine._asana_client = fake_client

        with patch(
            "asana_org_bridge.sync.get_settings"
        ) as mock_settings:
            mock_settings.return_value.sync.workspace_gid = "ws_123"
            result = engine.pull(include_comments=True)

        # Pull should succeed despite stories failure
        assert result.tasks_pulled == 1
        assert result.tasks[0]["stories"] == []
