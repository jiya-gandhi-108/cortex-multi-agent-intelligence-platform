import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.core.config import Settings
from app.core.security import (
    InvalidTokenError,
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)


def test_password_hash_is_argon2_and_verifies() -> None:
    h = hash_password("correct horse 1")
    assert h.startswith("$argon2id$")
    assert "correct horse" not in h
    assert verify_password("correct horse 1", h)
    assert not verify_password("wrong 1", h)


def test_unknown_user_and_malformed_hash_fail_closed() -> None:
    assert verify_password("anything", None) is False
    assert verify_password("anything", "not-a-hash") is False


def test_token_round_trip(settings: Settings) -> None:
    uid = uuid.uuid4()
    token, ttl = create_access_token(uid, settings)
    assert ttl == settings.access_token_expire_minutes * 60
    assert decode_access_token(token, settings) == uid


def test_expired_token_rejected(settings: Settings) -> None:
    past = datetime.now(UTC) - timedelta(hours=2)
    token = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "type": "access",
            "iat": past,
            "exp": past + timedelta(minutes=1),
        },
        settings.jwt_secret_key.get_secret_value(),
        algorithm="HS256",
    )
    with pytest.raises(InvalidTokenError):
        decode_access_token(token, settings)


def test_token_signed_with_other_key_rejected(settings: Settings) -> None:
    now = datetime.now(UTC)
    token = jwt.encode(
        {"sub": str(uuid.uuid4()), "type": "access", "iat": now, "exp": now + timedelta(hours=1)},
        "a-completely-different-secret-key-0123456789",
        algorithm="HS256",
    )
    with pytest.raises(InvalidTokenError):
        decode_access_token(token, settings)


def test_alg_none_rejected(settings: Settings) -> None:
    now = datetime.now(UTC)
    token = jwt.encode(
        {"sub": str(uuid.uuid4()), "type": "access", "iat": now, "exp": now + timedelta(hours=1)},
        key=None,
        algorithm="none",
    )
    with pytest.raises(InvalidTokenError):
        decode_access_token(token, settings)


def test_wrong_token_type_rejected(settings: Settings) -> None:
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "type": "refresh",
            "iat": now,
            "exp": now + timedelta(hours=1),
            "jti": "x",
        },
        settings.jwt_secret_key.get_secret_value(),
        algorithm="HS256",
    )
    with pytest.raises(InvalidTokenError, match="type"):
        decode_access_token(token, settings)


def test_token_without_jti_rejected(settings: Settings) -> None:
    now = datetime.now(UTC)
    token = jwt.encode(
        {"sub": str(uuid.uuid4()), "type": "access", "iat": now, "exp": now + timedelta(hours=1)},
        settings.jwt_secret_key.get_secret_value(),
        algorithm="HS256",
    )
    with pytest.raises(InvalidTokenError, match="jti"):
        decode_access_token(token, settings)


async def test_revocation_fails_open_without_redis() -> None:
    from app.core.security import is_revoked

    assert await is_revoked(None, "any") is False


def test_llm_trace_processors_expose_token_usage() -> None:
    from decimal import Decimal

    from app.llm.base import ChatMessage, LLMResult, ModelTier, StructuredRequest
    from app.monitoring.cost import TokenUsage
    from app.monitoring.tracing import _llm_inputs, _llm_outputs

    req = StructuredRequest([ChatMessage("user", "hi")], "Schema", {}, ModelTier.FAST, 100)
    assert _llm_inputs({"self": object(), "request": req})["messages"] == [
        {"role": "user", "content": "hi"}
    ]
    out = _llm_outputs(LLMResult("{}", "m", TokenUsage(10, 5, Decimal("0.01")), 12.3))
    assert out["usage_metadata"] == {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
