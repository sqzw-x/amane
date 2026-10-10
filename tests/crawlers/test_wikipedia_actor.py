"""维基来源: 简介拼装与词条语言回退.

fixture 用例 (amane-testdata) 覆盖真实页面, 这里只覆盖规则与无法搬进 TOML 的部分:
章节白名单与层级归属、引用标记、长度上限、多语言回退与请求次数.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from amane.config import SiteConfig
from amane.crawlers.actor.sites.wikipedia import (
    WikipediaActorCrawler,
    _parse_wiki_page,
)
from amane.crawlers.http import HttpClient
from amane.net.errors import RequestError, SourceError
from amane.utils.text import normalize_long_text

_QID = "Q57538529"
_NAME = "伊藤舞雪"
_AV_DESCRIPTION = "日本のAV女優"
_JA_URL = "https://ja.wikipedia.org/wiki/%E4%BC%8A%E8%97%A4%E8%88%9E%E9%9B%AA"
# 中文词条走简体变体路径, 见 wikipedia._WIKI_VARIANTS
_ZH_URL = "https://zh.wikipedia.org/zh-cn/%E4%BC%8A%E8%97%A4%E8%88%9E%E9%9B%AA"


def _page(body: str) -> str:
    return f'<html><body><div class="mw-content-ltr mw-parser-output">{body}</div></body></html>'


# (id, parser-output 内文, 期望简介)
_OVERVIEW_CASES: list[tuple[str, str, str | None]] = [
    ("导语单段", "<p>伊藤舞雪は日本のAV女優。</p>", "伊藤舞雪は日本のAV女優。"),
    ("导语多段", "<p>第一段。</p><p>第二段。</p>", "第一段。\n\n第二段。"),
    ("人物章节取用", "<h2>人物</h2><p>人物段落。</p>", "人物段落。"),
    ("非白名单章节跳过", "<h2>作品</h2><p>作品段落。</p>", None),
    ("导语与章节拼接", "<p>导语。</p><h2>略歴</h2><p>略歴段落。</p>", "导语。\n\n略歴段落。"),
    (
        "子章节归属父章节",
        "<h2>経歴</h2><h3>2018年</h3><ul><li>出道。</li></ul><h2>作品</h2><p>作品段落。</p>",
        "出道。",
    ),
    ("章节结束于同级标题", "<h2>人物</h2><p>人物段落。</p><h2>出演</h2><p>出演段落。</p>", "人物段落。"),
    ("繁体简历", "<h2>簡歷</h2><p>簡歷段落。</p>", "簡歷段落。"),
    ("繁体经历", "<h2>經歷</h2><p>經歷段落。</p>", "經歷段落。"),
    ("简体经历", "<h2>经历</h2><p>经历段落。</p>", "经历段落。"),
    ("英文 career", "<h2>Career</h2><p>Career text.</p>", "Career text."),
    ("英文 filmography 跳过", "<h2>Filmography</h2><p>skip.</p>", None),
    ("模型章节", "<h2>プロフィール</h2><p>プロフィール段落。</p>", "プロフィール段落。"),
    (
        "引用标记清理",
        "<p>似鳥（にとり、2000年2月12日[ 3 ] - ）は、日本のAV女優[注 1][要出典]である[10]。</p>",
        "似鳥（にとり、2000年2月12日 - ）は、日本のAV女優である。",
    ),
    (
        "标题带编辑入口",
        '<h2>人物<span class="mw-editsection">[編集]</span></h2><p>人物段落。</p>',
        "人物段落。",
    ),
    ("信息框内段落跳过", '<table class="infobox"><tr><td><p>情報框文字。</p></td></tr></table><p>导语。</p>', "导语。"),
    ("列表整块取用", "<h2>人物</h2><ul><li>第一项</li><li>第二项</li></ul>", "第一项\n第二项"),
    ("嵌套列表不重复", "<h2>人物</h2><ul><li>父项<ul><li>子项</li></ul></li></ul>", "父项子项"),
    ("列表内段落不重复", "<h2>人物</h2><ul><li><p>条目段落。</p></li></ul>", "条目段落。"),
    ("空段落跳过", "<p></p><p>  </p><p>正文。</p>", "正文。"),
    # 文档文本不按子节点补空格, 否则「日本のAV女優」会写成带空格的形态.
    ("节点真实文本", "<p>日本の<a href='/a'>AV女優</a>。</p>", "日本のAV女優。"),
    ("无 parser-output", "", None),
]
_OVERVIEW_IDS = [case[0] for case in _OVERVIEW_CASES]


@pytest.mark.parametrize(("case_id", "body", "expected"), _OVERVIEW_CASES, ids=_OVERVIEW_IDS)
def test_overview_blocks(case_id: str, body: str, expected: str | None) -> None:
    page = _parse_wiki_page(_JA_URL, _page(body))
    assert page.overview == expected


@pytest.mark.parametrize(("case_id", "body", "expected"), _OVERVIEW_CASES, ids=_OVERVIEW_IDS)
def test_overview_is_normalize_fixed_point(case_id: str, body: str, expected: str | None) -> None:
    """拼装结果必须与 utils/text.py 的归一幂等, 否则落库写入会再改一次."""
    overview = _parse_wiki_page(_JA_URL, _page(body)).overview
    if overview is None:
        assert overview is expected
        return
    assert normalize_long_text(overview) == overview


# (id, 整页 HTML, 期望简介): 这三项不能只给 parser-output 内文, _OVERVIEW_CASES 的 _page() 表达不了.
_PAGE_CASES: list[tuple[str, str, str | None]] = [
    # 页面先出现一个空的 mw-parser-output (Parsoid 试验标记) 时不能只看第一个.
    (
        "取第二个 parser-output",
        '<html><body><div><div class="mw-parser-output"></div><div class="mw-parser-output"><p>正文。</p></div></div></body></html>',
        "正文。",
    ),
    ("缺 parser-output", "<html><body><p>裸正文。</p></body></html>", None),
]
_PAGE_IDS = [case[0] for case in _PAGE_CASES]


@pytest.mark.parametrize(("case_id", "html", "expected"), _PAGE_CASES, ids=_PAGE_IDS)
def test_overview_page_shapes(case_id: str, html: str, expected: str | None) -> None:
    assert _parse_wiki_page(_JA_URL, html).overview == expected


def test_overview_truncates_at_sentence_end() -> None:
    paragraph = "长句第一句。" * 400
    page = _parse_wiki_page(_JA_URL, _page(f"<h2>人物</h2><p>{paragraph}</p>"))
    assert page.overview is not None
    assert len(page.overview) <= 1500
    assert page.overview.endswith("…")
    assert page.overview[:-1].endswith("。")


def test_overview_drops_tail_below_minimum() -> None:
    """剩余空间放不下一句完整的话时不再续接残句."""
    blocks = f"<p>{'甲' * 31}</p>" + f"<p>{'乙' * 1460}</p>" + f"<p>{'丙' * 200}</p>"
    page = _parse_wiki_page(_JA_URL, _page(blocks))
    assert page.overview is not None
    assert "丙" not in page.overview
    assert len(page.overview) <= 1500


# (id, 信息框 HTML, 期望出身地, 期望生日)
_INFOBOX_CASES: list[tuple[str, str, str | None, str | None]] = [
    (
        "日文行",
        "<table class='infobox'><tr><th>出身地</th><td>東京都[2]</td></tr>"
        "<tr><th>生年月日</th><td>1994年8月26日</td></tr></table>",
        "東京都",
        "1994-08-26",
    ),
    (
        "中文行",
        "<table class='infobox'><tr><th>出生地</th><td>宮城縣</td></tr>"
        "<tr><th>出生</th><td>(1994-08-26) 1994年8月26日（32歲）</td></tr></table>",
        "宮城縣",
        "1994-08-26",
    ),
    ("非日期值", "<table class='infobox'><tr><th>出生地</th><td>不明</td></tr></table>", "不明", None),
    ("空信息框", "<table class='infobox'></table>", None, None),
]
_INFOBOX_IDS = [case[0] for case in _INFOBOX_CASES]


@pytest.mark.parametrize(("case_id", "infobox", "birthplace", "birthday"), _INFOBOX_CASES, ids=_INFOBOX_IDS)
def test_infobox_fields(case_id: str, infobox: str, birthplace: str | None, birthday: str | None) -> None:
    page = _parse_wiki_page(_JA_URL, _page(f"{infobox}<p>导语。</p>"))
    assert (page.birthplace, page.birthday) == (birthplace, birthday)


def _entity(*, labels: dict[str, str], sitelinks: dict[str, str], birthday: str | None = None) -> dict[str, Any]:
    claims: dict[str, Any] = {
        "P106": [{"mainsnak": {"datavalue": {"value": {"id": "Q1079215"}}}}],
    }
    if birthday is not None:
        claims["P569"] = [{"mainsnak": {"datavalue": {"value": {"time": f"+{birthday}T00:00:00Z"}}}}]
    return {
        "labels": {lang: {"value": value} for lang, value in labels.items()},
        "descriptions": {"ja": {"value": _AV_DESCRIPTION}},
        "sitelinks": {f"{lang}wiki": {"title": title} for lang, title in sitelinks.items()},
        "claims": claims,
    }


def _crawler(
    entity: dict[str, Any],
    pages: dict[str, str | SourceError],
    *,
    config: SiteConfig | None = None,
) -> tuple[WikipediaActorCrawler, list[str]]:
    """pages 按 URL 子串路由; 值为 SourceError 时该次请求失败."""
    web = AsyncMock()
    fetched: list[str] = []

    async def get_json(url: str, **kwargs: object) -> object:
        if "wbsearchentities" in url:
            return {"search": [{"id": _QID, "label": _NAME, "description": _AV_DESCRIPTION}]}
        return {"entities": {_QID: entity}}

    async def get_text(url: str, **kwargs: object) -> str:
        fetched.append(url)
        for pattern, payload in pages.items():
            if pattern in url:
                if isinstance(payload, SourceError):
                    raise payload
                return payload
        return "<html></html>"

    web.get_json.side_effect = get_json
    web.get_text.side_effect = get_text
    return WikipediaActorCrawler(client=HttpClient(web=web, browser=None), config=config), fetched


_SITELINKS = {"ja": "伊藤舞雪", "zh": "伊藤舞雪"}
_JA_PAGE = _page("<p>日本のAV女優。</p><h2>経歴</h2><p>日文经历。</p>")
_ZH_PAGE = _page("<p>日本AV女優。</p><h2>經歷</h2><p>中文经历。</p>")


@pytest.mark.asyncio
async def test_entry_language_default_prefers_zh() -> None:
    crawler, fetched = _crawler(
        _entity(labels={"ja": _NAME}, sitelinks=_SITELINKS),
        {
            "ja.wikipedia.org": _JA_PAGE,
            "zh.wikipedia.org": _ZH_PAGE,
        },
    )
    meta = await crawler.fetch(_NAME)
    assert meta is not None
    assert meta.source_url == _ZH_URL
    assert meta.overview == "日本AV女優。\n\n中文经历。"
    assert fetched == [_ZH_URL]


@pytest.mark.asyncio
async def test_entry_language_config_overrides_default() -> None:
    crawler, fetched = _crawler(
        _entity(labels={"ja": _NAME}, sitelinks=_SITELINKS),
        {"ja.wikipedia.org": _JA_PAGE, "zh.wikipedia.org": _ZH_PAGE},
        config=SiteConfig(languages=["ja", "zh"]),
    )
    meta = await crawler.fetch(_NAME)
    assert meta is not None
    assert meta.source_url == _JA_URL
    assert meta.overview == "日本のAV女優。\n\n日文经历。"
    assert fetched == [_JA_URL]


@pytest.mark.asyncio
async def test_entry_language_falls_back_when_preferred_has_no_text() -> None:
    crawler, fetched = _crawler(
        _entity(labels={"ja": _NAME}, sitelinks=_SITELINKS),
        {"ja.wikipedia.org": _JA_PAGE, "zh.wikipedia.org": _page("<table class='infobox'></table>")},
    )
    meta = await crawler.fetch(_NAME)
    assert meta is not None
    assert meta.source_url == _JA_URL
    assert fetched == [_ZH_URL, _JA_URL]


@pytest.mark.asyncio
async def test_entry_language_falls_back_on_request_error() -> None:
    crawler, fetched = _crawler(
        _entity(labels={"ja": _NAME}, sitelinks=_SITELINKS),
        {"ja.wikipedia.org": _JA_PAGE, "zh.wikipedia.org": RequestError(_ZH_URL, "boom")},
    )
    meta = await crawler.fetch(_NAME)
    assert meta is not None
    assert meta.source_url == _JA_URL
    assert fetched == [_ZH_URL, _JA_URL]


@pytest.mark.asyncio
async def test_entry_language_all_failed_propagates() -> None:
    crawler, _ = _crawler(
        _entity(labels={"ja": _NAME}, sitelinks=_SITELINKS),
        {"zh.wikipedia.org": RequestError(_ZH_URL, "boom"), "ja.wikipedia.org": RequestError(_JA_URL, "boom")},
    )
    with pytest.raises(SourceError):
        await crawler.fetch(_NAME)


@pytest.mark.asyncio
async def test_entry_language_keeps_infobox_of_textless_page() -> None:
    """全部词条都没有正文时仍取优先级最高的那一条, 信息框字段不可丢."""
    infobox = "<table class='infobox'><tr><th>出身地</th><td>東京都</td></tr></table>"
    crawler, _ = _crawler(
        _entity(labels={"ja": _NAME}, sitelinks={"zh": "伊藤舞雪"}),
        {"zh.wikipedia.org": _page(infobox)},
    )
    meta = await crawler.fetch(_NAME)
    assert meta is not None
    assert meta.source_url == _ZH_URL
    assert meta.overview is None
    assert meta.birthplace == "東京都"


@pytest.mark.asyncio
async def test_entry_language_narrowed_list_without_sitelink() -> None:
    """配置里只留没有词条的语言时落到 Wikidata 实体页地址, 不报错."""
    crawler, fetched = _crawler(
        _entity(labels={"ja": _NAME}, sitelinks={"ja": "伊藤舞雪"}),
        {"ja.wikipedia.org": _JA_PAGE},
        config=SiteConfig(languages=["en"]),
    )
    meta = await crawler.fetch(_NAME)
    assert meta is not None
    assert meta.source_url == f"https://www.wikidata.org/wiki/{_QID}"
    assert meta.overview is None
    assert fetched == []
