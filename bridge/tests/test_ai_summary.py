"""Tests for AI summary feature (ai_client, sync.ai_summary, CLI command)."""

from __future__ import annotations

import json
import subprocess
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest

from asana_org_bridge.ai_client import (
    GeminiAPIError,
    GeminiClient,
    get_api_key,
)
from asana_org_bridge.config import AIConfig


# ---------------------------------------------------------------------------
# GeminiClient tests
# ---------------------------------------------------------------------------


class FakeResponse:
    """Minimal requests.Response stand-in."""

    def __init__(
        self,
        status_code: int = 200,
        json_data: Any = None,
        text: str = "",
    ) -> None:
        self.status_code = status_code
        self._json_data = json_data
        self.text = text

    def json(self) -> Any:
        if self._json_data is None:
            raise ValueError("No JSON")
        return self._json_data


class TestGeminiClientSummarize:
    """Tests for GeminiClient.summarize()."""

    def test_success(self) -> None:
        """Successful Gemini API call returns generated text."""
        client = GeminiClient(api_key="test-key", model="test-model")
        fake_resp = FakeResponse(
            status_code=200,
            json_data={
                "candidates": [
                    {
                        "content": {
                            "parts": [{"text": "Here is the summary."}]
                        }
                    }
                ]
            },
        )

        with patch("asana_org_bridge.ai_client.requests.post", return_value=fake_resp):
            result = client.summarize("Summarize these tasks")

        assert result == "Here is the summary."

    def test_http_error(self) -> None:
        """Non-2xx status raises GeminiAPIError."""
        client = GeminiClient(api_key="test-key")
        fake_resp = FakeResponse(
            status_code=500,
            json_data={"error": {"message": "Internal error"}},
        )

        with patch("asana_org_bridge.ai_client.requests.post", return_value=fake_resp):
            with pytest.raises(GeminiAPIError) as exc:
                client.summarize("prompt")

        assert exc.value.status_code == 500
        assert "Internal error" in exc.value.message

    def test_rate_limit(self) -> None:
        """429 status raises GeminiAPIError with rate limit message."""
        client = GeminiClient(api_key="test-key")
        fake_resp = FakeResponse(
            status_code=429,
            json_data={},
        )

        with patch("asana_org_bridge.ai_client.requests.post", return_value=fake_resp):
            with pytest.raises(GeminiAPIError) as exc:
                client.summarize("prompt")

        assert exc.value.status_code == 429
        assert "rate limit" in exc.value.message.lower()

    def test_no_candidates(self) -> None:
        """Empty candidates list raises GeminiAPIError."""
        client = GeminiClient(api_key="test-key")
        fake_resp = FakeResponse(
            status_code=200,
            json_data={"candidates": []},
        )

        with patch("asana_org_bridge.ai_client.requests.post", return_value=fake_resp):
            with pytest.raises(GeminiAPIError, match="no candidates"):
                client.summarize("prompt")

    def test_malformed_response(self) -> None:
        """Missing content parts raises GeminiAPIError."""
        client = GeminiClient(api_key="test-key")
        fake_resp = FakeResponse(
            status_code=200,
            json_data={"candidates": [{"content": {}}]},
        )

        with patch("asana_org_bridge.ai_client.requests.post", return_value=fake_resp):
            with pytest.raises(GeminiAPIError, match="Unexpected.*response structure"):
                client.summarize("prompt")


# ---------------------------------------------------------------------------
# get_api_key tests
# ---------------------------------------------------------------------------


class TestGetApiKey:
    """Tests for get_api_key()."""

    def test_from_env_var(self) -> None:
        """API key loaded from config.api_key (env var)."""
        config = AIConfig(api_key="env-key-value")
        key = get_api_key(config)
        assert key == "env-key-value"

    def test_from_pass_store(self) -> None:
        """API key loaded from pass store when env var is not set."""
        config = AIConfig(api_key=None, api_key_pass_path="test/path")

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "pass-key-value\nmetadata line\n"

        with patch("asana_org_bridge.ai_client.subprocess.run", return_value=mock_result):
            key = get_api_key(config)

        assert key == "pass-key-value"

    def test_no_key_available_raises(self) -> None:
        """RuntimeError raised when neither env nor pass has the key."""
        config = AIConfig(api_key=None)

        with patch(
            "asana_org_bridge.ai_client.subprocess.run",
            side_effect=FileNotFoundError("pass not found"),
        ):
            with pytest.raises(RuntimeError, match="No Gemini API key"):
                get_api_key(config)

    def test_pass_timeout(self) -> None:
        """RuntimeError raised when pass command times out."""
        config = AIConfig(api_key=None)

        with patch(
            "asana_org_bridge.ai_client.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="pass", timeout=10),
        ):
            with pytest.raises(RuntimeError, match="No Gemini API key"):
                get_api_key(config)


# ---------------------------------------------------------------------------
# SyncEngine.ai_summary tests
# ---------------------------------------------------------------------------


