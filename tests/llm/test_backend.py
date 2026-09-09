"""httpx2 SOCKS extra: 构造 socks5 客户端不得因缺 socksio 失败."""

import httpx2 as httpx
import pytest


@pytest.mark.parametrize("proxy", ["socks5://127.0.0.1:1080", "socks5h://127.0.0.1:1080"])
@pytest.mark.asyncio
async def test_socks_proxy_client_constructs(proxy: str) -> None:
    async with httpx.AsyncClient(proxy=proxy):
        pass


def test_socks4_scheme_rejected() -> None:
    with pytest.raises(ValueError, match="Unknown scheme"):
        httpx.AsyncClient(proxy="socks4://127.0.0.1:1080")
