"""Wikidata 搜索 + 维基百科页面简介解析 (演员元数据)."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

from parsel import Selector

from amane.enums import ActorGender, SiteName
from amane.net.errors import SourceError
from amane.plugins.models import SourceCapability
from amane.utils.dates import normalize_calendar_date

from ...base import CrawlerProfile
from ...parsing import extract_text
from ..base import ActorCrawler
from ..models import ActorMetadata

# 描述关键词: 拉丁短语子串 (大小写不敏感) / CJK 限定 AV 语境.
# CJK 不能收裸的 女優/女优/男優 (日语 女優 = 普通女演员, 多语言搜索会带进普通演员).
_AV_KEYWORDS: tuple[str, ...] = (
    "av idol",
    "av actress",
    "av actor",
    "adult actress",
    "adult actor",
    "adult model",
    "porn actress",
    "porn actor",
    "pornographic",
    "japanese idol",
    "gravure",
    "av女優",
    "av女优",
    "av男優",
    "av男优",
    "avアイドル",
    "av監督",
    "アダルト",
    "成人映画",
    "成人影片",
)

# 英文描述里行业词与角色词分离出现时兜底 (如 "Japanese adult video actress").
_AV_INDUSTRY_RE = re.compile(r"\b(?:av|adult|porn|xxx)\b", re.IGNORECASE)
_AV_ROLE_RE = re.compile(r"\b(?:actress|actor|idol|model|star)\b", re.IGNORECASE)

# 多语言搜索顺序: ja 标签最全 (日本 AV 从业者), zh 次之, en 兜底.
_SEARCH_LANGUAGES: tuple[str, ...] = ("ja", "zh", "en")
_SEARCH_CANDIDATE_LIMIT = 5

# Wikidata P106 职业中 AV 相关条目.
_AV_OCCUPATIONS: frozenset[str] = frozenset(
    {
        "Q1079215",  # AV女優 / AV idol
        "Q8380347",  # AV男優
        "Q488111",  # ポルノ俳優 / pornographic film actor / 色情演員
    }
)

# Wikidata P21 (sex or gender) → ActorGender
_P21_GENDER: dict[str, ActorGender] = {
    "Q6581072": ActorGender.FEMALE,
    "Q6581097": ActorGender.MALE,
}


class WikipediaActorCrawler(ActorCrawler):
    """Wikidata 搜索 + 维基百科页面简介."""

    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(
            name=SiteName.WIKIPEDIA,
            base_url="https://www.wikidata.org",
            urls=[
                "https://www.wikidata.org",
                "https://ja.wikipedia.org",
                "https://zh.wikipedia.org",
                "https://ja.m.wikipedia.org",
                "https://zh.m.wikipedia.org",
            ],
            capabilities=frozenset({SourceCapability.ACTOR_PROFILE}),
            genders=frozenset({ActorGender.FEMALE, ActorGender.MALE}),
        )

    async def fetch(self, name: str) -> ActorMetadata | None:
        """多语言搜索候选 → 逐候选校验实体 (全语言描述 / P106 职业) → 构建元数据."""
        candidates = await self._wikidata_search(name)
        for qid, desc in candidates:
            entity = await self._entity_data(qid)
            if entity is None:
                continue
            if _is_av_entity(entity):
                return await self._build_metadata(qid, entity, desc)
        return None

    async def _search(self, name: str) -> str | None:
        raise NotImplementedError("WikipediaActorCrawler overrides fetch()")

    async def _scrape(self, url: str) -> ActorMetadata | None:
        raise NotImplementedError("WikipediaActorCrawler overrides fetch()")

    async def _wikidata_search(self, name: str) -> list[tuple[str, str | None]]:
        """多语言 (ja/zh/en) wbsearchentities; 描述命中 AV 关键词者按语言顺序去重.

        单语言失败不阻断; 全部失败时冒泡最后一次异常 (契约见 docs/dev/crawlers.md 多 URL 试探).
        """
        out: dict[str, str | None] = {}
        last_error: SourceError | None = None
        for lang in _SEARCH_LANGUAGES:
            url = (
                f"{self.base_url}/w/api.php?action=wbsearchentities&search={quote(name)}"
                f"&language={lang}&uselang={lang}&limit={_SEARCH_CANDIDATE_LIMIT}&format=json"
            )
            try:
                data = await self.client.get_json(url, cookies=self.cookies)
            except SourceError as exc:
                last_error = exc
                continue
            if not isinstance(data, dict):
                continue
            for item in data.get("search") or []:
                if not isinstance(item, dict):
                    continue
                qid = item.get("id")
                if not (isinstance(qid, str) and qid.startswith("Q")) or qid in out:
                    continue
                desc = str(item.get("description") or "")
                if not _match_av_keyword(desc):
                    continue
                out[qid] = desc or None
        if not out and last_error is not None:
            raise last_error
        return list(out.items())

    async def _entity_data(self, qid: str) -> dict[str, Any] | None:
        url = f"{self.base_url}/wiki/Special:EntityData/{qid}.json"
        data = await self.client.get_json(url, cookies=self.cookies)
        if not isinstance(data, dict):
            return None
        entities = data.get("entities")
        if not isinstance(entities, dict):
            return None
        entity = entities.get(qid)
        return entity if isinstance(entity, dict) else None

    async def _build_metadata(
        self,
        qid: str,
        entity: dict[str, Any],
        tagline: str | None,
    ) -> ActorMetadata:
        labels_raw = entity.get("labels")
        labels: dict[str, Any] = labels_raw if isinstance(labels_raw, dict) else {}
        preferred = _prefer_label(labels)
        aliases = [a for a in _label_aliases(labels) if a != preferred]
        birthday = _claim_time(entity, "P569")
        gender = _claim_gender(entity)
        image_url = _claim_commons_image(entity)
        provider_ids = _provider_ids(qid, entity)
        sitelinks_raw = entity.get("sitelinks")
        sitelinks: dict[str, Any] = sitelinks_raw if isinstance(sitelinks_raw, dict) else {}

        overview: str | None = None
        birthplace: str | None = None
        source_url: str | None = None
        wiki_url = _prefer_wiki_url(sitelinks)
        if wiki_url:
            source_url = wiki_url
            page = await self.client.get_text(wiki_url, cookies=self.cookies)
            if page:
                overview, birthplace, page_birthday = _parse_wiki_page(page)
                if not birthday:
                    birthday = page_birthday

        if not source_url:
            source_url = f"https://www.wikidata.org/wiki/{qid}"

        # tagline: 搜索 description 优先, 否则实体 descriptions
        if not tagline:
            descs_raw = entity.get("descriptions")
            descs: dict[str, Any] = descs_raw if isinstance(descs_raw, dict) else {}
            for lang in ("zh", "zh-cn", "zh-tw", "ja", "en"):
                d = descs.get(lang)
                if isinstance(d, dict) and d.get("value"):
                    tagline = str(d["value"])
                    break

        return ActorMetadata(
            name=preferred,
            aliases=aliases,
            gender=gender,
            birthday=birthday,
            birthplace=birthplace,
            overview=overview,
            tagline=tagline,
            image_urls=[image_url] if image_url else [],
            provider_ids=provider_ids,
            source_url=source_url,
        )


def _match_av_keyword(description: str) -> bool:
    lower = description.lower()
    if any(k.lower() in lower for k in _AV_KEYWORDS):
        return True
    return bool(_AV_INDUSTRY_RE.search(description) and _AV_ROLE_RE.search(description))


def _is_av_entity(entity: dict[str, Any]) -> bool:
    """全语言描述关键词命中, 或 P106 职业含 AV 相关条目."""
    return _descriptions_match(entity) or _occupation_match(entity)


def _descriptions_match(entity: dict[str, Any]) -> bool:
    descs = entity.get("descriptions")
    if not isinstance(descs, dict):
        return False
    for item in descs.values():
        if isinstance(item, dict) and isinstance(item.get("value"), str) and _match_av_keyword(item["value"]):
            return True
    return False


def _occupation_match(entity: dict[str, Any]) -> bool:
    """P106 职业 claims 含 _AV_OCCUPATIONS 任一条目."""
    claims = entity.get("claims")
    if not isinstance(claims, dict):
        return False
    entries = claims.get("P106")
    if not isinstance(entries, list):
        return False
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        snak = entry.get("mainsnak")
        if not isinstance(snak, dict):
            continue
        datavalue = snak.get("datavalue")
        if not isinstance(datavalue, dict):
            continue
        value = datavalue.get("value")
        if isinstance(value, dict) and value.get("id") in _AV_OCCUPATIONS:
            return True
    return False


def _prefer_label(labels: dict[str, Any]) -> str | None:
    for lang in ("ja", "zh", "zh-cn", "zh-tw", "en"):
        item = labels.get(lang)
        if isinstance(item, dict) and item.get("value"):
            return str(item["value"])
    for item in labels.values():
        if isinstance(item, dict) and item.get("value"):
            return str(item["value"])
    return None


def _label_aliases(labels: dict[str, Any]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for lang in ("ja", "zh", "zh-cn", "zh-tw", "en"):
        item = labels.get(lang)
        if not isinstance(item, dict):
            continue
        val = str(item.get("value") or "").strip()
        if val and val not in seen:
            seen.add(val)
            out.append(val)
    return out


def _claim_time(entity: dict[str, Any], pid: str) -> str | None:
    claims = entity.get("claims")
    if not isinstance(claims, dict):
        return None
    entries = claims.get(pid)
    if not isinstance(entries, list) or not entries:
        return None
    snak = entries[0].get("mainsnak") if isinstance(entries[0], dict) else None
    if not isinstance(snak, dict):
        return None
    datavalue = snak.get("datavalue")
    if not isinstance(datavalue, dict):
        return None
    value = datavalue.get("value")
    if not isinstance(value, dict):
        return None
    time = value.get("time")
    if not isinstance(time, str):
        return None
    # +1994-08-26T00:00:00Z
    m = re.match(r"[+-]?(\d{4})-(\d{2})-(\d{2})", time)
    if not m:
        return None
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"


def _claim_gender(entity: dict[str, Any]) -> ActorGender | None:
    """Wikidata P21 → female/male; 其它/缺失返回 None."""
    claims = entity.get("claims")
    if not isinstance(claims, dict):
        return None
    entries = claims.get("P21")
    if not isinstance(entries, list) or not entries:
        return None
    snak = entries[0].get("mainsnak") if isinstance(entries[0], dict) else None
    if not isinstance(snak, dict):
        return None
    datavalue = snak.get("datavalue")
    if not isinstance(datavalue, dict):
        return None
    value = datavalue.get("value")
    if not isinstance(value, dict):
        return None
    qid = value.get("id")
    if not isinstance(qid, str):
        return None
    return _P21_GENDER.get(qid)


def _claim_commons_image(entity: dict[str, Any]) -> str | None:
    """Wikidata P18 (commonsMedia) → Commons Special:FilePath 直链."""
    claims = entity.get("claims")
    if not isinstance(claims, dict):
        return None
    entries = claims.get("P18")
    if not isinstance(entries, list) or not entries:
        return None
    snak = entries[0].get("mainsnak") if isinstance(entries[0], dict) else None
    if not isinstance(snak, dict):
        return None
    datavalue = snak.get("datavalue")
    if not isinstance(datavalue, dict):
        return None
    filename = datavalue.get("value")
    if not isinstance(filename, str) or not filename.strip():
        return None
    # Special:FilePath 会 302 到实际 upload URL; 空格等需编码.
    return f"https://commons.wikimedia.org/wiki/Special:FilePath/{quote(filename.strip())}"


def _provider_ids(qid: str, entity: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {"wikidata": qid}
    claims = entity.get("claims")
    if not isinstance(claims, dict):
        return out
    mapping = {
        "P345": "imdb",
        "P4985": "tmdb",
        "P2002": "twitter",
        "P2003": "instagram",
        "P9781": "fanza",
    }
    for pid, key in mapping.items():
        entries = claims.get(pid)
        if not isinstance(entries, list) or not entries:
            continue
        snak = entries[0].get("mainsnak") if isinstance(entries[0], dict) else None
        if not isinstance(snak, dict):
            continue
        datavalue = snak.get("datavalue")
        if not isinstance(datavalue, dict):
            continue
        val = datavalue.get("value")
        if isinstance(val, str) and val:
            out[key] = val
    return out


def _prefer_wiki_url(sitelinks: dict[str, Any]) -> str | None:
    for key, prefix in (
        ("jawiki", "https://ja.wikipedia.org/wiki/"),
        ("zhwiki", "https://zh.wikipedia.org/wiki/"),
        ("enwiki", "https://en.wikipedia.org/wiki/"),
    ):
        link = sitelinks.get(key)
        if isinstance(link, dict) and link.get("title"):
            return prefix + quote(str(link["title"]).replace(" ", "_"), safe="()_")
    return None


def _parse_wiki_page(html_text: str) -> tuple[str | None, str | None, str | None]:
    html = Selector(text=html_text)
    overview = None
    for p in html.css(".mw-parser-output p"):
        text = " ".join(p.xpath(".//text()").getall()).strip()
        text = re.sub(r"\s+", " ", text)
        if len(text) > 40:
            overview = text
            break

    birthplace = None
    birthday = None
    for row in html.css("table.infobox tr, .infobox tr"):
        label = (extract_text(row, "string(./th)") or extract_text(row, "string(.//th)") or "").strip()
        value = (extract_text(row, "string(./td)") or extract_text(row, "string(.//td)") or "").strip()
        if not label or not value:
            continue
        if "出身" in label and not birthplace:
            birthplace = re.sub(r"\s+", " ", value)
        if ("生年" in label or "出生" in label) and not birthday:
            birthday = normalize_calendar_date(value)
    return overview, birthplace, birthday
