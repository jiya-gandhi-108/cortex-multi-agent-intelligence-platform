"""Application settings.

All configuration comes from environment variables (or a local .env file in
development). Secrets are typed as ``SecretStr`` so they are masked in reprs,
logs and tracebacks; call ``.get_secret_value()`` only at the point of use.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, SecretStr, computed_field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Environment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class LLMProvider(StrEnum):
    GROQ = "groq"
    GEMINI = "gemini"


class SearchProvider(StrEnum):
    TAVILY = "tavily"
    EXA = "exa"


_INSECURE_DEFAULT_SECRET = "change-me-in-production-this-is-not-secret"  # noqa: S105 (sentinel, rejected in prod)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- App
    app_name: str = "Cortex"
    environment: Environment = Environment.DEVELOPMENT
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_json: bool = False
    api_v1_prefix: str = "/api/v1"
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000"]
    )

    # --- Database
    # Accepts postgres://, postgresql:// or postgresql+psycopg:// (Supabase/Render
    # hand out the first two); normalised to the psycopg3 async driver below.
    database_url: str = "postgresql+psycopg://cortex:cortex@localhost:5432/cortex"
    db_pool_size: int = Field(default=5, ge=1, le=50)
    db_max_overflow: int = Field(default=10, ge=0, le=100)
    db_echo: bool = False
    # Separate small pool for LangGraph checkpoints. Keep pool + overflow + this within
    # your database's connection limit (Supabase free pooler: see docs/deployment.md).
    checkpoint_pool_size: int = Field(default=5, ge=1, le=50)

    # --- Redis (rate limiting, short-lived caches, SSE fan-out)
    redis_url: str = "redis://localhost:6379/0"

    # --- Auth
    jwt_secret_key: SecretStr = SecretStr(_INSECURE_DEFAULT_SECRET)
    jwt_algorithm: Literal["HS256", "HS384", "HS512"] = "HS256"
    access_token_expire_minutes: int = Field(default=60, ge=5, le=60 * 24 * 7)

    # --- LLM providers
    default_llm_provider: LLMProvider = LLMProvider.GROQ
    groq_api_key: SecretStr | None = None
    # Two tiers: a stronger model for planning/analysis/report writing and a
    # cheaper one for high-volume extraction. Both must support strict
    # json_schema structured outputs on Groq.
    groq_model_reasoning: str = "openai/gpt-oss-120b"
    groq_model_fast: str = "openai/gpt-oss-20b"
    # Client-side throttle so we queue instead of hitting 429s. Defaults match
    # Groq's base per-model limits; raise them on a paid plan.
    groq_tokens_per_minute: int = Field(default=8_000, gt=0)
    groq_requests_per_minute: int = Field(default=30, gt=0)
    gemini_api_key: SecretStr | None = None
    gemini_model: str = "gemini-2.5-pro"
    llm_timeout_seconds: float = Field(default=120.0, gt=0)
    # Hard ceiling on a single prompt. Groq's free plan allows ~8K tokens/minute
    # per model (prompt + completion), so prompts are built to fit well inside it.
    llm_max_prompt_tokens: int = Field(default=4_500, ge=1_000)
    llm_max_retries: int = Field(default=3, ge=0, le=10)

    # Provider pricing in USD per 1M tokens, supplied by config rather than
    # hardcoded so it can track the provider's published price list.
    # Format: JSON object {"model-name": {"input": 3.0, "output": 15.0}}
    llm_pricing_json: str = "{}"

    # --- Research tools
    search_provider: SearchProvider = SearchProvider.TAVILY
    tavily_api_key: SecretStr | None = None
    exa_api_key: SecretStr | None = None
    http_fetch_timeout_seconds: float = Field(default=15.0, gt=0)
    http_fetch_max_bytes: int = Field(default=2_000_000, gt=0)
    extracted_content_max_chars: int = Field(default=20_000, gt=0)
    sources_per_task: int = Field(default=5, ge=1, le=15)
    fetch_robots_txt: bool = True
    user_agent: str = "CortexResearchBot/0.1 (+https://github.com/cortex-research)"
    source_max_age_years: int = Field(default=3, ge=1)

    # --- Workflow guard rails (defaults; users may lower them per project)
    max_research_iterations: int = Field(default=2, ge=0, le=5)
    max_agent_retries: int = Field(default=2, ge=0, le=5)
    max_tokens_per_project: int = Field(default=120_000, gt=0)
    max_cost_usd_per_project: float = Field(default=5.0, gt=0)

    # --- Frontend proxy: the Next.js server forwards the real client IP with this
    # shared secret; without it, X-Client-IP is ignored (it would be spoofable).
    proxy_shared_secret: SecretStr | None = None

    # --- Execution
    max_active_runs_per_user: int = Field(default=2, ge=1, le=20)
    event_stream_ttl_hours: int = Field(default=72, ge=1)

    # --- API rate limiting
    rate_limit_per_minute: int = Field(default=120, gt=0)
    research_start_limit_per_hour: int = Field(default=10, gt=0)

    # --- Observability
    langsmith_api_key: SecretStr | None = None
    langsmith_project: str = "cortex"
    langsmith_tracing: bool = False

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, v: object) -> object:
        if isinstance(v, str):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v

    @field_validator("database_url")
    @classmethod
    def _normalise_db_url(cls, v: str) -> str:
        for prefix in ("postgres://", "postgresql://"):
            if v.startswith(prefix):
                return "postgresql+psycopg://" + v[len(prefix) :]
        return v

    @model_validator(mode="after")
    def _production_guards(self) -> Settings:
        if self.environment is Environment.PRODUCTION:
            secret = self.jwt_secret_key.get_secret_value()
            if secret == _INSECURE_DEFAULT_SECRET or len(secret) < 32:
                raise ValueError(
                    "JWT_SECRET_KEY must be set to a random value of at least 32 "
                    "characters in production"
                )
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def sync_database_url(self) -> str:
        """libpq-style URL for tools that bypass SQLAlchemy (LangGraph checkpointer)."""
        return self.database_url.replace("postgresql+psycopg://", "postgresql://", 1)

    @property
    def is_production(self) -> bool:
        return self.environment is Environment.PRODUCTION

    def provider_configured(self, provider: LLMProvider) -> bool:
        key = self.groq_api_key if provider is LLMProvider.GROQ else self.gemini_api_key
        return _has(key)

    def integration_status(self) -> dict[str, bool]:
        """Which external integrations have credentials. Never exposes values."""
        return {
            "groq": self.provider_configured(LLMProvider.GROQ),
            "gemini": self.provider_configured(LLMProvider.GEMINI),
            "tavily": _has(self.tavily_api_key),
            "exa": _has(self.exa_api_key),
            "langsmith": self.langsmith_tracing and _has(self.langsmith_api_key),
        }

    def search_configured(self) -> bool:
        key = (
            self.tavily_api_key
            if self.search_provider is SearchProvider.TAVILY
            else self.exa_api_key
        )
        return _has(key)


def _has(secret: SecretStr | None) -> bool:
    return secret is not None and bool(secret.get_secret_value())


@lru_cache
def get_settings() -> Settings:
    return Settings()
