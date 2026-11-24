"""Markdown -> HTML -> PDF export.

Safety: raw HTML in the Markdown is disabled (report text originates partly
from LLM output over web content), and WeasyPrint's resource fetcher refuses
every URL, so rendering a report can never make a network request (no SSRF,
no tracking pixels). Links stay clickable in the PDF; they're just not fetched.
"""

from __future__ import annotations

import asyncio
from html import escape
from typing import Any

from markdown_it import MarkdownIt
from weasyprint.urls import URLFetcher, URLFetchingError

_md = MarkdownIt("commonmark", {"html": False, "linkify": False, "typographer": True}).enable(
    "table"
)

CSS = """
@page { size: A4; margin: 18mm 16mm 20mm 16mm;
        @bottom-center { content: "Cortex research report · page " counter(page)
                                  " of " counter(pages); font-size: 8pt; color: #6b7280; } }
body { font-family: "DejaVu Sans", Helvetica, Arial, sans-serif; font-size: 10pt; line-height: 1.5;
       color: #111827; }
h1 { font-size: 20pt; margin: 0 0 4pt; color: #1e1b4b; }
h2 { font-size: 13pt; margin: 16pt 0 6pt; padding-bottom: 3pt; border-bottom: 1px solid #e5e7eb;
     color: #312e81; page-break-after: avoid; }
p, li { orphans: 3; widows: 3; }
blockquote { margin: 8pt 0; padding: 6pt 10pt; background: #fef3c7;
             border-left: 3px solid #d97706; }
table { width: 100%; border-collapse: collapse; margin: 6pt 0; font-size: 8.5pt; }
th, td { border: 1px solid #d1d5db; padding: 3pt 5pt; vertical-align: top; text-align: left; }
th { background: #eef2ff; }
a { color: #4338ca; text-decoration: none; word-break: break-all; }
code { font-size: 8.5pt; }
"""


# weasyprint ships no type hints, hence the ignores on these two subclasses.
class BlockedFetchError(URLFetchingError):  # type: ignore[misc]
    """Non-fatal: the resource is skipped and the PDF still renders."""


class DenyAllFetcher(URLFetcher):  # type: ignore[misc]
    """WeasyPrint fetcher that refuses every URL (http, file, ftp, data, ...)."""

    def __init__(self) -> None:
        super().__init__(allowed_protocols=(), allow_redirects=False, timeout=1)

    def fetch(self, url: str, headers: Any = None) -> Any:
        raise BlockedFetchError(f"external resource blocked: {url[:80]}")


def markdown_to_html(markdown: str, title: str) -> str:
    body = _md.render(markdown)
    return (
        f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>{escape(title)}</title>"
        f"<style>{CSS}</style></head><body>{body}</body></html>"
    )


def _render_pdf(markdown: str, title: str) -> bytes:
    from weasyprint import HTML

    html = HTML(string=markdown_to_html(markdown, title), url_fetcher=DenyAllFetcher())
    return html.write_pdf()  # type: ignore[no-any-return]


async def render_pdf(markdown: str, title: str) -> bytes:
    return await asyncio.to_thread(_render_pdf, markdown, title)
