from app.core.logging import REDACTED, redact_sensitive


def run(event: dict[str, object]) -> dict[str, object]:
    return redact_sensitive(None, "info", event)


def test_sensitive_keys_redacted() -> None:
    out = run({"event": "login", "password": "hunter2", "api_key": "x", "user": "a@b.com"})
    assert out["password"] == REDACTED
    assert out["api_key"] == REDACTED
    assert out["user"] == "a@b.com"


def test_nested_structures_redacted() -> None:
    out = run({"event": "req", "headers": {"Authorization": "Bearer abc", "Accept": "json"}})
    assert out["headers"] == {"Authorization": REDACTED, "Accept": "json"}


def test_credential_shaped_values_redacted_under_innocent_keys() -> None:
    out = run(
        {
            "event": "oops sk-ant-api03-ABCDEFGHIJKL leaked",
            "detail": ["tvly-1234567890abc", "Bearer eyJhbGciOi.eyJzdWIi.sig"],
        }
    )
    assert "sk-ant-api03" not in str(out["event"])
    assert out["detail"] == [REDACTED, REDACTED]
