"""出站 URL 策略: 协议白名单与公网地址判定.

协议是全局底线 (``WebClient`` 的每个请求都校验): curl 本身还支持 ``file`` / ``gopher`` 等协议,
而调用方的 URL 可能来自第三方数据. 地址判定按调用点开启 — 资源下载把第三方 URL 带进出站通道,
是否允许私网由调用方决定 (见 ``docs/dev/api.md``).
"""

from __future__ import annotations

import asyncio
import socket
from dataclasses import dataclass
from ipaddress import IPv6Address, ip_address, ip_network
from urllib.parse import SplitResult, urlsplit

from .errors import FailureKind, RequestError, RequestFailure

_ALLOWED_SCHEMES = frozenset({"http", "https"})

_DEFAULT_PORTS = {"http": 80, "https": 443}

_FAKE_IP_NETWORKS = (ip_network("198.18.0.0/15"),)
"""fake-IP DNS 的默认段 (Clash / Mihomo / Surge). 代理环境下公网域名解析到这里, 按「非公网」拒绝会让
外链图片全部失败; 该段在普通网络上不可路由, 放行不扩大可达面."""


def _blocked(url: str, message: str) -> RequestError:
    return RequestError(url, RequestFailure(kind=FailureKind.UNEXPECTED, message=message))


def _split(url: str) -> SplitResult:
    try:
        return urlsplit(url)
    except ValueError as exc:
        raise _blocked(url, f"URL 无法解析: {exc}") from exc


def ensure_http_scheme(url: str) -> None:
    """出站 URL 只接受 http(s): 其余协议不进入 curl."""
    scheme = _split(url).scheme.lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise _blocked(url, f"拒绝的协议: {scheme or '(空)'}")


def is_public_address(address: str) -> bool:
    """地址能否作为出站目标. IPv4 映射的 IPv6 按 IPv4 判定, 否则映射地址会绕过私网检查."""
    ip = ip_address(address)
    if isinstance(ip, IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if ip.is_global:
        return True
    return any(ip in network for network in _FAKE_IP_NETWORKS)


async def resolve_addresses(host: str, port: int) -> set[str]:
    """解析 host 的全部地址. 模块级函数便于测试替换, 策略对象自身不发起 I/O."""
    infos = await asyncio.to_thread(socket.getaddrinfo, host, port, type=socket.SOCK_STREAM)
    return {str(info[4][0]) for info in infos}


def _looks_numeric(host: str) -> bool:
    """纯数值写法 (十进制 / 八进制 / 短写 / 十六进制)."""
    if host.lower().startswith("0x"):
        return True
    return bool(host) and all(ch.isdigit() or ch == "." for ch in host)


def _is_canonical_ip(host: str) -> bool:
    try:
        ip_address(host)
    except ValueError:
        return False
    return True


@dataclass(frozen=True, slots=True)
class EgressPolicy:
    """``allow_private=False`` 时解析 URL 主机并拒绝任何非公网地址.

    逐跳复查由 ``WebClient.request`` 负责: 每一跳都调用 ``ensure_allowed``, 重定向不能把一个
    已校验的 URL 带到内网. 代价是校验与 curl 的解析之间存在时间窗 (DNS rebinding), 因此只用于
    入口已知可控的调用点.
    """

    allow_private: bool = False
    max_redirects: int = 5

    async def ensure_allowed(self, url: str) -> None:
        ensure_http_scheme(url)
        if self.allow_private:
            return

        parsed = _split(url)
        host = parsed.hostname
        if not host:
            raise _blocked(url, "URL 缺少主机名")
        # curl 自己解析数值主机: 0177.0.0.1 是八进制、2130706433 是十进制, 与 getaddrinfo 的解释不同,
        # 只按解析结果判定会漏掉这些写法. 因此数值形态必须是规范写法.
        if _looks_numeric(host) and not _is_canonical_ip(host):
            raise _blocked(url, f"非规范的数值主机: {host}")
        try:
            port = parsed.port or _DEFAULT_PORTS[parsed.scheme.lower()]
        except ValueError as exc:
            raise _blocked(url, f"端口非法: {exc}") from exc

        try:
            addresses = await resolve_addresses(host, port)
        except (OSError, UnicodeError) as exc:
            raise _blocked(url, f"主机解析失败: {exc}") from exc
        if not addresses:
            raise _blocked(url, "主机解析无结果")

        # 任一地址落在私网就整体拒绝: 双栈主机可能同时给出公网与内网地址, 放行等于让 curl 自己选.
        for address in addresses:
            if not is_public_address(address):
                raise _blocked(url, f"地址不在公网范围: {address}")
