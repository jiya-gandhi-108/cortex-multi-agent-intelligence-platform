"""URL normalisation and SSRF defence.

Threat model: URLs reach the fetcher from search results and, transitively,
from attacker-controlled web pages (redirects). The fetcher must never reach
loopback, private, link-local (cloud metadata at 169.254.169.254), CGNAT,
multicast or reserved addresses, over IPv4 or IPv6, however the address is
spelled (decimal ``2130706433``, octal, IPv4-mapped IPv6, DNS names that
resolve inward).

Defence: parse strictly, allow only http/https on standard ports with no
userinfo, resolve the hostname once, require *every* resolved address to be
globally routable, and then connect to that vetted address (see fetcher),
which removes the DNS-rebinding window between check and use.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

ALLOWED_SCHEMES = frozenset({"http", "https"})
ALLOWED_PORTS = frozenset({80, 443, 8080, 8443})
_BLOCKED_HOST_SUFFIXES = (".localhost", ".local", ".internal", ".intranet", ".home.arpa", ".lan")
_TRACKING_PARAMS = frozenset(
    {
        "gclid",
        "fbclid",
        "msclkid",
        "mc_cid",
        "mc_eid",
        "ref",
        "ref_src",
        "igshid",
        "_hsenc",
        "_hsmi",
    }
)

Resolver = Callable[[str, int], Awaitable[list[str]]]


class UnsafeURLError(ValueError):
    pass


@dataclass(frozen=True)
class VettedURL:
    url: str
    scheme: str
    host: str  # hostname as given (for Host header / SNI)
    port: int
    ip: str  # vetted address to connect to
    path_and_query: str


def normalize_url(url: str) -> str:
    """Canonical form used for de-duplication (not for fetching)."""
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    port = parts.port
    default = {"http": 80, "https": 443}.get(scheme)
    netloc = host if port in (None, default) else f"{host}:{port}"
    query = sorted(
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in _TRACKING_PARAMS
    )
    path = parts.path or "/"
    if len(path) > 1:
        path = path.rstrip("/")
    return urlunsplit((scheme, netloc, path, urlencode(query), ""))


def domain_of(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    return host[4:] if host.startswith("www.") else host


def is_public_ip(raw: str) -> bool:
    try:
        ip = ipaddress.ip_address(raw.split("%", 1)[0])  # drop IPv6 zone id
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return is_public_ip(str(ip.ipv4_mapped))
        if ip.sixtofour is not None or ip.teredo is not None:
            return False  # tunnelled addresses can embed private IPv4 targets
    return bool(
        ip.is_global
        and not ip.is_multicast
        and not ip.is_reserved
        and not ip.is_loopback
        and not ip.is_link_local
        and not ip.is_private
        and not ip.is_unspecified
    )


async def system_resolver(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return sorted({str(info[4][0]) for info in infos})


def _parse(url: str) -> tuple[str, str, int, str]:
    if len(url) > 2048:
        raise UnsafeURLError("URL too long")
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError as exc:
        raise UnsafeURLError(f"unparseable URL: {exc}") from exc
    scheme = parts.scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        raise UnsafeURLError(f"scheme '{scheme}' not allowed")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise UnsafeURLError("credentials in URL not allowed")
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        raise UnsafeURLError("missing host")
    port = port or (443 if scheme == "https" else 80)
    if port not in ALLOWED_PORTS:
        raise UnsafeURLError(f"port {port} not allowed")
    if host == "localhost" or host.endswith(_BLOCKED_HOST_SUFFIXES):
        raise UnsafeURLError("internal hostname not allowed")
    path_and_query = parts.path or "/"
    if parts.query:
        path_and_query += f"?{parts.query}"
    return scheme, host, port, path_and_query


def _looks_numeric_host(host: str) -> bool:
    # Rejects decimal/octal/hex IPv4 spellings like 2130706433, 0x7f.1, 0177.0.0.1,
    # which some resolvers silently accept.
    return all(part.isdigit() or part.lower().startswith("0x") for part in host.split(".") if part)


async def vet_url(url: str, resolver: Resolver = system_resolver) -> VettedURL:
    scheme, host, port, path_and_query = _parse(url)

    literal: ipaddress.IPv4Address | ipaddress.IPv6Address | None
    try:
        literal = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        literal = None
    if literal is None and _looks_numeric_host(host):
        raise UnsafeURLError("non-canonical numeric host not allowed")

    if literal is not None:
        addresses = [str(literal)]
    else:
        try:
            addresses = await resolver(host, port)
        except (OSError, UnicodeError) as exc:
            raise UnsafeURLError(f"DNS resolution failed for {host}") from exc
    if not addresses:
        raise UnsafeURLError(f"no addresses for {host}")
    blocked = [a for a in addresses if not is_public_ip(a)]
    if blocked:
        raise UnsafeURLError(f"{host} resolves to a non-public address")
    return VettedURL(url, scheme, host.strip("[]"), port, addresses[0], path_and_query)
