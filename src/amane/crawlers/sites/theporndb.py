from typing import TYPE_CHECKING

from ...enums import ActorGender, SiteName
from ..base import Crawler, CrawlerProfile
from ..models import FetchOptions, FilmActor, MediaMetadata, SearchQuery

_PERFORMER_GENDER: dict[str, ActorGender] = {
    "FEMALE": ActorGender.FEMALE,
    "MALE": ActorGender.MALE,
}

if TYPE_CHECKING:
    from ...parsing.file_info import ContentType

_SEARCH_QUERY = """
query Search($term: String!) {
  searchScene(term: $term) {
    id title code date duration director details
    studio { name }
    tags { name }
    performers { as performer { name gender } }
    images { url }
  }
}"""

_FINGERPRINT_QUERY = """
query Find($hash: String!) {
  findSceneByFingerprint(fingerprint: {hash: $hash, algorithm: OSHASH}) {
    id title code date duration director details
    studio { name }
    tags { name }
    performers { as performer { name gender } }
    images { url }
  }
}"""

_FIND_BY_ID_QUERY = """
query FindByID($id: ID!) {
  findScene(id: $id) {
    id title code date duration director details
    studio { name }
    tags { name }
    performers { as performer { name gender } }
    images { url }
  }
}"""

_TYPE_FILTER: dict[str, str] = {
    "censored": "JAV",
    "uncensored": "Scene",
    "western": "Scene",
    "fc2": "Scene",
    "amateur": "Scene",
    "hentai": "JAV",
}


class ThePornDBCrawler(Crawler):
    """Stash-box GraphQL. ``content_type`` 映射到 ``?type=``; 缺省不加 filter."""

    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(name=SiteName.THEPORNDB, base_url="https://theporndb.net/graphql", uses_file_hash=True)

    async def fetch(self, query: SearchQuery, options: FetchOptions | None = None) -> MediaMetadata | None:
        token = self.config.api_token if self.config else None
        if not token:
            return None

        headers = {"Authorization": f"Bearer {token}"}
        gql_url = self._gql_url(query.content_type)

        # 优先 oshash 精确匹配.
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

        # 文本搜索.
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

        # 精确匹配 code 优先.
        number_lower = query.number.lower().replace("-", "")
        best = min(
            results,
            key=lambda s: (
                0 if s.get("code", "").lower().replace("-", "") == number_lower else 1,
                -len(s.get("title") or ""),
            ),
        )
        return self._scene_to_metadata(best)

    async def _search(self, query: SearchQuery, options: FetchOptions | None = None) -> str | None:
        token = self.config.api_token if self.config else None
        if not token:
            return None

        headers = {"Authorization": f"Bearer {token}"}
        gql_url = self._gql_url(query.content_type)

        # 优先 oshash 精确匹配.
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

        # 文本搜索.
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
            # 精确匹配 code 优先.
            number_lower = query.number.lower().replace("-", "")
            best = min(results, key=lambda s: (0 if s.get("code", "").lower().replace("-", "") == number_lower else 1,))
            return f"gql://scene/{best['id']}"

        return None

    async def _scrape(self, url: str, options: FetchOptions | None = None) -> MediaMetadata | None:
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

    def _gql_url(self, content_type: ContentType | None) -> str:
        if content_type:
            gql_type = _TYPE_FILTER.get(str(content_type))
            if gql_type:
                return f"{self.base_url}?type={gql_type}"
        return self.base_url

    @staticmethod
    def _scene_to_metadata(scene: dict) -> MediaMetadata:
        number = scene.get("code") or scene.get("title", "")

        studio = None
        if isinstance(scene.get("studio"), dict):
            studio = scene["studio"].get("name")

        actors: list[FilmActor] = []
        for pa in scene.get("performers", []) or []:
            perf = (pa or {}).get("performer", {}) or {}
            name = perf.get("name") or pa.get("as", "")
            if not name:
                continue
            gender = _PERFORMER_GENDER.get(str(perf.get("gender") or ""))
            actors.append(FilmActor(name=name, gender=gender or ActorGender.UNKNOWN))

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
