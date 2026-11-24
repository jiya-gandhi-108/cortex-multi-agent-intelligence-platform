from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core.config import get_settings
from app.database.session import get_sessionmaker

router = APIRouter(tags=["health"])


@router.get("/health")
async def liveness() -> dict[str, str]:
    """Process is up. Used by Render's health check; no dependencies touched."""
    return {"status": "ok"}


@router.get("/health/ready")
async def readiness(request: Request) -> JSONResponse:
    """Dependencies reachable. Reports integration *presence* only, never keys."""
    checks: dict[str, str] = {}
    try:
        async with get_sessionmaker()() as session:
            await session.execute(text("select 1"))
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "unavailable"
    redis = getattr(request.app.state, "redis", None)
    if redis is None:
        checks["redis"] = "not_configured"
    else:
        try:
            await redis.ping()
            checks["redis"] = "ok"
        except Exception:
            checks["redis"] = "unavailable"
    ready = checks["database"] == "ok"
    return JSONResponse(
        {
            "status": "ready" if ready else "degraded",
            "checks": checks,
            "integrations": get_settings().integration_status(),
        },
        status_code=200 if ready else 503,
    )
