"""Tests for configuration loading."""

from __future__ import annotations

import os
from collections.abc import Generator
from pathlib import Path
from unittest.mock import patch

import pytest

import asana_org_bridge.config as config_module
from asana_org_bridge.config import (
    AuthConfig,
    DatabaseConfig,
    LoggingConfig,
    Settings,
    SyncConfig,
    get_settings,
    reload_settings,
)


@pytest.fixture(autouse=True)
def clean_settings() -> Generator[None, None, None]:
    """Clean global settings between tests."""
    config_module._settings = None
    yield
    config_module._settings = None


def test_auth_config_from_env() -> None:
    """Test auth config loads from environment."""
    with patch.dict(os.environ, {"ASANA_PAT": "test_pat_12345"}):
        config = AuthConfig()
        assert config.pat == "test_pat_12345"
        assert config.auth_source == "env"


def test_auth_config_defaults() -> None:
    """Test auth config has correct defaults."""
    config = AuthConfig()
    assert config.pat is None
    assert config.auth_source == "env"


def test_database_config_defaults() -> None:
    """Test database config has correct defaults."""
    config = DatabaseConfig()
    assert (
        config.db_path == Path.home() / ".local" / "share" / "asana-org" / "bridge.db"
    )
    assert config.echo_sql is False


def test_database_config_from_env() -> None:
    """Test database config loads from environment."""
    with patch.dict(os.environ, {"ASANA_ORG_DB_PATH": "/custom/path/db.sqlite"}):
        config = DatabaseConfig()
        assert config.db_path == Path("/custom/path/db.sqlite")


def test_sync_config_defaults() -> None:
    """Test sync config has correct defaults."""
    config = SyncConfig()
    assert config.snapshot_retention_days == 30
    assert config.journal_retention_days == 90
    assert config.audit_retention_days == 180
    assert config.batch_size == 20
    assert config.confirmation_threshold == 5
    assert config.mock_data is False


def test_sync_config_from_env() -> None:
    """Test sync config loads from environment."""
    with patch.dict(
        os.environ,
        {
            "ASANA_ORG_SNAPSHOT_RETENTION_DAYS": "60",
            "ASANA_ORG_MOCK_DATA": "true",
        },
    ):
        config = SyncConfig()
        assert config.snapshot_retention_days == 60
        assert config.mock_data is True


def test_logging_config_defaults() -> None:
    """Test logging config has correct defaults."""
    config = LoggingConfig()
    assert config.level == "INFO"
    assert config.format_json is True
    assert config.redact_pii is True


def test_logging_config_from_env() -> None:
    """Test logging config loads from environment."""
    with patch.dict(
        os.environ,
        {
            "ASANA_ORG_LOG_LEVEL": "DEBUG",
            "ASANA_ORG_LOG_FORMAT_JSON": "false",
        },
    ):
        config = LoggingConfig()
        assert config.level == "DEBUG"
        assert config.format_json is False


def test_settings_composes_configs() -> None:
    """Test Settings composes all config sections."""
    settings = Settings()
    assert isinstance(settings.auth, AuthConfig)
    assert isinstance(settings.database, DatabaseConfig)
    assert isinstance(settings.sync, SyncConfig)
    assert isinstance(settings.logging, LoggingConfig)


def test_get_settings_singleton() -> None:
    """Test get_settings returns singleton."""
    settings1 = get_settings()
    settings2 = get_settings()
    assert settings1 is settings2


def test_reload_settings() -> None:
    """Test reload_settings creates new instance."""
    settings1 = get_settings()
    settings2 = reload_settings()
    assert settings1 is not settings2


def test_pat_validation_valid() -> None:
    """Test PAT validation with valid token."""
    from asana_org_bridge.auth import AuthManager

    with patch.dict(os.environ, {"ASANA_PAT": "valid_pat_token_12345"}):
        manager = AuthManager()
        assert manager.validate_pat("valid_pat_token_12345") is True


def test_pat_validation_too_short() -> None:
    """Test PAT validation rejects short tokens."""
    from asana_org_bridge.auth import AuthManager

    manager = AuthManager()
    assert manager.validate_pat("short") is False


def test_pat_validation_invalid_chars() -> None:
    """Test PAT validation rejects invalid characters."""
    from asana_org_bridge.auth import AuthManager

    manager = AuthManager()
    assert manager.validate_pat("invalid@token!") is False


def test_pat_validation_empty() -> None:
    """Test PAT validation rejects empty tokens."""
    from asana_org_bridge.auth import AuthManager

    manager = AuthManager()
    assert manager.validate_pat("") is False
    assert manager.validate_pat(None) is False  # type: ignore[arg-type]
