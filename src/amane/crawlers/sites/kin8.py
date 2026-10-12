"""官网番号形如 ``KIN8-0001`` / ``KIN8TENGOKU-0001``, 详情页 id 是其数字段补零到 4 位.

TPDB 的 kin8 条目 ``code`` 用同一个 id, 比对键取自 ``movie_id``, 补零规则只在本文件维护.
"""

from __future__ import annotations

import re

from parsel import Selector

from ...enums import SiteName
from ..base import Crawler, CrawlerProfile
from ..models import FetchOptions, MediaMetadata, SearchQuery, film_actors
from ..parsing import extract_all_texts, extract_text

# 只认本站前缀, 不认「任意番号里的首个数字串」: heyzo-1234 与 010115_001 都含数字, 拼出的是别人的 id.
_NUMBER_PREFIX = re.compile(r"^KIN8(?:TENGOKU)?[-_ ]?(?P<id>\d+)$")

# 官网 id 恰好 4 位补零: 未补零的 `/movie/1`、超出 4 位的 `/movie/12345` 与全零的 `/movie/0000` 都不存在.
_ID_DIGITS = 4

# 从详情页 URL 反解 id.
_MOVIE_PATH = re.compile(r"/movie/(?P<id>\d+)")

# `<title>` / `og:title` 的站点名后缀, 如 ``… | 金髪洋物専門 無修正動画サイト 金髪天國``.
_SITE_TITLE_SEPARATOR = "|"


def movie_id(number: str) -> str | None:
    """番号 → 详情页 id; 前缀不符或数字段不是有效站点编号时返回 ``None``."""
    match = _NUMBER_PREFIX.match(number.strip().upper())
    if match is None:
        return None
    value = match["id"].lstrip("0")
    if not value or len(value) > _ID_DIGITS:
        return None
    return value.zfill(_ID_DIGITS)


class Kin8Crawler(Crawler):
    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(name=SiteName.KIN8, base_url="https://www.kin8tengoku.com")

    async def _search(self, query: SearchQuery, options: FetchOptions | None = None) -> str | None:
        detail_id = movie_id(query.number)
        if detail_id is None:
            return None
        url = f"{self.base_url}/movie/{detail_id}"
        text = await self.client.get_html(url)
        if text and "title" in text.lower():
            return url
        return None

    async def _scrape(self, url: str, options: FetchOptions | None = None) -> MediaMetadata | None:
        """配信日在首屏是骨架屏占位, 水合后才由客户端填入, 因此不解析 `release`."""
        text = await self.client.get_html(url)
        if not text:
            return None

        html = Selector(text=text)

        title = extract_text(html, '//meta[@property="og:title"]/@content', "//title/text()")
        title = title.split(_SITE_TITLE_SEPARATOR, 1)[0].strip()
        if not title:
            return None

        actors = extract_all_texts(html, '//div[starts-with(@class, "Movie_Detail_actor_")]//a/text()')
        cover = extract_text(html, '//meta[@property="og:image"]/@content')
        tags = extract_all_texts(html, '//div[contains(@class, "Movie_Detail_actor-type")]//a/text()')

        id_match = _MOVIE_PATH.search(url)
        number = id_match["id"] if id_match else ""

        return MediaMetadata(
            number=number,
            title=title,
            actors=film_actors(actors),
            tags=tags,
            thumb_urls=[cover] if cover else [],
            source_url=url,
        )
