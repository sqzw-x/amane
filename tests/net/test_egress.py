"""出站策略: 协议白名单与公网地址判定. 用例不访问网络: 地址全部是字面量或替换解析."""

import pytest

from amane.net.egress import EgressPolicy, is_public_address
from amane.net.errors import RequestError


class TestScheme:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "url",
        [
            "file:///etc/passwd",
            "gopher://127.0.0.1:6379/_INFO",
            "ftp://example.com/a.jpg",
            "data:text/html,<script>alert(1)</script>",
            "/api/resources/abcdef",
            "example.com/a.jpg",
            "http://[::1/a.jpg",
            "",
        ],
    )
    async def test_rejects_non_http(self, url: str):
        with pytest.raises(RequestError):
            await EgressPolicy(allow_private=True).ensure_allowed(url)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("url", ["http://example.com/a.jpg", "https://example.com/a.jpg"])
    async def test_accepts_http(self, url: str):
        await EgressPolicy(allow_private=True).ensure_allowed(url)


class TestAddress:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1:8000/a.jpg",
            "http://localhost/a.jpg",
            "http://10.1.2.3/a.jpg",
            "http://172.16.0.1/a.jpg",
            "http://192.168.1.1/a.jpg",
            "http://169.254.169.254/latest/meta-data/",
            "http://100.64.0.1/a.jpg",
            "http://0.0.0.0/a.jpg",
            "http://[::1]/a.jpg",
            "http://[::ffff:127.0.0.1]/a.jpg",
            # 数值写法: curl 把前两种分别按十进制 / 八进制解析, 与 getaddrinfo 的结论不同.
            "http://2130706433/a.jpg",
            "http://0177.0.0.1/a.jpg",
            "http://127.1/a.jpg",
            "http://0x7f.1/a.jpg",
        ],
    )
    async def test_rejects_non_public(self, url: str):
        with pytest.raises(RequestError):
            await EgressPolicy().ensure_allowed(url)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("url", ["http://8.8.8.8/a.jpg", "https://93.184.216.34:8443/a.jpg"])
    async def test_allows_public_literal(self, url: str):
        await EgressPolicy().ensure_allowed(url)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("url", ["http://example.com:99999/a.jpg", "https://example.com:-1/a.jpg"])
    async def test_rejects_invalid_port(self, url: str):
        with pytest.raises(RequestError):
            await EgressPolicy().ensure_allowed(url)

    @pytest.mark.asyncio
    async def test_rejects_host_with_any_private_record(self, monkeypatch: pytest.MonkeyPatch):
        """双栈主机同时给出公网与内网地址时整体拒绝: 放行等于让 curl 自己选连接目标."""

        async def _resolve(host: str, port: int) -> set[str]:
            return {"93.184.216.34", "10.0.0.9"}

        monkeypatch.setattr("amane.net.egress.resolve_addresses", _resolve)

        with pytest.raises(RequestError):
            await EgressPolicy().ensure_allowed("https://dual.example/a.jpg")

    @pytest.mark.asyncio
    async def test_resolution_error_blocks(self, monkeypatch: pytest.MonkeyPatch):
        async def _resolve(host: str, port: int) -> set[str]:
            raise OSError("resolver down")

        monkeypatch.setattr("amane.net.egress.resolve_addresses", _resolve)

        with pytest.raises(RequestError):
            await EgressPolicy().ensure_allowed("https://nx.example/a.jpg")

    @pytest.mark.asyncio
    async def test_empty_resolution_blocks(self, monkeypatch: pytest.MonkeyPatch):
        async def _resolve(host: str, port: int) -> set[str]:
            return set()

        monkeypatch.setattr("amane.net.egress.resolve_addresses", _resolve)

        with pytest.raises(RequestError):
            await EgressPolicy().ensure_allowed("https://empty.example/a.jpg")

    @pytest.mark.asyncio
    async def test_allow_private_skips_resolution(self, monkeypatch: pytest.MonkeyPatch):
        calls: list[str] = []

        async def _resolve(host: str, port: int) -> set[str]:
            calls.append(host)
            return {"127.0.0.1"}

        monkeypatch.setattr("amane.net.egress.resolve_addresses", _resolve)

        await EgressPolicy(allow_private=True).ensure_allowed("http://127.0.0.1/a.jpg")

        assert calls == []


class TestPublicAddress:
    @pytest.mark.parametrize(
        ("address", "expected"),
        [
            ("8.8.8.8", True),
            ("2606:4700:4700::1111", True),
            ("127.0.0.1", False),
            ("10.0.0.1", False),
            ("172.16.0.1", False),
            ("192.168.0.1", False),
            ("169.254.169.254", False),
            ("100.64.0.1", False),
            ("0.0.0.0", False),
            # fake-IP DNS 的解析段: 不可路由, 但代理环境下是公网域名的代表地址
            ("198.18.0.1", True),
            ("198.19.0.1", True),
            ("::1", False),
            ("fc00::1", False),
            ("::ffff:127.0.0.1", False),
            ("::ffff:8.8.8.8", True),
        ],
    )
    def test_predicate(self, address: str, expected: bool):
        assert is_public_address(address) is expected
