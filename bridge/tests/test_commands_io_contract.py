from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

import asana_org_bridge.commands as commands_module
from asana_org_bridge.commands import app

runner = CliRunner()


def test_move_task_requires_cli_to_argument_even_with_stdin_json() -> None:
    result = runner.invoke(
        app,
        ["move-task", "1234567890", "--json"],
        input=json.dumps(
            {
                "version": "1",
                "command": "move-task",
                "payload": {
                    "to_list": "In Progress",
                },
            }
        ),
    )

    assert result.exit_code == 1
    envelope = json.loads(result.stdout)
    assert envelope["command"] == "move-task"
    assert envelope["error"]["code"] == "INVALID_REQUEST"
    assert "--to" in envelope["error"]["message"]


def test_comment_append_requires_cli_body_argument_even_with_stdin_json() -> None:
    result = runner.invoke(
        app,
        ["comment-append", "1234567890", "--json"],
        input=json.dumps(
            {
                "version": "1",
                "command": "comment-append",
                "payload": {
                    "text": "Body from stdin should be ignored",
                },
            }
        ),
    )

    assert result.exit_code == 1
    envelope = json.loads(result.stdout)
    assert envelope["command"] == "comment-append"
    assert envelope["error"]["code"] == "INVALID_REQUEST"
    assert "--body" in envelope["error"]["message"]


def test_sync_apply_json_fallback_uses_data_results_and_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeApplyResult:
        def __init__(self) -> None:
            self.results_json: dict[str, object] = {}
            self.errors: list[str] = []

    class FakeEngine:
        def apply(
            self,
            dry_run: bool = False,
            mutations_json: dict[str, object] | None = None,
        ) -> FakeApplyResult:  # noqa: ARG002
            return FakeApplyResult()

    monkeypatch.setattr(commands_module, "get_sync_engine", lambda: FakeEngine())

    result = runner.invoke(
        app,
        ["sync-apply", "--json", "-"],
        input=json.dumps({"version": "1", "command": "sync-apply", "mutations": []}),
    )

    assert result.exit_code == 0
    envelope = json.loads(result.stdout)
    assert envelope["command"] == "sync-apply"
    assert envelope["status"] == "success"
    assert envelope["data"]["results"] == []
    assert envelope["data"]["summary"] == {"total": 0, "applied": 0, "failed": 0}
