"""Test fixtures and configuration."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest


@pytest.fixture
def sample_task_data() -> dict[str, object]:
    """Sample task data for testing."""
    return {
        "gid": "test_task_001",
        "name": "Test Task",
        "completed": False,
        "permalink_url": "https://app.asana.com/0/0/test_task_001",
        "modified_at": datetime.now(UTC).isoformat(),
        "due_on": "2026-02-28",
        "start_on": "2026-02-25",
        "notes": "Test task notes",
        "memberships": [
            {
                "project": {"gid": "proj_001", "name": "Test Project"},
                "section": {"gid": "sect_001", "name": "To Do"},
            }
        ],
    }


@pytest.fixture
def mock_env_with_pat(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set up mock environment with PAT."""
    monkeypatch.setenv("ASANA_PAT", "test_pat_token_12345")