class TestSyncEngineAiSummaryMock:
    """Tests for SyncEngine.ai_summary() in mock mode."""

    def test_mock_mode_returns_deterministic_summary(self) -> None:
        """Mock mode returns summary without calling the API."""
        from asana_org_bridge.sync import SyncEngine

        db = MagicMock()
        auth = MagicMock()
        engine = SyncEngine(db=db, auth_manager=auth, use_mock=True)

        result = engine.ai_summary(task_gids=["task_001", "task_002"])

        assert result["task_count"] == 2
        assert result["model"] == "mock"
        assert "Mock AI Summary" in result["summary"]
        assert "2 tasks" in result["summary"]

    def test_mock_mode_no_api_call(self) -> None:
        """Mock mode does not instantiate GeminiClient."""
        from asana_org_bridge.sync import SyncEngine

        db = MagicMock()
        auth = MagicMock()
        engine = SyncEngine(db=db, auth_manager=auth, use_mock=True)

        with patch("asana_org_bridge.ai_client.create_gemini_client") as mock_create:
            engine.ai_summary(task_gids=["task_001"])
            mock_create.assert_not_called()


class TestSyncEngineAiSummaryLive:
    """Tests for SyncEngine.ai_summary() with mocked Gemini client."""

    def test_live_mode_calls_gemini(self) -> None:
        """Live mode builds prompt from snapshots and calls Gemini."""
        from asana_org_bridge.models import TaskSnapshot
        from asana_org_bridge.sync import SyncEngine
        from datetime import UTC, datetime

        # Mock database session with a snapshot
        mock_snapshot = MagicMock(spec=TaskSnapshot)
        mock_snapshot.name = "Test task"
        mock_snapshot.completed = False
        mock_snapshot.due_on = "2026-03-15"
        mock_snapshot.start_on = "2026-03-10"
        mock_snapshot.notes = "Some notes"
        mock_snapshot.stories_json = None

        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.first.return_value = mock_snapshot

        mock_session = MagicMock()
        mock_session.query.return_value = mock_query
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)

        mock_db = MagicMock()
        mock_db.session.return_value = mock_session

        mock_auth = MagicMock()
        mock_auth.get_pat.return_value = "test-pat"

        engine = SyncEngine(db=mock_db, auth_manager=mock_auth, use_mock=False)

        mock_gemini = MagicMock()
        mock_gemini.summarize.return_value = "AI generated summary text"

        with patch(
            "asana_org_bridge.ai_client.create_gemini_client",
            return_value=mock_gemini,
        ):
            with patch("asana_org_bridge.sync.get_settings") as mock_settings:
                mock_settings.return_value.ai.model = "test-model"
                result = engine.ai_summary(
                    task_gids=["task_001"],
                    include_notes=True,
                )

        assert result["summary"] == "AI generated summary text"
        assert result["task_count"] == 1
        assert result["model"] == "test-model"
        mock_gemini.summarize.assert_called_once()
        prompt = mock_gemini.summarize.call_args[0][0]
        assert "Test task" in prompt
        assert "Some notes" in prompt


# ---------------------------------------------------------------------------
# CLI command tests
# ---------------------------------------------------------------------------


class TestAiSummaryCLI:
    """Tests for the ai-summary CLI command."""

    def test_json_output_format_mock(self) -> None:
        """CLI outputs correct JSON envelope in mock mode."""
        from typer.testing import CliRunner
        from asana_org_bridge.commands import app

        runner = CliRunner()

        with patch.dict(
            "os.environ",
            {"ASANA_ORG_MOCK_DATA": "true"},
            clear=False,
        ):
            # Reset cached settings
            import asana_org_bridge.config as cfg
            old = cfg._settings
            cfg._settings = None
            try:
                result = runner.invoke(app, ["ai-summary", "task_001", "--json"])
            finally:
                cfg._settings = old

        assert result.exit_code == 0, result.output
        envelope = json.loads(result.output)
        assert envelope["version"] == "1"
        assert envelope["command"] == "ai-summary"
        assert envelope["status"] == "success"
        assert "summary" in envelope["data"]
        assert envelope["data"]["task_count"] == 1
        assert envelope["data"]["model"] == "mock"

    def test_json_output_multiple_tasks(self) -> None:
        """CLI handles multiple task GIDs."""
        from typer.testing import CliRunner
        from asana_org_bridge.commands import app

        runner = CliRunner()

        with patch.dict(
            "os.environ",
            {"ASANA_ORG_MOCK_DATA": "true"},
            clear=False,
        ):
            import asana_org_bridge.config as cfg
            old = cfg._settings
            cfg._settings = None
            try:
                result = runner.invoke(
                    app,
                    ["ai-summary", "task_001", "task_002", "task_003", "--json"],
                )
            finally:
                cfg._settings = old

        assert result.exit_code == 0, result.output
        envelope = json.loads(result.output)
        assert envelope["data"]["task_count"] == 3

    def test_rich_output_mock(self) -> None:
        """CLI produces readable rich output without --json."""
        from typer.testing import CliRunner
        from asana_org_bridge.commands import app

        runner = CliRunner()

        with patch.dict(
            "os.environ",
            {"ASANA_ORG_MOCK_DATA": "true"},
            clear=False,
        ):
            import asana_org_bridge.config as cfg
            old = cfg._settings
            cfg._settings = None
            try:
                result = runner.invoke(app, ["ai-summary", "task_001"])
            finally:
                cfg._settings = old

        assert result.exit_code == 0, result.output
        assert "AI Task Summary" in result.output or "Mock AI Summary" in result.output
