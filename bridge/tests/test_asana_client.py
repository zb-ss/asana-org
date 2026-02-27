from __future__ import annotations

from typing import Any, cast

import pytest

from asana_org_bridge.asana_client import (
    MAX_RETRY_AFTER_SECONDS,
    AsanaAPIError,
    AsanaClient,
    RateLimitError,
)


class FakeResponse:
    def __init__(
        self,
        status_code: int,
        headers: dict[str, str] | None = None,
        json_data: Any = None,
        json_error: Exception | None = None,
        text: str = "",
    ) -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self._json_data = json_data
        self._json_error = json_error
        self.text = text

    def json(self) -> Any:
        if self._json_error:
            raise self._json_error
        return self._json_data


def test_request_raises_structured_error_for_non_json_body() -> None:
    client = AsanaClient(pat="pat", max_retries=1)
    cast(Any, client._session).request = lambda **_: FakeResponse(
        status_code=500,
        json_error=ValueError("invalid json"),
        text="upstream proxy error",
    )

    with pytest.raises(AsanaAPIError) as exc:
        client._request("GET", "/tasks")

    assert exc.value.status_code == 500
    assert exc.value.code == "HTTP_ERROR"
    assert "upstream proxy error" in exc.value.message


def test_request_raises_structured_error_for_json_without_errors_array() -> None:
    client = AsanaClient(pat="pat", max_retries=1)
    cast(Any, client._session).request = lambda **_: FakeResponse(
        status_code=400,
        json_data={"message": "bad request"},
    )

    with pytest.raises(AsanaAPIError) as exc:
        client._request("GET", "/tasks")

    assert exc.value.status_code == 400
    assert exc.value.code == "HTTP_ERROR"
    assert "HTTP 400 error" in exc.value.message


def test_parse_retry_after_uses_fallback_and_clamp() -> None:
    assert AsanaClient._parse_retry_after(None) == 60
    assert AsanaClient._parse_retry_after("invalid") == 60
    assert AsanaClient._parse_retry_after("-1") == 1
    assert AsanaClient._parse_retry_after("99999") == MAX_RETRY_AFTER_SECONDS


def test_rate_limit_error_uses_safe_retry_after_fallback() -> None:
    client = AsanaClient(pat="pat", max_retries=1)
    cast(Any, client._session).request = lambda **_: FakeResponse(
        status_code=429,
        headers={"Retry-After": "not-a-number"},
    )

    with pytest.raises(RateLimitError) as exc:
        client._request("GET", "/tasks")

    assert exc.value.retry_after == 60
