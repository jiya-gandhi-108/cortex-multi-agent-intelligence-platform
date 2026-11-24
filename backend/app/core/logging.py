"""Structured logging with secret redaction.

Every log event passes through ``redact_sensitive`` before rendering, so a
stray ``password=`` or ``Authorization`` header never reaches the log sink.
"""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import Mapping, MutableMapping
from typing import Any

import structlog

_SENSITIVE_KEYS = re.compile(
    r"(pass(word)?|secret|token|api[_-]?key|authorization|cookie|credential|hashed_password)",
    re.IGNORECASE,
)
# Values that look like credentials even under an innocuous key.
_SENSITIVE_VALUES = re.compile(
    r"(sk-ant-[A-Za-z0-9_\-]{8,}|gsk_[A-Za-z0-9]{8,}|tvly-[A-Za-z0-9_\-]{8,}|lsv2_[A-Za-z0-9_\-]{8,}"
    r"|AIza[0-9A-Za-z_\-]{20,}|Bearer\s+[A-Za-z0-9._\-]+|eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+)"
)
REDACTED = "[REDACTED]"


def _is_sensitive_key(key: object) -> bool:
    return isinstance(key, str) and bool(_SENSITIVE_KEYS.search(key))


def _scrub(value: Any, depth: int = 0) -> Any:
    if depth > 6:
        return value
    if isinstance(value, Mapping):
        return {
            k: REDACTED if _is_sensitive_key(k) else _scrub(v, depth + 1) for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return type(value)(_scrub(v, depth + 1) for v in value)
    if isinstance(value, str):
        return _SENSITIVE_VALUES.sub(REDACTED, value)
    return value


def redact_sensitive(_: Any, __: str, event_dict: MutableMapping[str, Any]) -> dict[str, Any]:
    return _scrub(event_dict)  # type: ignore[no-any-return]


def configure_logging(level: str = "INFO", json_logs: bool = False) -> None:
    shared: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        redact_sensitive,
    ]
    renderer: structlog.types.Processor = (
        structlog.processors.JSONRenderer() if json_logs else structlog.dev.ConsoleRenderer()
    )

    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.format_exc_info,
            renderer,
        ],
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    # Quiet chatty libraries; their errors still surface.
    for noisy in ("httpx", "httpcore", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)  # type: ignore[no-any-return]
