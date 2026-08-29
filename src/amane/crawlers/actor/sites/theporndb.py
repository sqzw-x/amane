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
_SEARCH_QUERY = """
query SearchPerformer($term: String!) {
  searchPerformer(term: $term) {
    id name aliases gender birth_date country height
    measurements { cup_size band_size waist hip }
    images { url }
  }
}"""

_GENDER: dict[str, ActorGender] = {
    "FEMALE": ActorGender.FEMALE,
    "MALE": ActorGender.MALE,
}


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
        data = await self.client.post_json(
            self.base_url,
            json={"query": _SEARCH_QUERY, "variables": {"term": name}},
            headers=headers,
        )
        if not isinstance(data, dict):
            return None
        payload = data.get("data")
        if not isinstance(payload, dict):
            return None
        results = payload.get("searchPerformer") or []
        if not isinstance(results, list):
            return None
        hit = pick_performer(results, name)
        return performer_to_metadata(hit) if hit is not None else None

    async def _search(self, name: str) -> str | None:
        raise NotImplementedError("ThePornDBActorCrawler overrides fetch()")

    async def _scrape(self, url: str) -> ActorMetadata | None:
        raise NotImplementedError("ThePornDBActorCrawler overrides fetch()")


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
    raw_images = perf.get("images")
    images = raw_images if isinstance(raw_images, list) else []
    image_urls = [url for img in images if isinstance(img, dict) for url in [_norm(str(img.get("url") or ""))] if url]
    performer_id = str(perf.get("id") or "").strip()
    birthday = perf.get("birth_date")

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
        image_urls=image_urls,
        provider_ids={"theporndb": performer_id} if performer_id else {},
        source_url=f"https://theporndb.net/performers/{performer_id}" if performer_id else None,
    )


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
