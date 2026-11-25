"""data18.com: 欧美场景的剧照集.

TPDB 只给一张封面, data18 的 Photoset 给整组剧照. 定位链路是
搜索作品名 → 结果里混排 movie 与 scene 两种链接 → 按演员确认场景 → 取剧照地址.

剧照地址形如 ``https://imb.dt18.com/<a>/<b>/<key>/NN.jpg`` (有的场景是 ``tNN.jpg``):
**前缀与文件名形态都因场景而异, 静态正文里没有** —— 必须在渲染后的页面点开 Photoset 那一组
才会出现, 因此本来源默认走浏览器 (``SiteConfig.use_browser=always``). ``<key>`` 是场景页里
``media/scenes/<a>/<b>/<key>-<slug>.jpg`` 的第三段, 不是 URL 里的 scene id.

站点有年龄门: 页面与搜索端点都要带 cookie, 缺了只回年龄确认页; 域名在部分网络下直连不通,
需要经 ``site_config.data18.proxy`` 出站. 检索结果分页且混排 movie / scene, 翻页靠 session
里的页码而不是 URL 参数. 同名系列的不同演员各自是独立场景, 场景页标题只写主演, 因此按
「任一演员出现在标题」确认.
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
_BASE = "https://www.data18.com"
#: 剧照大图: 前缀与文件名形态都因场景而异, 只能从渲染后的正文取.
_IMB_LARGE = re.compile(r"https://imb\.dt18\.com/(\d+)/(\d+)/(\d+)/(?P<t>t?)\d+\.jpg")
#: Photoset 那一组的入口: 同一页还有 Primary 与 Video Stills, 只点带 ``Photoset`` 的那块.
_PHOTOSET_TAB = "xpath=//div[contains(@class, 'changeimggallery') and contains(., 'Photoset')]"
#: 图组数量: ``Photoset 15`` / ``Video Stills 32``.
_PHOTOSET_COUNT = re.compile(r"Photoset\s*<b>(\d+)</b>", re.IGNORECASE)
_MOVIE_LINK = re.compile(r'href="(https://www\.data18\.com/movies/[^"#]+)"')
_SCENE_LINK = re.compile(r'href="(https://www\.data18\.com/scenes/\d+)"')
#: 检索结果页数: ``10 [pages: 2]``.
_PAGES = re.compile(r"\[pages:\s*(\d+)\]")
#: 每页条数; 翻页靠它换算 ``positionsearch``.
_PAGE_SIZE = 5


def search_key(title: str) -> str:
    """TPDB 的标题带演员名与场景序号; 检索只用作品名部分, 否则 data18 一条也命中不了."""
    head = title.split(" - ", 1)[0]
    return re.sub(r"\s*#\d+\s*$", "", head).strip()


def _result_links(text: str) -> list[str]:
    """检索结果是 movie 与 scene 的混排, 两种都要."""
    return [*_MOVIE_LINK.findall(text), *_SCENE_LINK.findall(text)]


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
        links = await self._search_links(title)
        return await self._match_scene(links, actors)

    async def _search_links(self, title: str) -> list[str]:
        """检索结果分页且混排 movie / scene.

        第一页用 ``back=undefined``; 后续页要先把页码写进 session (``pagesearch``), 再用
        ``positionsearch`` 取该页 —— 只改 ``page`` / ``b`` 参数服务端不认.
        """
        work = search_key(title)
        key = quote(work)
        keyfull = quote(re.sub(r"\s+", "--", work.lower()))
        first = await self._text(
            f"{self.base_url}/sys/live.php?index=&key={key}&key2={key}&keyfull={keyfull}&t=0&b=1&page=1&back=undefined"
        )
        if first is None:
            return []
        links = _result_links(first)
        found = _PAGES.search(first)
        pages = int(found.group(1)) if found is not None else 1
        for page in range(2, pages + 1):
            try:
                await self._text(f"{self.base_url}/sys/user.php?pagesearch={page}")
            except RequestError:
                break
            position = (page - 1) * _PAGE_SIZE + 1
            body = await self._text(
                f"{self.base_url}/sys/live.php?live=1&key={key}&key2={key}"
                f"&t=0&b=1&change=1&next=1&page=1&positionsearch={position}"
            )
            if body is None:
                break
            links.extend(_result_links(body))
        return list(dict.fromkeys(links))

    async def _match_scene(self, links: list[str], actors: list[str]) -> MediaMetadata | None:
        """先试检索结果里的 scene, 再顺着 movie 页展开; 每个场景页都要按演员确认."""
        wanted = [name.strip().casefold() for name in actors if name.strip()]
        for scene_url in [url for url in links if "/scenes/" in url]:
            scene = await self._scene_metadata(scene_url, wanted)
            if scene is not None:
                return scene
        for movie_url in [url for url in links if "/movies/" in url]:
            text = await self._text(movie_url)
            if text is None:
                continue
            for scene_url in dict.fromkeys(_SCENE_LINK.findall(text)):
                scene = await self._scene_metadata(scene_url, wanted)
                if scene is not None:
                    return scene
        return None

    async def _scene_metadata(self, scene_url: str, wanted: list[str]) -> MediaMetadata | None:
        try:
            text = await self.client.get_html(
                scene_url,
                cookies=self.cookies,
                headers={"Referer": f"{self.base_url}/"},
                click=_PHOTOSET_TAB,
                scroll=4,
            )
        except RequestError as exc:
            if exc.reason is FailureReason.NOT_FOUND:
                return None
            raise
        page_title = extract_text(Selector(text=text), "//title/text()") or ""
        haystack = page_title.casefold()
        # 场景页标题只写主演 (``{出演者} in {作品名}``), 要求全部演员都在会永远匹配不上.
        if not any(name in haystack for name in wanted):
            return None
        count = _PHOTOSET_COUNT.search(text)
        if count is None:
            return None
        total = int(count.group(1))
        if total <= 0:
            return None
        found = _IMB_LARGE.search(text)
        if found is None:
            # 正文里没有剧照地址: 前端未渲染出来, 不猜路径.
            self.logger.debug("photoset urls not rendered", url=scene_url)
            return None
        prefix = f"https://imb.dt18.com/{found[1]}/{found[2]}/{found[3]}/{found['t']}"
        return MediaMetadata(
            number=found[3],
            extrafanart=[f"{prefix}{index:02d}.jpg" for index in range(1, total + 1)],
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
