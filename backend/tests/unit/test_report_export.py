import pytest

from app.services.events import is_terminal
from app.services.report_export import (
    BlockedFetchError,
    DenyAllFetcher,
    markdown_to_html,
    render_pdf,
)


def test_raw_html_in_markdown_is_escaped() -> None:
    html = markdown_to_html("Hello <script>alert(1)</script> <img src=x onerror=y>", "T")
    assert "<script>alert" not in html and "<img src=x" not in html
    assert "&lt;script&gt;" in html


def test_tables_render() -> None:
    html = markdown_to_html("| a | b |\n|---|---|\n| 1 | 2 |", "T")
    assert "<table>" in html and "<td>1</td>" in html


def test_resource_fetcher_refuses_everything() -> None:
    for url in ("http://169.254.169.254/latest", "file:///etc/passwd", "https://example.com/x.png"):
        with pytest.raises(BlockedFetchError):
            DenyAllFetcher().fetch(url)


async def test_pdf_renders_without_fetching_remote_images() -> None:
    md = "# Report\n\n![tracker](http://169.254.169.254/pixel.png)\n\nText [link](https://example.com)."
    pdf = await render_pdf(md, "Report")
    assert pdf.startswith(b"%PDF") and len(pdf) > 1000


@pytest.mark.parametrize(
    ("event", "terminal"),
    [
        ({"type": "completed"}, False),  # graph event: DB not final yet
        ({"type": "error"}, False),
        ({"type": "status", "data": {"status": "completed"}}, True),
        ({"type": "status", "data": {"status": "awaiting_approval"}}, True),
        ({"type": "status", "data": {"status": "planning"}}, False),
        ({"type": "llm_call"}, False),
    ],
)
def test_terminal_event_detection(event: dict, terminal: bool) -> None:
    assert is_terminal(event) is terminal
