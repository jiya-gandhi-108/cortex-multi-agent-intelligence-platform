"""Research Evidence Collector: search -> dedupe -> rank -> safe fetch -> extract.

Every source returned here was actually retrieved: either the page was
fetched and parsed, or (if fetching failed or was disallowed) the search
provider's own snippet for that URL is used and marked ``fetched=False``.
URLs never come from the LLM.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.core.config import Settings
from app.core.logging import get_logger
from app.schemas.evidence import SourceRecord, citation_key_for
from app.tools.extract import extract_content
from app.tools.fetcher import FetchError, SafeFetcher
from app.tools.sanitize import clean_text
from app.tools.search import SearchError, SearchResult, WebSearch
from app.tools.url_safety import domain_of, normalize_url

log = get_logger(__name__)

MIN_USEFUL_CHARS = 200
MAX_PER_DOMAIN = 2
EXCERPT_CHARS = 6_000  # stored per source; prompts take a smaller slice

ToolCallback = Callable[[str, dict[str, Any]], None]


@dataclass
class CollectionResult:
    sources: list[SourceRecord] = field(default_factory=list)
    failures: list[dict[str, str]] = field(default_factory=list)
    queries_run: int = 0
    search_errors: list[str] = field(default_factory=list)


def rank_results(
    results: list[tuple[str, SearchResult]], limit: int, exclude: set[str]
) -> list[tuple[str, SearchResult]]:
    """Dedupe by normalised URL, prefer higher score, cap per-domain for diversity."""
    best: dict[str, tuple[str, SearchResult]] = {}
    for query, r in results:
        norm = normalize_url(r.url)
        if norm in exclude:
            continue
        if norm not in best or (r.score or 0) > (best[norm][1].score or 0):
            best[norm] = (query, r)
    ordered = sorted(best.values(), key=lambda qr: qr[1].score or 0, reverse=True)
    per_domain: dict[str, int] = {}
    picked: list[tuple[str, SearchResult]] = []
    for query, r in ordered:
        d = domain_of(r.url)
        if per_domain.get(d, 0) >= MAX_PER_DOMAIN:
            continue
        per_domain[d] = per_domain.get(d, 0) + 1
        picked.append((query, r))
        if len(picked) >= limit:
            break
    return picked


class EvidenceCollector:
    """Stateless across calls, so one instance is safely shared by parallel branches."""

    def __init__(
        self,
        settings: Settings,
        search: WebSearch,
        fetcher: SafeFetcher,
        on_tool_call: ToolCallback | None = None,
        fetch_concurrency: int = 4,
    ) -> None:
        self.settings = settings
        self.search = search
        self.fetcher = fetcher
        self.on_tool_call = on_tool_call or (lambda _n, _d: None)
        self._sem = asyncio.Semaphore(fetch_concurrency)

    async def _search_all(
        self, queries: list[str], result: CollectionResult, limit: int, notify: ToolCallback
    ) -> list[tuple[str, SearchResult]]:
        found: list[tuple[str, SearchResult]] = []
        for q in queries:
            start = time.perf_counter()
            outcome: dict[str, Any]
            try:
                hits = await self.search.search(q, limit)
                found.extend((q, h) for h in hits)
                outcome = {"results": len(hits)}
            except SearchError as exc:
                result.search_errors.append(str(exc))
                outcome = {"error": str(exc)}
                if not exc.transient:
                    raise
            finally:
                result.queries_run += 1
            notify(
                "web_search",
                {"query": q, "provider": self.search.name, "ms": _ms(start), **outcome},
            )
        return found

    async def _materialise(
        self,
        task_key: str,
        query: str,
        hit: SearchResult,
        result: CollectionResult,
        notify: ToolCallback,
    ) -> SourceRecord | None:
        norm = normalize_url(hit.url)
        title, published, text, fetched = hit.title, hit.published, "", False
        async with self._sem:
            start = time.perf_counter()
            outcome: dict[str, Any]
            try:
                page = await self.fetcher.fetch(hit.url)
                extracted = await extract_content(page, self.settings.extracted_content_max_chars)
                if len(extracted.text) >= MIN_USEFUL_CHARS:
                    text, fetched = extracted.text, True
                    title = extracted.title or title
                    published = extracted.publication_date or published
                else:
                    result.failures.append({"url": hit.url, "reason": "too_little_content"})
                outcome = {"fetched": fetched, "chars": len(text)}
            except FetchError as exc:
                result.failures.append({"url": hit.url, "reason": exc.reason.value})
                outcome = {"error": exc.reason.value}
            notify("url_extract", {"url": hit.url, "ms": _ms(start), **outcome})

        if not fetched:
            text = hit.snippet  # provider-retrieved snippet for this exact URL
        text = clean_text(text, EXCERPT_CHARS)
        if not text:
            return None
        return SourceRecord(
            citation_key=citation_key_for(norm),
            url=hit.url,
            normalized_url=norm,
            title=clean_text(title, 300) if title else None,
            domain=domain_of(hit.url),
            publication_date=published,
            retrieved_at=datetime.now(UTC),
            content=text,
            fetched=fetched,
            task_key=task_key,
            search_provider=hit.provider,
            query=query,
        )

    async def collect(
        self,
        task_key: str,
        queries: list[str],
        known_urls: set[str] | None = None,
        limit: int | None = None,
        on_tool_call: ToolCallback | None = None,
    ) -> CollectionResult:
        """``on_tool_call`` is per call because the collector is shared across branches."""
        limit = limit or self.settings.sources_per_task
        notify = on_tool_call or self.on_tool_call
        result = CollectionResult()
        hits = await self._search_all(queries, result, limit, notify)
        picked = rank_results(hits, limit, known_urls or set())
        records = await asyncio.gather(
            *(self._materialise(task_key, q, h, result, notify) for q, h in picked)
        )
        result.sources = [r for r in records if r is not None]
        log.info(
            "evidence_collected",
            task=task_key,
            queries=result.queries_run,
            sources=len(result.sources),
            fetched=sum(s.fetched for s in result.sources),
            failures=len(result.failures),
        )
        return result


def _ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 1)
