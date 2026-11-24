"""Web search providers (Tavily, Exa) behind one interface, called over REST."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

import httpx

from app.core.config import SearchProvider, Settings
from app.core.logging import get_logger
from app.llm.retry import backoff_delay
from app.monitoring.tracing import trace_tool

log = get_logger(__name__)


@dataclass(frozen=True)
class SearchResult:
    url: str
    title: str | None
    snippet: str
    published: datetime | None
    score: float | None
    provider: str


class SearchError(Exception):
    def __init__(self, message: str, *, transient: bool = False) -> None:
        super().__init__(message)
        self.transient = transient


class SearchConfigurationError(SearchError):
    pass


class WebSearch(Protocol):
    name: str

    async def search(self, query: str, max_results: int) -> list[SearchResult]: ...


def _parse_dt(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        try:
            from email.utils import parsedate_to_datetime

            return parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            return None


class _RestSearch:
    name = "base"
    endpoint = ""

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self._owns = client is None
        self.client = client or httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=5.0))

    async def aclose(self) -> None:
        if self._owns:
            await self.client.aclose()

    def _headers(self) -> dict[str, str]:
        raise NotImplementedError

    def _body(self, query: str, max_results: int) -> dict[str, Any]:
        raise NotImplementedError

    def _parse(self, data: dict[str, Any]) -> list[SearchResult]:
        raise NotImplementedError

    @trace_tool("web_search")
    async def search(self, query: str, max_results: int) -> list[SearchResult]:
        import asyncio

        query = query.strip()[:400]
        if not query:
            return []
        last: SearchError | None = None
        for attempt in range(3):
            try:
                resp = await self.client.post(
                    self.endpoint, json=self._body(query, max_results), headers=self._headers()
                )
            except httpx.TimeoutException:
                last = SearchError(f"{self.name} timeout", transient=True)
            except httpx.HTTPError as exc:
                last = SearchError(
                    f"{self.name} network error: {type(exc).__name__}", transient=True
                )
            else:
                if resp.status_code == 200:
                    return self._parse(resp.json())[:max_results]
                if resp.status_code in (401, 403):
                    raise SearchConfigurationError(f"{self.name} rejected the API key")
                if resp.status_code in (429, 500, 502, 503, 504):
                    last = SearchError(f"{self.name} HTTP {resp.status_code}", transient=True)
                else:
                    raise SearchError(f"{self.name} HTTP {resp.status_code}: {resp.text[:200]}")
            if attempt < 2:
                await asyncio.sleep(backoff_delay(attempt, base=1.0, cap=8.0))
        assert last is not None
        raise last


class TavilySearch(_RestSearch):
    name = "tavily"
    endpoint = "https://api.tavily.com/search"

    def _headers(self) -> dict[str, str]:
        assert self.settings.tavily_api_key is not None
        return {"Authorization": f"Bearer {self.settings.tavily_api_key.get_secret_value()}"}

    def _body(self, query: str, max_results: int) -> dict[str, Any]:
        # "basic" depth costs 1 credit; raw content is fetched by our own safe fetcher.
        return {
            "query": query,
            "max_results": max_results,
            "search_depth": "basic",
            "include_answer": False,
            "include_raw_content": False,
        }

    def _parse(self, data: dict[str, Any]) -> list[SearchResult]:
        return [
            SearchResult(
                url=r["url"],
                title=r.get("title"),
                snippet=r.get("content") or "",
                published=_parse_dt(r.get("published_date")),
                score=r.get("score"),
                provider=self.name,
            )
            for r in data.get("results", [])
            if isinstance(r, dict) and r.get("url")
        ]


class ExaSearch(_RestSearch):
    name = "exa"
    endpoint = "https://api.exa.ai/search"

    def _headers(self) -> dict[str, str]:
        assert self.settings.exa_api_key is not None
        return {"x-api-key": self.settings.exa_api_key.get_secret_value()}

    def _body(self, query: str, max_results: int) -> dict[str, Any]:
        return {
            "query": query,
            "numResults": max_results,
            "type": "auto",
            "contents": {"text": {"maxCharacters": 1500}},
        }

    def _parse(self, data: dict[str, Any]) -> list[SearchResult]:
        return [
            SearchResult(
                url=r["url"],
                title=r.get("title"),
                snippet=r.get("text") or r.get("summary") or "",
                published=_parse_dt(r.get("publishedDate")),
                score=r.get("score"),
                provider=self.name,
            )
            for r in data.get("results", [])
            if isinstance(r, dict) and r.get("url")
        ]


def get_search_provider(settings: Settings, client: httpx.AsyncClient | None = None) -> WebSearch:
    if not settings.search_configured():
        provider = settings.search_provider.value
        raise SearchConfigurationError(
            f"{provider} is not configured: set {provider.upper()}_API_KEY"
        )
    if settings.search_provider is SearchProvider.TAVILY:
        return TavilySearch(settings, client)
    return ExaSearch(settings, client)
