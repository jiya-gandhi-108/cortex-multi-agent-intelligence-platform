import pytest

from app.tools.url_safety import UnsafeURLError, domain_of, is_public_ip, normalize_url, vet_url


def resolver_for(mapping: dict[str, list[str]]):
    async def resolve(host: str, port: int) -> list[str]:
        if host not in mapping:
            raise OSError("NXDOMAIN")
        return mapping[host]

    return resolve


PUBLIC = resolver_for({"example.com": ["93.184.216.34"], "dual.example": ["2606:4700::1111"]})


class TestNormalize:
    @pytest.mark.parametrize(
        ("a", "b"),
        [
            ("https://www.Example.com/Path/?b=2&a=1#frag", "https://example.com/Path?a=1&b=2"),
            ("https://example.com:443/x/", "https://example.com/x"),
            ("http://example.com", "http://example.com/"),
            ("https://example.com/p?utm_source=x&id=7&gclid=abc", "https://example.com/p?id=7"),
        ],
    )
    def test_equivalent_urls_collapse(self, a: str, b: str) -> None:
        assert normalize_url(a) == normalize_url(b) == b

    def test_domain_of_strips_www(self) -> None:
        assert domain_of("https://WWW.Practo.com/a") == "practo.com"


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",
        "10.0.0.5",
        "172.16.3.4",
        "192.168.1.1",
        "169.254.169.254",  # cloud metadata
        "100.64.0.1",  # CGNAT
        "0.0.0.0",
        "224.0.0.1",
        "::1",
        "fc00::1",
        "fe80::1",
        "::ffff:127.0.0.1",  # IPv4-mapped loopback
        "2002:7f00:1::",  # 6to4 wrapping 127.0.0.1
        "240.0.0.1",
    ],
)
def test_non_public_ips_rejected(ip: str) -> None:
    assert not is_public_ip(ip)


def test_public_ips_accepted() -> None:
    assert is_public_ip("93.184.216.34")
    assert is_public_ip("2606:4700::1111")


class TestVet:
    async def test_public_host_passes_and_is_pinned(self) -> None:
        v = await vet_url("https://example.com/page?q=1", PUBLIC)
        assert v.ip == "93.184.216.34" and v.host == "example.com" and v.port == 443
        assert v.path_and_query == "/page?q=1"

    @pytest.mark.parametrize(
        "url",
        [
            "file:///etc/passwd",
            "ftp://example.com/x",
            "gopher://example.com",
            "javascript:alert(1)",
            "http://user:pass@example.com/",
            "http://example.com@evil.com/",
            "http://localhost/admin",
            "http://metadata.google.internal/",
            "http://printer.local/",
            "http://127.0.0.1/",
            "http://169.254.169.254/latest/meta-data/",
            "http://[::1]/",
            "http://2130706433/",  # decimal 127.0.0.1
            "http://0x7f.0.0.1/",
            "http://0177.0.0.1/",
            "http://example.com:22/",
            "https://" + "a" * 3000 + ".com",
        ],
    )
    async def test_dangerous_urls_rejected(self, url: str) -> None:
        with pytest.raises(UnsafeURLError):
            await vet_url(url, PUBLIC)

    async def test_hostname_resolving_inward_rejected(self) -> None:
        rebind = resolver_for({"evil.example": ["93.184.216.34", "10.0.0.1"]})
        with pytest.raises(UnsafeURLError, match="non-public"):
            await vet_url("https://evil.example/", rebind)

    async def test_dns_failure_rejected(self) -> None:
        with pytest.raises(UnsafeURLError, match="DNS"):
            await vet_url("https://nope.example/", PUBLIC)
