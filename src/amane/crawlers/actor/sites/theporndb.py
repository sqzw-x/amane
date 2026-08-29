"""ThePornDB 演员档案 — Stash-box GraphQL searchPerformer, 与影片爬虫共用 api_token."""

from __future__ import annotations

import unicodedata
from typing import Any

from amane.enums import ActorGender, SiteName
from amane.plugins.models import SourceCapability

from ...base import CrawlerProfile
from ..base import ActorCrawler
from ..models import ActorMetadata

# Stash 客户端 PerformerFragment 子集. 不用顶层 cup_size 等较新字段, 以免旧 stash-box 拒查询.
_PERFORMER_FIELDS = """
    id name aliases gender birth_date country height disambiguation
    deleted merged_into_id
    measurements { cup_size band_size waist hip }
    images { url width height }
    urls { url type }
"""

_SEARCH_QUERY = f"""
query SearchPerformer($term: String!) {{
  searchPerformer(term: $term) {{
    {_PERFORMER_FIELDS}
  }}
}}"""

_FIND_QUERY = f"""
query FindPerformer($id: ID!) {{
  findPerformer(id: $id) {{
    {_PERFORMER_FIELDS}
  }}
}}"""

_GENDER: dict[str, ActorGender] = {
    "FEMALE": ActorGender.FEMALE,
    "MALE": ActorGender.MALE,
}

# stash-box URL.type → provider_ids 键; 与 wikipedia 已用的 imdb/twitter/instagram 对齐.
_URL_TYPE_KEYS: dict[str, str] = {
    "iafd": "iafd",
    "freeones": "freeones",
    "indexxx": "indexxx",
    "twitter": "twitter",
    "instagram": "instagram",
    "imdb": "imdb",
    "wikidata": "wikidata",
    "wikipedia": "wikipedia",
    "fanza": "fanza",
    "manyvids": "manyvids",
    "onlyfans": "onlyfans",
    "official homepage": "homepage",
    "homepage": "homepage",
    "official": "homepage",
}

_MERGE_HOPS = 4


class ThePornDBActorCrawler(ActorCrawler):
    """theporndb.net Stash-box 演员搜索. 无 token 不发请求; 精确匹配 name/aliases, 不回退首条."""

    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(
            name=SiteName.THEPORNDB,
            base_url="https://theporndb.net/graphql",
            capabilities=frozenset({SourceCapability.ACTOR_PROFILE}),
            genders=frozenset({ActorGender.FEMALE, ActorGender.MALE}),
        )

    async def fetch(self, name: str) -> ActorMetadata | None:
        token = self.config.api_token if self.config else None
        if not token or not name.strip():
            return None

        headers = {"Authorization": f"Bearer {token}"}
        payload = await self._gql(_SEARCH_QUERY, {"term": name}, headers)
        if payload is None:
            return None
        results = payload.get("searchPerformer") or []
        if not isinstance(results, list):
            return None
        hit = pick_performer(results, name)
        if hit is None:
            return None
        resolved = await self._follow_merge(hit, headers)
        return performer_to_metadata(resolved) if resolved is not None else None

    async def _search(self, name: str) -> str | None:
        raise NotImplementedError("ThePornDBActorCrawler overrides fetch()")

    async def _scrape(self, url: str) -> ActorMetadata | None:
        raise NotImplementedError("ThePornDBActorCrawler overrides fetch()")

    async def _follow_merge(self, hit: dict[str, Any], headers: dict[str, str]) -> dict[str, Any] | None:
        """deleted 条目跟 merged_into_id; 无合并目标则丢弃. 环/过深视为未命中."""
        current: dict[str, Any] | None = hit
        seen: set[str] = set()
        for _ in range(_MERGE_HOPS):
            if current is None:
                return None
            pid = str(current.get("id") or "").strip()
            if pid:
                if pid in seen:
                    return None
                seen.add(pid)
            if current.get("deleted") is not True:
                return current
            merge_id = str(current.get("merged_into_id") or "").strip()
            if not merge_id:
                return None
            payload = await self._gql(_FIND_QUERY, {"id": merge_id}, headers)
            found = payload.get("findPerformer") if payload is not None else None
            current = found if isinstance(found, dict) else None
        return None

    async def _gql(self, query: str, variables: dict[str, Any], headers: dict[str, str]) -> dict[str, Any] | None:
        data = await self.client.post_json(
            self.base_url,
            json={"query": query, "variables": variables},
            headers=headers,
        )
        if not isinstance(data, dict):
            return None
        payload = data.get("data")
        return payload if isinstance(payload, dict) else None


