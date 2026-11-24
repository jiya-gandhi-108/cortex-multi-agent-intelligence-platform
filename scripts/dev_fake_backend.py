"""Run the real Cortex API with scripted agents, for frontend development and demos
without LLM/search API keys.

Everything is real (FastAPI, Postgres, Redis, LangGraph, checkpoints, SSE) except the
LLM and the web, which are replaced by the deterministic fakes used in the test suite.
Each evidence-collection step pauses briefly so the live view has something to show.

    cd backend && .venv/bin/python ../scripts/dev_fake_backend.py  # http://localhost:8000

Refuses to start when ENVIRONMENT=production.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)

import uvicorn  # noqa: E402
from pydantic import SecretStr  # noqa: E402

from app.core.config import Environment, get_settings  # noqa: E402
from app.main import create_app  # noqa: E402
from tests.fakes import FakeCollector, FakeLLM  # noqa: E402

PAUSE_SECONDS = float(os.environ.get("FAKE_STEP_SECONDS", "2.5"))


class PacedCollector(FakeCollector):
    async def collect(self, *args: Any, **kwargs: Any) -> Any:
        await asyncio.sleep(PAUSE_SECONDS)
        return await super().collect(*args, **kwargs)


async def main() -> None:
    base = get_settings()
    if base.environment is Environment.PRODUCTION:
        raise SystemExit("dev_fake_backend.py must never run in production")
    # Fake keys only satisfy the start preflight; nothing calls the real providers.
    settings = base.model_copy(
        update={"groq_api_key": SecretStr("gsk_fake"), "tavily_api_key": SecretStr("tvly-fake")}
    )
    app = create_app(settings=settings)
    app.dependency_overrides[get_settings] = lambda: settings

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=int(os.environ.get("PORT", "8000"))))
    serve = asyncio.create_task(server.serve())
    while not server.started:
        if serve.done():
            return
        await asyncio.sleep(0.05)
    app.state.runner.llm_factory = lambda s, p: FakeLLM()
    app.state.runner.collector_factory = lambda s: PacedCollector()
    print("Cortex API (scripted agents) on http://127.0.0.1:8000. NOT for production.")
    await serve


if __name__ == "__main__":
    asyncio.run(main())
