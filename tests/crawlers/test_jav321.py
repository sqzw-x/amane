"""jav321 的检索入口是 ``POST /search`` 的 ``sn`` 表单, 详情地址是站内 content id.

线上实录: ``/video/<番号>`` 已返回 404; 表单检索命中时 301 到 ``/video/<content id>``, 未命中停留在检索页,
相近番号也会被重定向到别的详情页. 因此检索结果必须按详情页的 品番 确认.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from amane.crawlers.models import SearchQuery
from amane.crawlers.sites.jav321 import Jav321Crawler

from .driven import http_client

DETAIL_URL = "https://www.jav321.com/video/h_422sero00025"

# 站点 header 的语言切换链接指回当前详情页.
HEADER = """<ul class="dropdown-menu" role="menu">
<li><a href="//en.jav321.com/video/h_422sero00025">English</a></li>
<li><a href="//www.jav321.com/video/h_422sero00025">简体中文</a></li>
</ul>"""

DETAIL_PAGE = f"""<html><body>{HEADER}
<div class="panel panel-info">
  <div class="panel-heading"><h3>秋月玲奈といく！混浴露天バスツアー 秋月玲奈 <small>sero-025 秋月玲奈</small></h3></div>
  <div class="panel-body">
    <div class="row">
      <div class="col-md-3"><img class="img-responsive" src="http://pics.dmm.co.jp/digital/video/h_422sero00025/h_422sero00025ps.jpg"></div>
      <div class="col-md-9"><b>出演者</b>: <a href="/star/1003566/1">秋月玲奈</a> &nbsp; <br><b>メーカー</b>: <a href="/company/EROTICA/1">EROTICA</a><br><b>ジャンル</b>:  <a href="/genre/5022/1">3P・4P</a> <a href="/genre/4005/1">乱交</a><br><b>品番</b>: sero-025<br><b>配信開始日</b>: 2010-06-25<br><b>収録時間</b>: 120 minutes<br><b>シリーズ</b>: <a href="/series/76891/1">○○といく！混浴露天バスツアー</a></div>
    </div>
    <div class="row"><div class="col-md-12">EROTICA版「バスツアー」が登場！！</div></div>
  </div>
</div>
</body></html>"""

MISS_PAGE = (
    """<html><body><div class="alert alert-danger" role="alert">AVが見つかりませんでした。</div></body></html>"""
)

SEARCH_CASES: list[tuple[str, str, str | None]] = [
    # 命中: 表单检索把站点重定向到详情页
    ("SERO-025", DETAIL_PAGE, DETAIL_URL),
    # 未命中: 停留在检索页
    ("ABCD-999", MISS_PAGE, None),
    # 站点把相近番号重定向到别的详情页: 按 品番 确认后拒绝
    ("SSIS-497", DETAIL_PAGE, None),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("query", "body", "expected"), SEARCH_CASES)
async def test_search_confirms_number(query: str, body: str, expected: str | None) -> None:
    web = AsyncMock()
    web.post_text.return_value = body
    crawler = Jav321Crawler(client=http_client(web))

    assert await crawler._search(SearchQuery(query)) == expected
    call = web.post_text.await_args
    assert call.args[0] == "https://www.jav321.com/search"
    assert call.kwargs["data"] == {"sn": query}


@pytest.mark.asyncio
async def test_scrape_parses_info_fields() -> None:
    web = AsyncMock()
    web.get_text.return_value = DETAIL_PAGE
    crawler = Jav321Crawler(client=http_client(web))

    meta = await crawler._scrape(DETAIL_URL)

    assert meta is not None
    assert meta.number == "sero-025"
    assert meta.title == "秋月玲奈といく！混浴露天バスツアー 秋月玲奈"
    assert [actor.name for actor in meta.actors] == ["秋月玲奈"]
    assert meta.studio == "EROTICA"
    assert meta.release == "2010-06-25"
    assert meta.runtime == 120
    assert meta.tags == ["3P・4P", "乱交"]
    assert meta.series == "○○といく！混浴露天バスツアー"
    assert meta.plot == "EROTICA版「バスツアー」が登場！！"
    assert meta.thumb_urls == ["http://pics.dmm.co.jp/digital/video/h_422sero00025/h_422sero00025ps.jpg"]
    assert meta.source_url == DETAIL_URL
