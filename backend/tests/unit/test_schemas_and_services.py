from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.core.rate_limit import InMemoryRateLimiter
from app.models import UserSettings
from app.models.enums import AgentName, ProjectStatus
from app.schemas.auth import RegisterRequest
from app.schemas.research import ExecutionLimits, ProjectCreate
from app.services.projects import derive_title
from app.services.settings import resolve_limits

OBJECTIVE = "Research the Indian healthcare SaaS market and identify major competitors."


class TestRegisterRequest:
    def test_email_lowercased(self) -> None:
        r = RegisterRequest(email="Alice@Example.COM", password="abcdef12")
        assert r.email == "alice@example.com"

    @pytest.mark.parametrize("pw", ["short1", "allletters", "12345678", "x" * 129 + "1"])
    def test_weak_or_oversized_passwords_rejected(self, pw: str) -> None:
        with pytest.raises(ValidationError):
            RegisterRequest(email="a@b.com", password=pw)


class TestProjectCreate:
    def test_valid(self) -> None:
        p = ProjectCreate(objective=OBJECTIVE, focus_areas=["pricing", "competitors", "pricing"])
        assert p.focus_areas == ["competitors", "pricing"]

    def test_objective_too_short(self) -> None:
        with pytest.raises(ValidationError):
            ProjectCreate(objective="too short")

    def test_unknown_focus_area_rejected(self) -> None:
        with pytest.raises(ValidationError, match="unknown focus"):
            ProjectCreate(objective=OBJECTIVE, focus_areas=["astrology"])

    def test_control_characters_stripped(self) -> None:
        p = ProjectCreate(objective=OBJECTIVE + "\x00\x1b[31m")
        assert "\x00" not in p.objective and "\x1b" not in p.objective


def test_derive_title_truncates_at_word_boundary() -> None:
    long = "Analyse " + "the quite long healthcare market objective " * 5
    title = derive_title(long)
    assert len(title) <= 81 and title.endswith("…")
    assert derive_title("Short goal. More detail here.") == "Short goal"


class TestResolveLimits:
    def test_server_max_when_nothing_set(self, settings: Settings) -> None:
        out = resolve_limits(settings, UserSettings(require_plan_approval=False))
        assert out["max_tokens"] == settings.max_tokens_per_project
        assert out["max_research_iterations"] == settings.max_research_iterations

    def test_users_can_only_tighten_limits(self, settings: Settings) -> None:
        user = UserSettings(max_tokens_per_project=50_000, require_plan_approval=False)
        loose = ExecutionLimits(max_tokens=10_000_000 // 10, max_cost_usd=Decimal("99"))
        out = resolve_limits(settings, user, loose)
        assert out["max_tokens"] == 50_000
        assert out["max_cost_usd"] == settings.max_cost_usd_per_project

    def test_override_tightens_further(self, settings: Settings) -> None:
        user = UserSettings(max_tokens_per_project=50_000, require_plan_approval=False)
        out = resolve_limits(settings, user, ExecutionLimits(max_tokens=20_000))
        assert out["max_tokens"] == 20_000

    def test_plan_approval_override(self, settings: Settings) -> None:
        user = UserSettings(require_plan_approval=False)
        out = resolve_limits(settings, user, ExecutionLimits(require_plan_approval=True))
        assert out["require_plan_approval"] is True


async def test_in_memory_rate_limiter_blocks_after_limit() -> None:
    rl = InMemoryRateLimiter()
    results = [await rl.hit("k", limit=3, window_seconds=60) for _ in range(4)]
    assert [r.allowed for r in results] == [True, True, True, False]
    assert results[2].remaining == 0


def test_enum_semantics() -> None:
    assert ProjectStatus.COMPLETED.is_terminal and not ProjectStatus.RUNNING.is_terminal
    assert ProjectStatus.RUNNING.is_active and not ProjectStatus.DRAFT.is_active
    assert AgentName.PLANNER not in AgentName.assignable()
    assert AgentName.RESEARCH in AgentName.assignable()


def test_client_ip_trusts_forwarded_ip_only_with_shared_secret(monkeypatch) -> None:
    from starlette.requests import Request

    from app.core import config
    from app.core.rate_limit import client_ip
    from tests.conftest import make_settings

    def req(headers: dict[str, str]) -> Request:
        raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
        return Request({"type": "http", "headers": raw, "client": ("10.0.0.9", 1234)})

    monkeypatch.setattr(config, "get_settings", lambda: make_settings(proxy_shared_secret="s3cret"))
    assert client_ip(req({"X-Client-IP": "1.2.3.4", "X-Proxy-Secret": "s3cret"})) == "1.2.3.4"
    assert client_ip(req({"X-Client-IP": "1.2.3.4", "X-Proxy-Secret": "wrong"})) == "10.0.0.9"
    assert client_ip(req({"X-Client-IP": "1.2.3.4"})) == "10.0.0.9"
    monkeypatch.setattr(config, "get_settings", lambda: make_settings())
    assert client_ip(req({"X-Client-IP": "1.2.3.4", "X-Proxy-Secret": "anything"})) == "10.0.0.9"
