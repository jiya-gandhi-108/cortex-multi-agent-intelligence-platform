"""FastAPI application factory."""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import structlog
from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis

from app.api import health
from app.api.v1 import auth, dashboard, research, runs
from app.api.v1 import settings as settings_router
from app.core.config import Settings, get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.core.rate_limit import RateLimiter, RedisRateLimiter
from app.database.checkpointer import open_checkpointer
from app.database.session import dispose_engine, init_engine
from app.monitoring.cost import parse_pricing
from app.monitoring.tracing import configure_tracing
from app.services.runner import ResearchRunner

log = get_logger(__name__)

MAX_BODY_BYTES = 256 * 1024  # research objectives are capped at 4k chars; this is generous


def create_app(
    settings: Settings | None = None, rate_limiter: RateLimiter | None = None
) -> FastAPI:
    cfg = settings or get_settings()
    configure_logging(cfg.log_level, cfg.log_json)
    configure_tracing(cfg)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        init_engine(cfg)
        app.state.redis = Redis.from_url(cfg.redis_url, decode_responses=True)
        if rate_limiter is None:
            app.state.rate_limiter = RedisRateLimiter(app.state.redis)

        # LangGraph checkpoints live in the same Postgres (own tables, created by setup()).
        checkpoints = open_checkpointer(cfg.sync_database_url, cfg.checkpoint_pool_size)
        checkpointer = await checkpoints.__aenter__()
        runner = ResearchRunner(cfg, app.state.redis, checkpointer)
        await runner.recover_orphans()
        app.state.runner = runner

        priced = parse_pricing(cfg.llm_pricing_json)
        unpriced = [m for m in (cfg.groq_model_reasoning, cfg.groq_model_fast) if m not in priced]
        if unpriced:
            log.warning(
                "llm_pricing_missing",
                models=unpriced,
                detail="cost estimates for these models will read as unknown",
            )
        if cfg.is_production and not cfg.proxy_shared_secret:
            log.warning(
                "proxy_secret_missing",
                detail="per-IP login limits will see the frontend's IP, not the user's",
            )
        log.info("startup", environment=cfg.environment, integrations=cfg.integration_status())
        try:
            yield
        finally:
            await runner.shutdown()
            await checkpoints.__aexit__(None, None, None)
            await app.state.redis.aclose()
            await dispose_engine()
            log.info("shutdown")

    app = FastAPI(
        title="Cortex API",
        version="0.4.0",
        description="Autonomous multi-agent research & decision intelligence platform.",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url=None,
        openapi_url="/openapi.json",
    )
    app.state.redis = None
    app.state.runner = None
    if rate_limiter is not None:
        app.state.rate_limiter = rate_limiter

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.cors_origins,
        allow_credentials=False,  # bearer tokens, not cookies
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID", "Last-Event-ID"],
        expose_headers=["X-Request-ID", "Retry-After"],
    )

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        incoming = request.headers.get("x-request-id", "")
        rid = incoming if 8 <= len(incoming) <= 64 and incoming.isalnum() else uuid.uuid4().hex
        request.state.request_id = rid
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=rid)

        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > MAX_BODY_BYTES:
            from fastapi.responses import JSONResponse

            return JSONResponse(
                {
                    "error": {"code": "payload_too_large", "message": "Request body too large"},
                    "request_id": rid,
                },
                status_code=413,
            )

        start = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = round((time.perf_counter() - start) * 1000, 1)
        response.headers["X-Request-ID"] = rid
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path not in ("/health",):
            log.info(
                "request",
                method=request.method,
                path=request.url.path,
                status=response.status_code,
                ms=elapsed_ms,
            )
        return response

    register_exception_handlers(app)

    api = APIRouter(prefix=cfg.api_v1_prefix)
    api.include_router(auth.router)
    api.include_router(research.router)
    api.include_router(runs.router)
    api.include_router(dashboard.router)
    api.include_router(settings_router.router)
    app.include_router(api)
    app.include_router(health.router)
    return app


app = create_app()