def pick_performer(results: list[Any], name: str) -> dict[str, Any] | None:
    """精确匹配 canonical 名或 aliases; 比较 NFKC + casefold. 不回退首条, 避免模糊搜索误伤."""
    needle = _norm(name).casefold()
    if not needle:
        return None
    for item in results:
        if not isinstance(item, dict):
            continue
        if any(_norm(n).casefold() == needle for n in _performer_names(item)):
            return item
    return None


def performer_to_metadata(perf: dict[str, Any]) -> ActorMetadata | None:
    """将 GraphQL Performer 转为 ActorMetadata; 无名则 None."""
    name = _norm(str(perf.get("name") or ""))
    if not name:
        return None

    raw_aliases = perf.get("aliases")
    alias_src = raw_aliases if isinstance(raw_aliases, list) else []
    aliases = _dedupe_preserve([a for a in (_norm(str(x)) for x in alias_src if x) if a != name])
    raw_meas = perf.get("measurements")
    meas = raw_meas if isinstance(raw_meas, dict) else {}
    performer_id = str(perf.get("id") or "").strip()
    birthday = perf.get("birth_date")
    tagline = _norm(str(perf.get("disambiguation") or "")) or None

    return ActorMetadata(
        name=name,
        aliases=aliases,
        gender=_GENDER.get(str(perf.get("gender") or "")),
        birthday=birthday if isinstance(birthday, str) else None,
        birthplace=_norm(str(perf.get("country") or "")) or None,
        height=_positive_int(perf.get("height")),
        bust=_positive_int(meas.get("band_size")),
        waist=_positive_int(meas.get("waist")),
        hip=_positive_int(meas.get("hip")),
        cup=_norm(str(meas.get("cup_size") or "")) or None,
        tagline=tagline,
        image_urls=_image_urls(perf),
        provider_ids=_provider_ids(perf, performer_id),
        source_url=f"https://theporndb.net/performers/{performer_id}" if performer_id else None,
    )


def _image_urls(perf: dict[str, Any]) -> list[str]:
    raw_images = perf.get("images")
    images = [img for img in raw_images if isinstance(img, dict)] if isinstance(raw_images, list) else []
    ranked = sorted(images, key=_image_area, reverse=True)
    urls: list[str] = []
    for img in ranked:
        url = _norm(str(img.get("url") or ""))
        if url and url not in urls:
            urls.append(url)
    return urls


def _image_area(img: dict[str, Any]) -> int:
    width = _positive_int(img.get("width")) or 0
    height = _positive_int(img.get("height")) or 0
    return width * height


def _provider_ids(perf: dict[str, Any], performer_id: str) -> dict[str, str]:
    out: dict[str, str] = {}
    if performer_id:
        out["theporndb"] = performer_id
    raw_urls = perf.get("urls")
    if not isinstance(raw_urls, list):
        return out
    for item in raw_urls:
        if not isinstance(item, dict):
            continue
        url = _norm(str(item.get("url") or ""))
        if not url or "theporndb.net" in url.casefold():
            continue
        key = _URL_TYPE_KEYS.get(_norm(str(item.get("type") or "")).casefold())
        if key is None or key in out:
            continue
        out[key] = url
    return out


def _performer_names(perf: dict[str, Any]) -> list[str]:
    names = [str(perf.get("name") or "")]
    aliases = perf.get("aliases") or []
    if isinstance(aliases, list):
        names.extend(str(a) for a in aliases if a)
    return [n for n in names if _norm(n)]


def _norm(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip()


def _dedupe_preserve(values: list[str]) -> list[str]:
    out: list[str] = []
    for v in values:
        if v not in out:
            out.append(v)
    return out


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value
