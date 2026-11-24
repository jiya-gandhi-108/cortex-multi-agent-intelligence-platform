"""Main-content and metadata extraction from fetched HTML (no JS execution)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime

import trafilatura

from app.tools.fetcher import FetchedPage


@dataclass(frozen=True)
class ExtractedContent:
    text: str
    title: str | None
    publication_date: datetime | None
    site_name: str | None


def _parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m", "%Y"):
        try:
            parsed = datetime.strptime(raw[: len(fmt) + 2].strip(), fmt)
        except ValueError:
            continue
        if parsed.year < 1990 or parsed > datetime.now():
            return None  # implausible: don't let a bad parse look authoritative
        return parsed.replace(tzinfo=UTC)
    return None


def _extract_sync(page: FetchedPage, max_chars: int) -> ExtractedContent:
    if page.content_type == "text/plain":
        return ExtractedContent(page.text[:max_chars], None, None, None)
    text = trafilatura.extract(
        page.text,
        url=page.final_url,
        output_format="txt",
        include_comments=False,
        include_tables=True,
        include_images=False,
        favor_precision=True,
        deduplicate=True,
    )
    meta = trafilatura.extract_metadata(page.text, default_url=page.final_url)
    return ExtractedContent(
        text=(text or "")[:max_chars],
        title=(meta.title if meta and meta.title else None),
        publication_date=_parse_date(meta.date if meta else None),
        site_name=(meta.sitename if meta and meta.sitename else None),
    )


async def extract_content(page: FetchedPage, max_chars: int) -> ExtractedContent:
    # trafilatura is CPU-bound and synchronous; keep it off the event loop.
    return await asyncio.to_thread(_extract_sync, page, max_chars)
