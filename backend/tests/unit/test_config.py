import pytest
from pydantic import SecretStr, ValidationError

from app.core.config import Environment, LLMProvider, Settings


def make(**kw: object) -> Settings:
    return Settings(_env_file=None, **kw)  # type: ignore[call-arg]


@pytest.mark.parametrize(
    "raw",
    ["postgres://u:p@h:5432/d", "postgresql://u:p@h:5432/d", "postgresql+psycopg://u:p@h:5432/d"],
)
def test_database_url_normalised_to_psycopg(raw: str) -> None:
    s = make(database_url=raw)
    assert s.database_url == "postgresql+psycopg://u:p@h:5432/d"
    assert s.sync_database_url == "postgresql://u:p@h:5432/d"


def test_cors_origins_parsed_from_csv() -> None:
    s = make(cors_origins="http://a.com, http://b.com,")
    assert s.cors_origins == ["http://a.com", "http://b.com"]


def test_production_rejects_default_jwt_secret() -> None:
    with pytest.raises(ValidationError, match="JWT_SECRET_KEY"):
        make(environment=Environment.PRODUCTION)


def test_production_rejects_short_jwt_secret() -> None:
    with pytest.raises(ValidationError):
        make(environment=Environment.PRODUCTION, jwt_secret_key="short")


def test_production_accepts_strong_secret() -> None:
    s = make(environment=Environment.PRODUCTION, jwt_secret_key="x" * 48)
    assert s.is_production


def test_secrets_masked_in_repr() -> None:
    s = make(groq_api_key="gsk_super-secret-value")
    assert "super-secret" not in repr(s)
    assert "super-secret" not in str(s.model_dump())


def test_integration_status_reports_presence_not_values() -> None:
    s = make(groq_api_key=SecretStr("gsk_abc"), tavily_api_key=SecretStr(""))
    status = s.integration_status()
    assert status == {
        "groq": True,
        "gemini": False,
        "tavily": False,
        "exa": False,
        "langsmith": False,
    }
    assert s.provider_configured(LLMProvider.GROQ)
    assert not s.search_configured()


def test_search_configured_follows_selected_provider() -> None:
    s = make(search_provider="exa", exa_api_key=SecretStr("exa-key"))
    assert s.search_configured()


def test_guard_rail_bounds_enforced() -> None:
    with pytest.raises(ValidationError):
        make(max_research_iterations=50)
