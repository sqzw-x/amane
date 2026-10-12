"""kin8 爬虫单元测试: 番号前缀判据与详情页 id 拼装."""

from unittest.mock import AsyncMock

import pytest

from amane.crawlers.http import HttpClient
from amane.crawlers.models import SearchQuery
from amane.crawlers.sites.kin8 import Kin8Crawler

_PAGE = "<html><head><title>金髪天國</title></head></html>"


@pytest.fixture
def web_client() -> AsyncMock:
    """替代 WebClient: 记录 get_text 调用并返回含 title 的页面."""
    client = AsyncMock()
    client.get_text.return_value = _PAGE
    return client


# 夹具的 ``expected_none`` 用例把 ``RequestError`` 当作没有 URL 处理, 因此「拒绝分支是否真的没发请求」
# 只能在单元测试里断言; 补零宽度与越界边界同样不值得为它各写一个 TOML.
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("number", "detail_id"),
    [
        ("KIN8-1", "0001"),
        ("KIN8-001", "0001"),
        ("KIN8-0001", "0001"),
        ("KIN8 0001", "0001"),
        ("KIN8TENGOKU-123", "0123"),
        # 编号越界或缺少本站前缀: 不命中, 且不发 HTTP
        ("KIN8-10000", None),
        ("KIN8TENGOKU-12345", None),
        ("KIN8-0", None),
        ("KIN8-0000", None),
        ("0001", None),
        ("HEYZO-1234", None),
        ("KIN8", None),
        ("KIN8-", None),
        ("", None),
    ],
)
async def test_search_detail_url(web_client: AsyncMock, number: str, detail_id: str | None) -> None:
    """命中时请求 4 位补零 id 的详情页; 编号越界或前缀不符时不命中且不发 HTTP."""
    crawler = Kin8Crawler(client=HttpClient(web=web_client))

    url = await crawler._search(SearchQuery(number=number))

    if detail_id is None:
        assert url is None
        web_client.get_text.assert_not_awaited()
        return
    expected = f"https://www.kin8tengoku.com/movie/{detail_id}"
    assert url == expected
    assert web_client.get_text.await_args.args == (expected,)
