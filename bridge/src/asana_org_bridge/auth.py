"""Authentication module for Asana Org Bridge.

Supports PAT (Personal Access Token) authentication from environment
or other secure sources (keyring, 1password, pass) via auth-source compatibility.
"""

from __future__ import annotations

import os
import subprocess
from abc import ABC, abstractmethod

from asana_org_bridge.config import AuthConfig, get_settings
from asana_org_bridge.logging_config import get_logger

logger = get_logger(__name__)


def pass_show(path: str) -> str | None:
    """Retrieve a secret from the pass password store.

    Runs ``pass show <path>`` and returns the first line of output,
    which is the secret value.  Returns None if pass is not installed
    or the path does not exist.

    Args:
        path: Path in the pass store (e.g. ``"asana/pat"``).

    Returns:
        The secret string or None on failure.
    """
    try:
        result = subprocess.run(
            ["pass", "show", path],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except FileNotFoundError:
        logger.debug("pass_not_installed")
        return None
    except subprocess.TimeoutExpired:
        logger.debug("pass_timeout", path=path)
        return None

    if result.returncode != 0:
        logger.debug(
            "pass_lookup_failed",
            path=path,
            returncode=result.returncode,
        )
        return None

    first_line = result.stdout.strip().split("\n")[0] if result.stdout else ""
    if not first_line:
        logger.debug("pass_empty_result", path=path)
        return None

    logger.debug("pass_secret_retrieved", path=path)
    return first_line


class AuthSource(ABC):
    """Abstract base class for authentication sources."""

    @abstractmethod
    def get_pat(self) -> str | None:
        """Get Personal Access Token from source.

        Returns:
            PAT string or None if not available
        """
        pass


class EnvAuthSource(AuthSource):
    """Load PAT from environment variable."""

    def get_pat(self) -> str | None:
        """Get PAT from ASANA_PAT environment variable."""
        return os.environ.get("ASANA_PAT")


class KeyringAuthSource(AuthSource):
    """Load PAT from system keyring (future extension)."""

    def get_pat(self) -> str | None:
        """Get PAT from keyring.

        Note: This is a placeholder. Install 'keyring' package and implement
        to enable keyring support.
        """
        logger.debug("Keyring auth source not implemented, skipping")
        return None


class OnePasswordAuthSource(AuthSource):
    """Load PAT from 1Password (future extension)."""

    def get_pat(self) -> str | None:
        """Get PAT from 1Password CLI.

        Note: This is a placeholder. Requires 'op' CLI to be installed
        and configured.
        """
        logger.debug("1Password auth source not implemented, skipping")
        return None


class PassAuthSource(AuthSource):
    """Load PAT from the pass password store."""

    def __init__(self, pass_path: str = "asana/pat") -> None:
        """Initialize pass auth source.

        Args:
            pass_path: Path within the pass store (e.g. ``"asana/pat"``).
        """
        self._pass_path = pass_path

    def get_pat(self) -> str | None:
        """Get PAT from the pass password store.

        Returns:
            PAT string or None if retrieval fails.
        """
        logger.debug("pass_auth_lookup", path=self._pass_path)
        return pass_show(self._pass_path)


class AuthManager:
    """Manages authentication for Asana API."""

    # Auth source implementations
    _SOURCES: dict[str, type[AuthSource]] = {
        "env": EnvAuthSource,
        "keyring": KeyringAuthSource,
        "1password": OnePasswordAuthSource,
        "pass": PassAuthSource,
    }

    def __init__(self, config: AuthConfig | None = None) -> None:
        """Initialize auth manager.

        Args:
            config: Auth configuration (uses global if not provided)
        """
        self._config = config or get_settings().auth

    def get_pat(self) -> str | None:
        """Get the Personal Access Token.

        Returns:
            PAT string or None if not configured
        """
        source_name = self._config.auth_source
        source_class = self._SOURCES.get(source_name)

        if source_class is None:
            logger.warning(
                "unknown_auth_source",
                source=source_name,
                available=list(self._SOURCES.keys()),
            )
            return None

        source = source_class()
        pat = source.get_pat()

        if pat:
            # Log successful retrieval without any token fragment exposure
            logger.info(
                "auth_token_loaded",
                source=source_name,
                token_length=len(pat),
            )
        else:
            logger.warning(
                "no_auth_token",
                source=source_name,
                hint="Set ASANA_PAT environment variable or configure auth_source",
            )

        return pat

    def validate_pat(self, pat: str) -> bool:
        """Validate a PAT format (basic validation).

        Args:
            pat: Personal Access Token to validate

        Returns:
            True if format appears valid
        """
        if not pat:
            return False

        # Asana PATs are typically 16+ characters alphanumeric with underscores
        # This is just a basic format check - actual validation requires API call
        if len(pat) < 16:
            logger.warning("pat_too_short", length=len(pat))
            return False

        if not all(c.isalnum() or c in "_-" for c in pat):
            logger.warning("pat_invalid_characters")
            return False

        return True

    def ensure_authenticated(self) -> str:
        """Ensure valid authentication is available.

        Returns:
            Valid PAT string

        Raises:
            RuntimeError: If no valid PAT is available
        """
        pat = self.get_pat()

        if not pat:
            raise RuntimeError(
                "No authentication token available. "
                "Set ASANA_PAT environment variable or configure auth_source."
            )

        if not self.validate_pat(pat):
            raise RuntimeError(
                "Invalid PAT format. Please check your authentication configuration."
            )

        return pat


# Global auth manager instance
_auth_manager: AuthManager | None = None


def get_auth_manager() -> AuthManager:
    """Get or create global auth manager."""
    global _auth_manager
    if _auth_manager is None:
        _auth_manager = AuthManager()
    return _auth_manager
