"""SSRF-safe HTTP fetcher.

* Every hop (initial URL and each redirect) is vetted by ``vet_url``.
* The TCP connection goes to the *vetted IP*, with the original hostname sent
  as Host header and TLS SNI, so certificate verification still checks the real
  hostname and DNS rebinding between check and connect is impossible.
* Redirects are followed manually (max 5), never by the HTTP client.
* robots.txt is honoured per RFC 9309 (4xx -> allowed, 5xx/unreachable -> disallowed).
* Only HTML/XHTML/plain text is read; the body is streamed and capped.
* Nothing is executed: HTML is handed to a parser, never rendered.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urljoin
from urllib.robotparser import RobotFileParser

import httpx

from app.core.config import Settings
from app.core.logging import get_logger
from app.monitoring.tracing import trace_tool
from app.tools.url_safety import Resolver, UnsafeURLError, VettedURL, system_resolver, vet_url

log = get_logger(__name__)

MAX_REDIRECTS = 5
_TEXT_TYPES = ("text/html", "application/xhtml+xml", "text/plain")


class FetchFailure(StrEnum):
    UNSAFE_URL = "unsafe_url"
    ROBOTS_DISALLOWED = "robots_disallowed"
    TIMEOUT = "timeout"
    HTTP_STATUS = "http_status"
    UNSUPPORTED_CONTENT = "unsupported_content"
    TOO_MANY_REDIRECTS = "too_many_redirects"
    NETWORK = "network"


class FetchError(Exception):
    def __init__(self, reason: FetchFailure, detail: str = "") -> None:
        super().__init__(f"{reason.value}: {detail}" if detail else reason.value)
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class FetchedPage:
    requested_url: str
    final_url: str
    status_code: int
    content_type: str
    text: str
    truncated: bool


def _pinned_url(v: VettedURL) -> str:
    host = f"[{v.ip}]" if isinstance(ipaddress.ip_address(v.ip), ipaddress.IPv6Address) else v.ip
    return f"{v.scheme}://{host}:{v.port}{v.path_and_query}"


def _host_header(v: VettedURL) -> str:
    default = 443 if v.scheme == "https" else 80
    return v.host if v.port == default else f"{v.host}:{v.port}"


class SafeFetcher:
    def __init__(
        self,
        settings: Settings,
        client: httpx.AsyncClient | None = None,
        resolver: Resolver = system_resolver,
    ) -> None:
        self.settings = settings
        self.resolver = resolver
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(settings.http_fetch_timeout_seconds, connect=5.0),
            follow_redirects=False,
            trust_env=False,  # never route through env proxies: they'd bypass IP pinning
            headers={"User-Agent": settings.user_agent, "Accept": "text/html,text/plain;q=0.9"},
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )
        self._robots: dict[str, RobotFileParser | None] = {}

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def _send(self, v: VettedURL, max_bytes: int) -> tuple[httpx.Response, bytes, bool]:
        request = self.client.build_request(
            "GET",
            _pinned_url(v),
            headers={"Host": _host_header(v)},
            extensions={"sni_hostname": v.host},
        )
        try:
            response = await self.client.send(request, stream=True)
        except httpx.TimeoutException as exc:
            raise FetchError(FetchFailure.TIMEOUT, v.host) from exc
        except httpx.HTTPError as exc:
            raise FetchError(FetchFailure.NETWORK, f"{v.host}: {type(exc).__name__}") from exc
        body = bytearray()
        truncated = False
        try:
            if response.status_code == 200:
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) >= max_bytes:
                        truncated = True
                        break
        except httpx.TimeoutException as exc:
            raise FetchError(FetchFailure.TIMEOUT, v.host) from exc
        except httpx.HTTPError as exc:
            raise FetchError(FetchFailure.NETWORK, f"{v.host}: {type(exc).__name__}") from exc
        finally:
            await response.aclose()
        return response, bytes(body[:max_bytes]), truncated

    async def _allowed_by_robots(self, v: VettedURL) -> bool:
        if not self.settings.fetch_robots_txt:
            return True
        origin = f"{v.scheme}://{_host_header(v)}"
        if origin not in self._robots:
            robots_v = VettedURL(
                f"{origin}/robots.txt", v.scheme, v.host, v.port, v.ip, "/robots.txt"
            )
            parser: RobotFileParser | None = RobotFileParser()
            try:
                resp, body, _ = await self._send(robots_v, 256_000)
                if resp.status_code == 200:
                    parser.parse(body.decode("utf-8", "replace").splitlines())  # type: ignore[union-attr]
                elif 400 <= resp.status_code < 500:
                    parser.allow_all = True  # type: ignore[union-attr]
                else:
                    parser = None  # 5xx: treat as full disallow (RFC 9309 §2.3.1.4)
            except FetchError:
                parser = None
            self._robots[origin] = parser
        parser = self._robots[origin]
        if parser is None:
            return False
        return parser.can_fetch(self.settings.user_agent, v.url)

    @trace_tool("url_fetch")
    async def fetch(self, url: str) -> FetchedPage:
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            try:
                vetted = await vet_url(current, self.resolver)
            except UnsafeURLError as exc:
                raise FetchError(FetchFailure.UNSAFE_URL, str(exc)) from exc
            if not await self._allowed_by_robots(vetted):
                raise FetchError(FetchFailure.ROBOTS_DISALLOWED, vetted.host)

            response, body, truncated = await self._send(vetted, self.settings.http_fetch_max_bytes)
            if response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get("location")
                if not location:
                    raise FetchError(FetchFailure.HTTP_STATUS, "redirect without location")
                current = urljoin(current, location)
                continue
            if response.status_code != 200:
                raise FetchError(FetchFailure.HTTP_STATUS, str(response.status_code))
            content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
            if content_type and not content_type.startswith(_TEXT_TYPES):
                raise FetchError(FetchFailure.UNSUPPORTED_CONTENT, content_type)
            encoding = response.charset_encoding or "utf-8"
            try:
                text = body.decode(encoding, "replace")
            except LookupError:
                text = body.decode("utf-8", "replace")
            return FetchedPage(url, current, 200, content_type or "text/html", text, truncated)
        raise FetchError(FetchFailure.TOO_MANY_REDIRECTS, url)
