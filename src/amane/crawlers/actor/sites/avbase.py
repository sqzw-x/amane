"""``/talents/{名}`` 聚合同一人物的多个名義, ``actors[]`` 即别名; 三围 / 生日来自 ``meta.basic_info``.

站点没有演员检索入口, ``/talents?q=`` 返回 404, 因此直接按名字构造 URL, 由站点把别名解析到人物页.
不存在的名義以 404 表达, 属于未命中而非来源失败. ``overview`` 不取: ``profile`` 出现率仅 5% 且含 HTML.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from amane.enums import ActorGender, SiteName
from amane.net.errors import FailureReason, RequestError
from amane.plugins.models import SourceCapability
from amane.utils.dates import normalize_calendar_date

from ...base import CrawlerProfile
from ...parsing import next_data_props
from ..base import ActorCrawler
from ..models import ActorMetadata

_MALE_NOTE = "男優"


class AvbaseActorCrawler(ActorCrawler):
    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(
            name=SiteName.AVBASE,
            base_url="https://www.avbase.net",
            capabilities=frozenset({SourceCapability.ACTOR_PROFILE}),
            # 男优也有 talent 页; 只声明 FEMALE 会在按性别裁站时跳过男优档案.
            genders=frozenset({ActorGender.FEMALE, ActorGender.MALE}),
        )

    async def _search(self, name: str) -> str | None:
        target = name.strip()
        if not target:
            return None
        return f"{self.base_url}/talents/{quote(target, safe='')}"

    async def _scrape(self, url: str) -> ActorMetadata | None:
        try:
            text = await self.client.get_html(url, cookies=self.cookies)
        except RequestError as exc:
            if exc.reason is FailureReason.NOT_FOUND:
                return None
            raise
        props = next_data_props(text)
        talent = props.get("talent") if props else None
        if not isinstance(talent, dict):
            return None
        return build_metadata(talent, url=url)


def build_metadata(talent: dict[str, Any], *, url: str) -> ActorMetadata | None:
    primary = talent.get("primary")
    if not isinstance(primary, dict):
        return None
    name = _text(primary.get("name"))
    if not name:
        return None
    info = _basic_info(talent)
    return ActorMetadata(
        name=name,
        aliases=[n for actor in _actors(talent) if (n := _text(actor.get("name"))) and n != name],
        gender=ActorGender.MALE if _text(primary.get("note")) == _MALE_NOTE else None,
        birthday=normalize_calendar_date(_text(info.get("birthday"))),
        birthplace=_text(info.get("prefectures")) or None,
        height=_int(info.get("height")),
        bust=_int(info.get("bust")),
        waist=_int(info.get("waist")),
        hip=_int(info.get("hip")),
        cup=_text(info.get("cup")) or None,
        image_urls=[image] if (image := _text(primary.get("image_url"))) else [],
        provider_ids={"avbase": str(talent["id"])} if talent.get("id") is not None else {},
        source_url=url,
    )


def _basic_info(talent: dict[str, Any]) -> dict[str, Any]:
    meta = talent.get("meta")
    info = meta.get("basic_info") if isinstance(meta, dict) else None
    return info if isinstance(info, dict) else {}


def _actors(talent: dict[str, Any]) -> list[dict[str, Any]]:
    actors = talent.get("actors")
    return [a for a in actors if isinstance(a, dict)] if isinstance(actors, list) else []


def _int(value: object) -> int | None:
    text = _text(value)
    return int(text) if text.isdigit() else None


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""
