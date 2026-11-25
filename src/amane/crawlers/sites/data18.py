"""data18.com: 欧美场景的剧照集.

TPDB 只给一张封面, data18 的 Photoset 给整组剧照. 定位链路是
搜索标题 → movie 页 → 场景列表 → 按演员确认场景 → 取剧照地址:

    剧照      https://imb.dt18.com/314/128/{scene_key}/tNN.jpg   (两位补零)
    缩略图    https://imb.dt18.com/314/128/{scene_key}/th8_2/NN.jpg

``314/128`` 是站点级常量 (换任一段都是 404); ``scene_key`` 是场景页里
``media/scenes/<a>/<b>/<key>-<slug>.jpg`` 的第三段, 不是 URL 里的 scene id.

站点有年龄门: 页面与搜索端点都必须带 cookie, 缺了只回年龄确认页. 域名在部分网络下直连不通,
需要经 ``site_config.data18.proxy`` 出站. 同名系列的不同演员各自是独立场景, 只按标题取会错配,
因此场景页必须按演员确认.
"""

from __future__ import annotations

import re
from typing import override
from urllib.parse import quote

from parsel import Selector

from ...enums import SiteName
from ...net.errors import FailureReason, RequestError
from ...plugins.models import SourceTrait
from ..base import Crawler, CrawlerProfile
from ..models import FetchOptions, MediaMetadata, SearchQuery
from ..parsing import extract_text

#: 年龄门 cookie: 只表示「已确认成年」, 与账号无关.
_COOKIES = {
    "data_user_navigation": "1",
    "data_user_captcha": "1",
    "data_user_enter": "UNK-en-1",
    "data_user_nav_custom": "t%3D1%26b%3D1%26html%3Dindex",
}
#: 剧照 CDN 的站点级前缀.
_IMAGE_BASE = "https://imb.dt18.com/314/128"
_BASE = "https://www.data18.com"
#: 场景页里的大图地址: ``media/scenes/<a>/<b>/<key>-<slug>.jpg``.
_SCENE_KEY = re.compile(r"media/scenes/\d+/\d+/(\d+)-[a-z0-9\-]+\.jpg", re.IGNORECASE)
#: 图组数量: ``Photoset 15`` / ``Video Stills 32``.
_PHOTOSET_COUNT = re.compile(r"Photoset\s*<b>(\d+)</b>", re.IGNORECASE)
_MOVIE_LINK = re.compile(r'href="(https://www\.data18\.com/movies/[^"#]+)"')
_SCENE_LINK = re.compile(r'href="(https://www\.data18\.com/scenes/\d+)"')


def search_key(title: str) -> str:
    """TPDB 的标题带演员名与场景序号; 检索只用作品名部分, 否则 data18 一条也命中不了."""
    head = title.split(" - ", 1)[0]
    return re.sub(r"\s*#\d+\s*$", "", head).strip()


class Data18Crawler(Crawler):
    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(
            name=SiteName.DATA18,
            base_url=_BASE,
            # 剧照与缩略图在 img CDN 上; 列出它们, 站点级代理才会覆盖到这些 host.
            urls=["https://imb.dt18.com", "https://cdn.dt18.com"],
            cookies=dict(_COOKIES),
            # 图片 CDN 校验 Referer: 裸请求一律 403, 同源引用即可放行.
            same_origin_referer=True,
            traits=frozenset({SourceTrait.NEEDS_PARTIAL}),
        )

    @override
    async def fetch(self, query: SearchQuery, options: FetchOptions | None = None) -> MediaMetadata | None:
        partial = query.partial_result
        if partial is None:
            return None
        title = (partial.title or "").strip()
        actors = [actor.name for actor in partial.actors]
        if not title or not actors:
            return None
        for movie_url in await self._search_movies(title):
            scene = await self._match_scene(movie_url, actors)
            if scene is not None:
                return scene
        return None

    async def _search_movies(self, title: str) -> list[str]:
        """检索页给出的是 movie 级结果; 同名系列会有多条, 逐条尝试."""
        work = search_key(title)
        key = quote(work)
        keyfull = quote(re.sub(r"\s+", "--", work.lower()))
        url = (
            f"{self.base_url}/sys/live.php?index=&key={key}&key2={key}&keyfull={keyfull}&t=0&b=1&page=1&back=undefined"
        )
        text = await self._text(url)
        if text is None:
            return []
        return list(dict.fromkeys(_MOVIE_LINK.findall(text)))

    async def _match_scene(self, movie_url: str, actors: list[str]) -> MediaMetadata | None:
        """movie 页列出所属场景; 逐个场景页按演员名确认, 命中即返回."""
        text = await self._text(movie_url)
        if text is None:
            return None
        wanted = [name.strip().casefold() for name in actors if name.strip()]
        for scene_url in dict.fromkeys(_SCENE_LINK.findall(text)):
            scene = await self._scene_metadata(scene_url, wanted)
            if scene is not None:
                return scene
        return None

    async def _scene_metadata(self, scene_url: str, wanted: list[str]) -> MediaMetadata | None:
        text = await self._text(scene_url)
        if text is None:
            return None
        page_title = extract_text(Selector(text=text), "//title/text()") or ""
        haystack = page_title.casefold()
        # 场景页标题只写主演 (``{出演者} in {作品名}``), 要求全部演员都在会永远匹配不上.
        if not any(name in haystack for name in wanted):
            return None
        key = _SCENE_KEY.search(text)
        count = _PHOTOSET_COUNT.search(text)
        if key is None or count is None:
            return None
        total = int(count.group(1))
        if total <= 0:
            return None
        base = f"{_IMAGE_BASE}/{key.group(1)}"
        return MediaMetadata(
            number=key.group(1),
            extrafanart=[f"{base}/t{index:02d}.jpg" for index in range(1, total + 1)],
            source_url=scene_url,
        )

    async def _text(self, url: str) -> str | None:
        try:
            return await self.client.get_text(url, cookies=self.cookies, headers={"Referer": f"{self.base_url}/"})
        except RequestError as exc:
            # 未收录是未命中, 其余失败照旧上报.
            if exc.reason is FailureReason.NOT_FOUND:
                return None
            raise

    async def _search(self, query: SearchQuery, options: FetchOptions | None = None) -> str | None:
        return None

    async def _scrape(self, url: str, options: FetchOptions | None = None) -> MediaMetadata | None:
        return None


__all__ = ["Data18Crawler"]
