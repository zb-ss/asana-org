"""Configuration module for Asana Org Bridge."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AuthConfig(BaseSettings):
    """Authentication configuration - loads from env/auth-source."""

    model_config = SettingsConfigDict(
        env_prefix="ASANA_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    pat: str | None = Field(
        default=None,
        description="Personal Access Token for Asana API",
    )

    # Auth source compatible placeholder - can be extended to support
    # keyring, 1password CLI, or other secure storage backends
    auth_source: str = Field(
        default="env",
        description="Auth source: env, keyring, or 1password",
    )


class DatabaseConfig(BaseSettings):
    """Database configuration."""

    model_config = SettingsConfigDict(
        env_prefix="ASANA_ORG_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    db_path: Path = Field(
        default=Path.home() / ".local" / "share" / "asana-org" / "bridge.db",
        description="Path to SQLite database",
    )

    echo_sql: bool = Field(
        default=False,
        description="Echo SQL statements for debugging",
    )


class SyncConfig(BaseSettings):
    """Sync configuration."""

    model_config = SettingsConfigDict(
        env_prefix="ASANA_ORG_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Retention days
    snapshot_retention_days: int = Field(
        default=30,
        description="Days to keep task snapshots",
    )

    journal_retention_days: int = Field(
        default=90,
        description="Days to keep sync journal metadata",
    )

    audit_retention_days: int = Field(
        default=180,
        description="Days to keep mutation audit records",
    )

    # Sync defaults
    batch_size: int = Field(
        default=20,
        description="Default batch size for mutations",
    )

    confirmation_threshold: int = Field(
        default=5,
        description="Mutations requiring confirmation",
    )

    # Workspace GID (required for "My Tasks" pull)
    workspace_gid: str | None = Field(
        default=None,
        description="Asana workspace GID (required for pulling My Tasks)",
    )

    # Safety cap on write operations per invocation
    max_writes: int = Field(
        default=60,
        description="Maximum write operations per command invocation (0 = unlimited)",
    )

    # Mock mode for testing
    mock_data: bool = Field(
        default=False,
        description="Use deterministic mock data instead of API calls",
    )


class LoggingConfig(BaseSettings):
    """Logging configuration."""

    model_config = SettingsConfigDict(
        env_prefix="ASANA_ORG_LOG_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    level: str = Field(
        default="INFO",
        description="Log level: DEBUG, INFO, WARNING, ERROR",
    )

    format_json: bool = Field(
        default=True,
        description="Use JSON structured logging",
    )

    redact_pii: bool = Field(
        default=True,
        description="Redact PII from logs",
    )


class Settings(BaseSettings):
    """Main application settings composing all config sections."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    auth: AuthConfig = Field(default_factory=AuthConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    sync: SyncConfig = Field(default_factory=SyncConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)


# Global settings instance
_settings: Settings | None = None


def get_settings() -> Settings:
    """Get or create global settings instance."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reload_settings() -> Settings:
    """Force reload settings (useful for testing)."""
    global _settings
    _settings = Settings()
    return _settings
