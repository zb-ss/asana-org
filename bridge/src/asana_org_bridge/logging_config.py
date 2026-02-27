"""Logging module for Asana Org Bridge with structured JSON output and redaction."""

from __future__ import annotations

import logging
import re
import sys
from typing import Any, cast

import structlog
from structlog.types import EventDict, FilteringBoundLogger, Processor

from asana_org_bridge.config import get_settings

# Default redaction patterns
REDACT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # PAT tokens (various formats)
    (
        re.compile(
            r"(?i)(pat|token|access_token|api_key)[\"']?\s*[:=]\s*[\"']?([a-zA-Z0-9_\-]{10,})[\"']?"
        ),
        r"\1=**[REDACTED]**",
    ),
    # Bearer tokens
    (re.compile(r"(?i)bearer\s+([a-zA-Z0-9_\-\.]+)"), "bearer **[REDACTED]**"),
    # Email addresses
    (re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"), "**[EMAIL]**"),
    # URLs with query params containing sensitive data
    (
        re.compile(
            r"(https?://[^\s\"\'<>]+)(\?[^#\s\"\'<>]*secret[^#\s\"\'<>]*)",
            re.IGNORECASE,
        ),
        r"\1?**[REDACTED]**",
    ),
    (
        re.compile(
            r"(https?://[^\s\"\'<>]+)(\?[^#\s\"\'<>]*token[^#\s\"\'<>]*)", re.IGNORECASE
        ),
        r"\1?**[REDACTED]**",
    ),
]


class RedactionHook:
    """Hook for redacting sensitive data from logs."""

    def __init__(
        self,
        enabled: bool = True,
        custom_patterns: list[tuple[str, str]] | None = None,
    ) -> None:
        """Initialize redaction hook.

        Args:
            enabled: Whether redaction is enabled
            custom_patterns: Additional (pattern_str, replacement) tuples
        """
        self.enabled = enabled
        self._patterns = list(REDACT_PATTERNS)
        if custom_patterns:
            for pattern_str, replacement in custom_patterns:
                self._patterns.append((re.compile(pattern_str), replacement))

    def redact_string(self, value: str) -> str:
        """Redact sensitive data from a string.

        Args:
            value: String to redact

        Returns:
            Redacted string
        """
        if not self.enabled:
            return value

        result = value
        for pattern, replacement in self._patterns:
            result = pattern.sub(replacement, result)
        return result

    def redact_dict(self, data: dict[str, Any]) -> dict[str, Any]:
        """Recursively redact sensitive data from a dictionary.

        Args:
            data: Dictionary to redact

        Returns:
            Redacted dictionary
        """
        if not self.enabled:
            return data

        redacted: dict[str, Any] = {}

        for key, value in data.items():
            # Redact key if it looks sensitive
            key_lower = key.lower()
            if any(
                sensitive in key_lower
                for sensitive in ["password", "secret", "token", "key", "auth"]
            ):
                redacted[key] = "**[REDACTED]**"
            elif isinstance(value, str):
                redacted[key] = self.redact_string(value)
            elif isinstance(value, dict):
                redacted[key] = self.redact_dict(value)
            elif isinstance(value, list):
                redacted[key] = [
                    self.redact_dict(item)
                    if isinstance(item, dict)
                    else self.redact_string(item)
                    if isinstance(item, str)
                    else item
                    for item in value
                ]
            else:
                redacted[key] = value

        return redacted

    def __call__(
        self, logger: Any, method_name: str, event_dict: EventDict
    ) -> EventDict:
        """Structlog processor to redact sensitive data.

        Args:
            logger: Logger instance
            method_name: Log method name
            event_dict: Event data dictionary

        Returns:
            Redacted event dictionary
        """
        if not self.enabled:
            return event_dict

        # Redact the message
        if "event" in event_dict and isinstance(event_dict["event"], str):
            event_dict["event"] = self.redact_string(event_dict["event"])

        # Redact extra fields
        for key in list(event_dict.keys()):
            if key not in ("event", "level", "timestamp", "logger"):
                value = event_dict[key]
                if isinstance(value, str):
                    event_dict[key] = self.redact_string(value)
                elif isinstance(value, dict):
                    event_dict[key] = self.redact_dict(value)

        return event_dict


# Global redaction hook instance
_redaction_hook: RedactionHook | None = None


def get_redaction_hook() -> RedactionHook:
    """Get or create global redaction hook."""
    global _redaction_hook
    if _redaction_hook is None:
        settings = get_settings()
        _redaction_hook = RedactionHook(
            enabled=settings.logging.redact_pii,
        )
    return _redaction_hook


def configure_logging(
    level: str | None = None,
    json_format: bool | None = None,
) -> None:
    """Configure structured logging.

    Args:
        level: Log level override
        json_format: Use JSON format override
    """
    settings = get_settings()

    log_level = level or settings.logging.level
    use_json = json_format if json_format is not None else settings.logging.format_json

    # Configure standard library logging
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, log_level.upper()),
    )

    # Configure structlog
    processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
    ]

    if use_json:
        processors.extend(
            [
                get_redaction_hook(),
                structlog.processors.JSONRenderer(),
            ]
        )
    else:
        processors.extend(
            [
                structlog.dev.ConsoleRenderer(),
            ]
        )

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, log_level.upper())
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> FilteringBoundLogger:
    """Get a structured logger.

    Args:
        name: Logger name (typically __name__)

    Returns:
        Configured structlog logger
    """
    configure_logging()
    return cast(FilteringBoundLogger, structlog.get_logger(name))
