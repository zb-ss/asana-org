"""Tests for pass auth source and AIConfig."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Generator
from unittest.mock import patch

import pytest

import asana_org_bridge.config as config_module
from asana_org_bridge.auth import AuthManager, PassAuthSource, pass_show
from asana_org_bridge.config import AIConfig, AuthConfig


@pytest.fixture(autouse=True)
def clean_settings() -> Generator[None, None, None]:
    """Clean global settings between tests."""
    config_module._settings = None
    yield
    config_module._settings = None


# --- pass_show() tests ---


def test_pass_show_success() -> None:
    """Test pass_show returns first line of pass output."""
    mock_result = subprocess.CompletedProcess(
        args=["pass", "show", "asana/pat"],
        returncode=0,
        stdout="my_secret_token\nsome metadata\n",
        stderr="",
    )
    with patch("asana_org_bridge.auth.subprocess.run", return_value=mock_result):
        result = pass_show("asana/pat")
    assert result == "my_secret_token"


def test_pass_show_not_installed() -> None:
    """Test pass_show returns None when pass is not installed."""
    with patch(
        "asana_org_bridge.auth.subprocess.run",
        side_effect=FileNotFoundError("No such file or directory: 'pass'"),
    ):
        result = pass_show("asana/pat")
    assert result is None


def test_pass_show_key_not_found() -> None:
    """Test pass_show returns None on non-zero exit code."""
    mock_result = subprocess.CompletedProcess(
        args=["pass", "show", "nonexistent/key"],
        returncode=1,
        stdout="",
        stderr="Error: nonexistent/key is not in the password store.",
    )
    with patch("asana_org_bridge.auth.subprocess.run", return_value=mock_result):
        result = pass_show("nonexistent/key")
    assert result is None


def test_pass_show_timeout() -> None:
    """Test pass_show returns None on timeout."""
    with patch(
        "asana_org_bridge.auth.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="pass", timeout=10),
    ):
        result = pass_show("asana/pat")
    assert result is None


def test_pass_show_empty_output() -> None:
    """Test pass_show returns None when output is empty."""
    mock_result = subprocess.CompletedProcess(
        args=["pass", "show", "empty/key"],
        returncode=0,
        stdout="",
        stderr="",
    )
    with patch("asana_org_bridge.auth.subprocess.run", return_value=mock_result):
        result = pass_show("empty/key")
    assert result is None


# --- PassAuthSource tests ---


def test_pass_auth_source_get_pat() -> None:
    """Test PassAuthSource.get_pat returns first line of pass output."""
    mock_result = subprocess.CompletedProcess(
        args=["pass", "show", "asana/pat"],
        returncode=0,
        stdout="secret_pat_value_1234567890\n",
        stderr="",
    )
    with patch("asana_org_bridge.auth.subprocess.run", return_value=mock_result):
        source = PassAuthSource(pass_path="asana/pat")
        pat = source.get_pat()
    assert pat == "secret_pat_value_1234567890"


def test_pass_auth_source_default_path() -> None:
    """Test PassAuthSource uses default pass_path."""
    source = PassAuthSource()
    assert source._pass_path == "asana/pat"


def test_pass_auth_source_custom_path() -> None:
    """Test PassAuthSource accepts custom pass_path."""
    source = PassAuthSource(pass_path="work/asana/token")
    assert source._pass_path == "work/asana/token"


def test_pass_auth_source_registered() -> None:
    """Test PassAuthSource is registered in AuthManager._SOURCES."""
    assert "pass" in AuthManager._SOURCES
    assert AuthManager._SOURCES["pass"] is PassAuthSource


# --- AIConfig tests ---


def test_ai_config_defaults() -> None:
    """Test AIConfig has correct defaults."""
    config = AIConfig()
    assert config.enabled is False
    assert config.model == "gemini-3-flash-preview"
    assert config.api_key_pass_path == "api.gemini.ai/z-first-key"
    assert config.api_key is None


def test_ai_config_from_env() -> None:
    """Test AIConfig reads from environment variables."""
    with patch.dict(
        os.environ,
        {
            "ASANA_ORG_AI_ENABLED": "true",
            "ASANA_ORG_AI_MODEL": "gpt-4",
            "ASANA_ORG_AI_API_KEY": "sk-test-key-123",
            "ASANA_ORG_AI_API_KEY_PASS_PATH": "custom/ai/key",
        },
    ):
        config = AIConfig()
    assert config.enabled is True
    assert config.model == "gpt-4"
    assert config.api_key == "sk-test-key-123"
    assert config.api_key_pass_path == "custom/ai/key"


def test_ai_config_in_settings() -> None:
    """Test AIConfig is composed into Settings."""
    from asana_org_bridge.config import Settings

    settings = Settings()
    assert isinstance(settings.ai, AIConfig)
    assert settings.ai.enabled is False


# --- AuthConfig pass_path field ---


def test_auth_config_pass_path_default() -> None:
    """Test AuthConfig.pass_path defaults to None."""
    config = AuthConfig()
    assert config.pass_path is None


def test_auth_config_pass_path_from_env() -> None:
    """Test AuthConfig.pass_path loads from environment."""
    with patch.dict(os.environ, {"ASANA_PASS_PATH": "custom/asana/pat"}):
        config = AuthConfig()
    assert config.pass_path == "custom/asana/pat"
