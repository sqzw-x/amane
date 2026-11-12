"""出站策略: 协议白名单与内网地址判定. 用例不发起 HTTP 请求: 地址取自字面量或本机 hosts, 解析可替换."""

import pytest

from amane.net.egress import EgressPolicy, is_internal_address
from amane.net.errors import EgressRejected, RequestError


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
            "http://[64:ff9b::7f00:1]/a.jpg",
            # 数值写法: curl 把前两种分别按十进制 / 八进制解析, 与 getaddrinfo 的结论不同.
            "http://2130706433/a.jpg",
            "http://0177.0.0.1/a.jpg",
            "http://127.1/a.jpg",
            "http://0x7f.1/a.jpg",
        ],
    )
    async def test_rejects_internal(self, url: str):
        with pytest.raises(EgressRejected):
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
    async def test_resolution_failure_is_not_a_rejection(self, monkeypatch: pytest.MonkeyPatch):
        """解析失败属于网络故障: 调用方不能据此告诉用户地址不合规."""

        async def _resolve(host: str, port: int) -> set[str]:
            raise OSError("resolver down")

        monkeypatch.setattr("amane.net.egress.resolve_addresses", _resolve)

        with pytest.raises(RequestError) as excinfo:
            await EgressPolicy().ensure_allowed("https://nx.example/a.jpg")

        assert not isinstance(excinfo.value, EgressRejected)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("address", ["198.18.18.132", "2001:2::8", "2001::80f2:f51d"])
    async def test_unroutable_resolution_allowed(self, monkeypatch: pytest.MonkeyPatch, address: str):
        """fake-IP DNS 与 DNS 封堵应答给出的地址不指向内网: 放行, 连不通由真实连接报错."""

        async def _resolve(host: str, port: int) -> set[str]:
            return {address}

        monkeypatch.setattr("amane.net.egress.resolve_addresses", _resolve)

        await EgressPolicy().ensure_allowed("https://pics.example/a.jpg")

    @pytest.mark.asyncio
    async def test_allow_private_skips_resolution(self, monkeypatch: pytest.MonkeyPatch):
        calls: list[str] = []

        async def _resolve(host: str, port: int) -> set[str]:
            calls.append(host)
            return {"127.0.0.1"}

        monkeypatch.setattr("amane.net.egress.resolve_addresses", _resolve)

        await EgressPolicy(allow_private=True).ensure_allowed("http://127.0.0.1/a.jpg")

        assert calls == []


class TestInternalAddress:
    @pytest.mark.parametrize(
        ("address", "expected"),
        [
            ("8.8.8.8", False),
            ("2606:4700:4700::1111", False),
            ("127.0.0.1", True),
            ("10.0.0.1", True),
            ("172.16.0.1", True),
            ("192.168.0.1", True),
            ("169.254.169.254", True),
            ("100.64.0.1", True),
            ("0.0.0.0", True),
            ("::", True),
            ("::1", True),
            ("fc00::1", True),
            ("fe80::1%en0", True),
            # fake-IP DNS 与 DNS 封堵应答落在特殊用途段, 但不指向内网
            ("198.18.0.1", False),
            ("2001:2::8", False),
            ("2001::80f2:f51d", False),
            ("2001:db8::1", False),
            # IPv4 映射与 NAT64 的连接到达内嵌地址, 按内嵌地址判定
            ("::ffff:127.0.0.1", True),
            ("::ffff:8.8.8.8", False),
            ("64:ff9b::a00:1", True),
            ("64:ff9b::7f00:1", True),
            ("64:ff9b::808:808", False),
            # 本地 NAT64 前缀的内嵌位置不固定, 整段拒绝
            ("64:ff9b:1::a00:1", True),
            ("64:ff9b:1:ffff:ffff:ffff:ffff:ffff", True),
            ("64:ff9b:2::1", False),
            # 6to4 内嵌的是隧道端点, 协议 41 的包发往该地址, 但到不了其上的 TCP 服务
            ("2002:a00:1::", False),
        ],
    )
    def test_predicate(self, address: str, expected: bool):
        assert is_internal_address(address) is expected

    @pytest.mark.parametrize("address", ["", "localhost", "10.0.0.256", "2001:::1"])
    def test_rejects_non_address(self, address: str):
        with pytest.raises(ValueError, match="does not appear to be an IPv4 or IPv6 address"):
            is_internal_address(address)
