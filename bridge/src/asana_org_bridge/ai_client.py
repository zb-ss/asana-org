"""Gemini AI client module for Asana Org Bridge.

Provides AI-powered task summarization via the Google Gemini API.
Falls back to `pass` password store when the API key is not set
as an environment variable.
"""

from __future__ import annotations

import subprocess
from typing import Any

import requests  # type: ignore[import-untyped]

from asana_org_bridge.config import AIConfig, get_settings
from asana_org_bridge.logging_config import get_logger

logger = get_logger(__name__)

GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_TIMEOUT = 30


class GeminiAPIError(Exception):
    """Exception for Gemini API errors."""

    def __init__(self, message: str, status_code: int = 0) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class GeminiClient:
    """Client for Google Gemini generative AI API."""

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-3-flash-preview",
    ) -> None:
        """Initialize the Gemini client.

        Args:
            api_key: Gemini API key
            model: Model name to use for generation
        """
        self._api_key = api_key
        self._model = model

    def summarize(self, prompt: str) -> str:
        """Send a prompt to Gemini and return the generated text.

        Args:
            prompt: Text prompt to send

        Returns:
            Generated text response

        Raises:
            GeminiAPIError: On HTTP errors, missing candidates, or timeouts
        """
        url = f"{GEMINI_API_BASE}/models/{self._model}:generateContent"
        params = {"key": self._api_key}
        body: dict[str, Any] = {
            "contents": [{"parts": [{"text": prompt}]}],
        }
        headers = {"Content-Type": "application/json"}

        try:
            response = requests.post(
                url,
                params=params,
                json=body,
                headers=headers,
                timeout=DEFAULT_TIMEOUT,
            )
        except requests.Timeout as e:
            raise GeminiAPIError(
                "Gemini API request timed out", status_code=0
            ) from e
        except requests.RequestException as e:
            raise GeminiAPIError(
                f"Gemini API request failed: {e}", status_code=0
            ) from e

        if response.status_code == 429:
            raise GeminiAPIError(
                "Gemini API rate limit exceeded", status_code=429
            )

        if response.status_code >= 400:
            error_detail = ""
            try:
                error_json = response.json()
                error_detail = str(
                    error_json.get("error", {}).get("message", "")
                )
            except (ValueError, KeyError):
                error_detail = response.text[:256] if response.text else ""
            raise GeminiAPIError(
                f"Gemini API error (HTTP {response.status_code}): {error_detail}",
                status_code=response.status_code,
            )

        try:
            response_json = response.json()
        except ValueError as e:
            raise GeminiAPIError(
                "Failed to parse Gemini API response as JSON"
            ) from e

        candidates = response_json.get("candidates")
        if not candidates:
            raise GeminiAPIError("Gemini API returned no candidates")

        try:
            text = candidates[0]["content"]["parts"][0]["text"]
        except (IndexError, KeyError, TypeError) as e:
            raise GeminiAPIError(
                "Unexpected Gemini API response structure"
            ) from e

        return str(text)


def get_api_key(config: AIConfig) -> str:
    """Retrieve the Gemini API key from config or pass store.

    Tries in order:
    1. Direct env var via config.api_key (ASANA_ORG_AI_API_KEY)
    2. `pass show <config.api_key_pass_path>` via subprocess

    Args:
        config: AI configuration

    Returns:
        API key string

    Raises:
        RuntimeError: If no API key can be retrieved
    """
    if config.api_key:
        logger.info("ai_api_key_source", source="env")
        return config.api_key

    try:
        result = subprocess.run(
            ["pass", "show", config.api_key_pass_path],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            key = result.stdout.strip().splitlines()[0]
            logger.info("ai_api_key_source", source="pass")
            return key
    except FileNotFoundError:
        logger.debug("pass_not_found", hint="pass binary not in PATH")
    except subprocess.TimeoutExpired:
        logger.warning("pass_timeout", path=config.api_key_pass_path)
    except Exception as e:
        logger.warning("pass_failed", error=str(e))

    raise RuntimeError(
        "No Gemini API key available. "
        "Set ASANA_ORG_AI_API_KEY env var or configure pass store at "
        f"'{config.api_key_pass_path}'."
    )


def create_gemini_client(config: AIConfig | None = None) -> GeminiClient:
    """Factory function to create a configured GeminiClient.

    Args:
        config: AI configuration (uses global settings if not provided)

    Returns:
        Configured GeminiClient instance

    Raises:
        RuntimeError: If no API key is available
    """
    if config is None:
        config = get_settings().ai
    api_key = get_api_key(config)
    return GeminiClient(api_key=api_key, model=config.model)
