"""famedigital.com (Gamma 平台): 场景预告片.

Fame Digital / AdultTime / DevilsFilm 等厂牌共用 Gamma 的 CMS, 场景数据放在 Algolia 的
``all_scenes`` 索引里. TPDB 只给一张封面, 预告片要在这里取.

索引按 ``clip_id`` 能精确命中, 但 TPDB 已定值的字段里没有 clip_id, 因此用它的标题检索, 再按
**演员集合**确认同一条目 — 同名系列的不同演员各自是独立条目, 只按标题取会错配.

Algolia 的 apiKey 带 ``validUntil`` (数天内到期), 因此每次现取首页的 ``window.env.api.algolia``,
不写死. 剧照集在 ``all_photosets`` 索引里, 但原图走 ``signedPhotoset``, 会员态才有, 这里只取预告片.
"""

from __future__ import annotations

import re
from typing import Any, override

from ...enums import SiteName
from ...plugins.models import SourceTrait
from ..base import Crawler, CrawlerProfile
from ..models import FetchOptions, MediaMetadata, SearchQuery

#: 首页内联的 Algolia 凭据; 键名固定, 值随站点发布轮换.
_APP_ID = re.compile(r'"applicationID":"(?P<value>[^"]+)"')
_API_KEY = re.compile(r'"apiKey":"(?P<value>[^"]+)"')
#: 预告片按画质降级取第一个存在的档位.
_QUALITY_ORDER = ("1080p", "720p", "540p", "480p", "360p", "240p", "160p")
_HITS_PER_PAGE = 10


class GammaCrawler(Crawler):
    """依赖 TPDB 已定值的标题与演员; 无前序结果时不猜."""

    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(
            name=SiteName.GAMMA,
            base_url="https://www.famedigital.com",
            traits=frozenset({SourceTrait.NEEDS_PARTIAL}),
        )

    @override
    async def fetch(self, query: SearchQuery, options: FetchOptions | None = None) -> MediaMetadata | None:
        title = (query.partial_result.title or "").strip() if query.partial_result else ""
        actors = [actor.name for actor in query.partial_result.actors] if query.partial_result else []
        if not title or not actors:
            return None
        credentials = await self._credentials()
        if credentials is None:
            return None
        scene = await self._search_scene(*credentials, title, actors)
        if scene is None:
            return None
        trailers = _trailer_urls(scene.get("trailers"))
        if not trailers:
            return None
        return MediaMetadata(
            number=str(scene.get("clip_id") or ""),
            title=scene.get("title") or None,
            trailer_urls=trailers,
            source_url=_scene_url(scene),
        )

    async def _credentials(self) -> tuple[str, str] | None:
        """现取首页里的 Algolia 凭据; 站点改版取不到时按未命中处理."""
        try:
            text = await self.client.get_text(self.base_url + "/")
        except Exception:
            return None
        app_id = _APP_ID.search(text)
        api_key = _API_KEY.search(text)
        if app_id is None or api_key is None:
            self.logger.warning("algolia credentials not found")
            return None
        return app_id["value"], api_key["value"]

    async def _search_scene(self, app_id: str, api_key: str, title: str, actors: list[str]) -> dict[str, Any] | None:
        payload = await self.client.post_json(
            f"https://{app_id}-dsn.algolia.net/1/indexes/all_scenes/query",
            json={"query": title, "hitsPerPage": _HITS_PER_PAGE},
            headers={
                "X-Algolia-Application-Id": app_id,
                "X-Algolia-API-Key": api_key,
                # Algolia 的 secured key 绑定来源, 缺该头一律 403.
                "Referer": f"{self.base_url}/",
            },
        )
        hits = payload.get("hits") if isinstance(payload, dict) else None
        return pick_scene(hits, actors)

    async def _search(self, query: SearchQuery, options: FetchOptions | None = None) -> str | None:
        return None

    async def _scrape(self, url: str, options: FetchOptions | None = None) -> MediaMetadata | None:
        return None


def pick_scene(hits: object, actors: list[str]) -> dict[str, Any] | None:
    """按演员集合确认同一部作品; 集合不等视为别的条目, 不取首条."""
    wanted = {name.strip().casefold() for name in actors if name.strip()}
    if not wanted or not isinstance(hits, list):
        return None
    for hit in hits:
        if not isinstance(hit, dict):
            continue
        found = {
            str(item.get("name") or "").strip().casefold()
            for item in (hit.get("actors") or [])
            if isinstance(item, dict)
        }
        if found == wanted:
            return hit
    return None


def _trailer_urls(trailers: object) -> list[str]:
    """按画质降级取第一个存在的档位; 结构不符按没有处理."""
    if not isinstance(trailers, dict):
        return []
    for quality in _QUALITY_ORDER:
        url = trailers.get(quality)
        if isinstance(url, str) and url:
            return [url]
    return []


def _scene_url(scene: dict[str, Any]) -> str | None:
    """站内场景页; 站点用 ``sitename`` + ``url_title`` 拼, 缺任一段则不拼."""
    sitename = scene.get("sitename")
    url_title = scene.get("url_title")
    clip_id = scene.get("clip_id")
    if not sitename or not url_title or not clip_id:
        return None
    return f"https://www.famedigital.com/en/video/{sitename}/{url_title}/{clip_id}"


__all__ = ["GammaCrawler"]
