from __future__ import annotations

from asana_org_bridge.logging_config import RedactionHook


def test_redact_string_masks_tokens_emails_and_sensitive_urls() -> None:
    hook = RedactionHook(enabled=True)

    message = (
        "pat=abcDEF1234567890 bearer secret.jwt.token "
        "owner=dev@example.com "
        "https://example.com/callback?token=abc123"
    )
    redacted = hook.redact_string(message)

    assert "abcDEF1234567890" not in redacted
    assert "secret.jwt.token" not in redacted
    assert "dev@example.com" not in redacted
    assert "?token=abc123" not in redacted
    assert "**[REDACTED]**" in redacted
    assert "**[EMAIL]**" in redacted


def test_redact_dict_masks_sensitive_keys_and_nested_values() -> None:
    hook = RedactionHook(enabled=True)

    payload = {
        "api_key": "visible-no-more",
        "nested": {
            "note": "contact qa@example.com",
            "items": [
                "bearer another.token.value",
                {"token_hint": "abc123"},
            ],
        },
    }

    redacted = hook.redact_dict(payload)

    assert redacted["api_key"] == "**[REDACTED]**"
    assert redacted["nested"]["note"] == "contact **[EMAIL]**"
    assert "another.token.value" not in redacted["nested"]["items"][0]
    assert redacted["nested"]["items"][1]["token_hint"] == "**[REDACTED]**"


def test_redaction_hook_processor_redacts_event_and_extra_fields() -> None:
    hook = RedactionHook(enabled=True)
    event_dict = {
        "event": "bearer top.secret.value",
        "level": "info",
        "email": "ops@example.com",
        "request": {
            "secret": "x",
            "url": "https://example.com/path?secret=1",
        },
    }

    redacted = hook(logger=None, method_name="info", event_dict=event_dict)

    assert "top.secret.value" not in redacted["event"]
    assert redacted["email"] == "**[EMAIL]**"
    assert redacted["request"]["secret"] == "**[REDACTED]**"
    assert redacted["request"]["url"].endswith("?**[REDACTED]**")
