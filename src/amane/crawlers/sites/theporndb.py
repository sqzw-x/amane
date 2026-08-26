"""ThePornDB 爬虫 - Stash GraphQL API + oshash 匹配."""

from typing import TYPE_CHECKING

from ...enums import SiteName
from ..base import Crawler, CrawlerProfile
from ..models import FetchOptions, MediaMetadata, SearchQuery

if TYPE_CHECKING:
    from ...parsing.file_info import ContentType

# GraphQL 查询模板 - searchScene 返回完整的场景数据
_SEARCH_QUERY = """
query Search($term: String!) {
  searchScene(term: $term) {
    id title code date duration director details
    studio { name }
    tags { name }
    performers { as performer { name } }
    images { url }
  }
}"""

_FINGERPRINT_QUERY = """
query Find($hash: String!) {
  findSceneByFingerprint(fingerprint: {hash: $hash, algorithm: OSHASH}) {
    id title code date duration director details
    studio { name }
    tags { name }
    performers { as performer { name } }
    images { url }
  }
}"""

_FIND_BY_ID_QUERY = """
query FindByID($id: ID!) {
  findScene(id: $id) {
    id title code date duration director details
    studio { name }
    tags { name }
    performers { as performer { name } }
    images { url }
  }
}"""

# content_type → GraphQL type 参数映射
_TYPE_FILTER: dict[str, str] = {
    "censored": "JAV",
    "uncensored": "Scene",
    "western": "Scene",
    "fc2": "Scene",
    "amateur": "Scene",
    "hentai": "JAV",
}


class ThePornDBCrawler(Crawler):
    """theporndb.net Stash GraphQL 爬虫.

    使用通用 Stash-box 兼容 GraphQL 接口, 可访问 theporndb 或其他 Stash 实例.
    content_type → GraphQL ?type= 参数:
      censored / hentai → JAV
      uncensored / western → Scene
      null → 不加 filter (搜索所有类型)
    """

    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(name=SiteName.THEPORNDB, base_url="https://theporndb.net/graphql")

    async def fetch(self, query: SearchQuery, options: FetchOptions | None = None) -> MediaMetadata | None:
        token = self.config.api_token if self.config else None
        if not token:
            return None

        headers = {"Authorization": f"Bearer {token}"}
        gql_url = self._gql_url(query.content_type)

        # 优先用 oshash 精确匹配
        if query.file_hash:
            data = await self.client.post_json(
                gql_url,
                json={
                    "query": _FINGERPRINT_QUERY,
                    "variables": {"hash": query.file_hash},
                },
                headers=headers,
            )
            if data and isinstance(data, dict):
                results = (data.get("data") or {}).get("findSceneByFingerprint", [])
                if results:
                    return self._scene_to_metadata(results[0])

        # 文本搜索
        data = await self.client.post_json(
            gql_url,
            json={
                "query": _SEARCH_QUERY,
                "variables": {"term": query.number},
            },
            headers=headers,
        )

        if not data or not isinstance(data, dict):
            return None

        results = (data.get("data") or {}).get("searchScene", [])
        if not results:
            return None

        # 选取 code 最匹配的 (精确匹配优先)
        number_lower = query.number.lower().replace("-", "")
        best = min(
            results,
            key=lambda s: (
                0 if s.get("code", "").lower().replace("-", "") == number_lower else 1,
                -len(s.get("title") or ""),
            ),
        )
        return self._scene_to_metadata(best)

    # --- 抽象方法实现 (保持测试框架兼容) ---

    async def _search(self, query: SearchQuery, options: FetchOptions | None = None) -> str | None:
        """搜索并返回一个假的 internal URL (由 _scrape 处理)."""
        token = self.config.api_token if self.config else None
        if not token:
            return None

        headers = {"Authorization": f"Bearer {token}"}
        gql_url = self._gql_url(query.content_type)

        # hash 查找
        if query.file_hash:
            data = await self.client.post_json(
                gql_url,
                json={
                    "query": _FINGERPRINT_QUERY,
                    "variables": {"hash": query.file_hash},
                },
                headers=headers,
            )
            results = (data or {}).get("data", {}).get("findSceneByFingerprint", [])
            if results:
                return f"gql://scene/{results[0]['id']}"

        # 文本搜索
        data = await self.client.post_json(
            gql_url,
            json={
                "query": _SEARCH_QUERY,
                "variables": {"term": query.number},
            },
            headers=headers,
        )

        results = (data or {}).get("data", {}).get("searchScene", [])
        if results:
            number_lower = query.number.lower().replace("-", "")
            best = min(results, key=lambda s: (0 if s.get("code", "").lower().replace("-", "") == number_lower else 1,))
            return f"gql://scene/{best['id']}"

        return None

    async def _scrape(self, url: str, options: FetchOptions | None = None) -> MediaMetadata | None:
        """从 gql://scene/{id} URL 获取场景详情."""
        token = self.config.api_token if self.config else None
        if not token or not url.startswith("gql://scene/"):
            return None

        scene_id = url.removeprefix("gql://scene/")
        headers = {"Authorization": f"Bearer {token}"}

        data = await self.client.post_json(
            self.base_url,
            json={
                "query": _FIND_BY_ID_QUERY,
                "variables": {"id": scene_id},
            },
            headers=headers,
        )

        scene = (data or {}).get("data", {}).get("findScene")
        if not scene:
            return None
        return self._scene_to_metadata(scene)

    # --- 工具方法 ---

    def _gql_url(self, content_type: ContentType | None) -> str:
        """根据 ContentType 构造带 type filter 的 GraphQL URL."""
        if content_type:
            gql_type = _TYPE_FILTER.get(str(content_type))
            if gql_type:
                return f"{self.base_url}?type={gql_type}"
        return self.base_url

    @staticmethod
    def _scene_to_metadata(scene: dict) -> MediaMetadata:
        """将 GraphQL Scene 响应转换为 MediaMetadata."""
        number = scene.get("code") or scene.get("title", "")

        studio = None
        if isinstance(scene.get("studio"), dict):
            studio = scene["studio"].get("name")

        actors = []
        for pa in scene.get("performers", []) or []:
            perf = (pa or {}).get("performer", {}) or {}
            name = perf.get("name") or pa.get("as", "")
            if name:
                actors.append(name)

        tags = [t.get("name", "") for t in (scene.get("tags") or []) if t.get("name")]

        images = scene.get("images") or []
        thumb_url = images[0]["url"] if images else None

        return MediaMetadata(
            number=number,
            title=scene.get("title") or None,
            actors=actors,
            studio=studio,
            release=scene.get("date") or None,
            runtime=scene.get("duration"),
            tags=tags,
            directors=[scene["director"]] if scene.get("director") else [],
            plot=scene.get("details") or None,
            thumb_urls=[thumb_url] if thumb_url else [],
            external_id=scene.get("id") or None,
            source_url=f"https://theporndb.net/scenes/{scene['id']}" if scene.get("id") else None,
        )
