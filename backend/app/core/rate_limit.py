"""Fixed-window rate limiting.

Redis-backed in deployment (shared across instances); an in-memory backend
exists for unit tests and single-process development. If Redis is down the
limiter fails *open* and logs: availability of the API matters more than
strict limits on cheap endpoints, and the expensive path (starting research)
is additionally bounded by per-project token and cost budgets.
"""

from __future__ import annotations

import hmac
import time
from dataclasses import dataclass
from typing import Protocol

from fastapi import Request
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.errors import RateLimitedError
from app.core.logging import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    remaining: int
    reset_in: int  # seconds until the window resets


class RateLimiter(Protocol):
    async def hit(self, key: str, limit: int, window_seconds: int) -> RateLimitResult: ...


class RedisRateLimiter:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def hit(self, key: str, limit: int, window_seconds: int) -> RateLimitResult:
        window = int(time.time() // window_seconds)
        rkey = f"ratelimit:{key}:{window}"
        try:
            async with self._redis.pipeline(transaction=True) as pipe:
                pipe.incr(rkey)
                pipe.expire(rkey, window_seconds + 1)
                count, _ = await pipe.execute()
        except RedisError as exc:
            log.warning("rate_limiter_unavailable", error=type(exc).__name__)
            return RateLimitResult(True, limit, window_seconds)
        reset_in = window_seconds - int(time.time() % window_seconds)
        return RateLimitResult(int(count) <= limit, max(0, limit - int(count)), reset_in)


class InMemoryRateLimiter:
    def __init__(self) -> None:
        self._counts: dict[str, int] = {}

    async def hit(self, key: str, limit: int, window_seconds: int) -> RateLimitResult:
        window = int(time.time() // window_seconds)
        rkey = f"{key}:{window}"
        self._counts[rkey] = self._counts.get(rkey, 0) + 1
        count = self._counts[rkey]
        reset_in = window_seconds - int(time.time() % window_seconds)
        return RateLimitResult(count <= limit, max(0, limit - count), reset_in)

    def reset(self) -> None:
        self._counts.clear()


def client_ip(request: Request) -> str:
    """Client address for per-IP limits.

    Requests relayed by the Cortex frontend carry the browser's IP in X-Client-IP,
    trusted only when X-Proxy-Secret matches PROXY_SHARED_SECRET (constant-time).
    Otherwise use the socket peer (uvicorn --proxy-headers resolves the platform's
    own load balancer).
    """
    from app.core.config import get_settings

    secret = get_settings().proxy_shared_secret
    claimed = request.headers.get("x-client-ip", "").strip()
    supplied = request.headers.get("x-proxy-secret", "")
    if secret and claimed and hmac.compare_digest(supplied, secret.get_secret_value()):
        return claimed[:64]
    return request.client.host if request.client else "unknown"


async def enforce_rate_limit(request: Request, key: str, limit: int, window_seconds: int) -> None:
    limiter: RateLimiter = request.app.state.rate_limiter
    result = await limiter.hit(key, limit, window_seconds)
    if not result.allowed:
        raise RateLimitedError(
            f"Rate limit exceeded; retry in {result.reset_in}s",
            headers={
                "Retry-After": str(result.reset_in),
                "X-RateLimit-Limit": str(limit),
                "X-RateLimit-Remaining": "0",
            },
        )
