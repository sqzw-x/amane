"""Wikidata 搜索 + 维基百科简介. CJK 不允许单独匹配「女優/女优/男優」."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, NamedTuple
from urllib.parse import quote

from parsel import Selector

from amane.enums import ActorGender, SiteName
from amane.net.errors import SourceError
from amane.plugins.models import SourceCapability
from amane.utils.dates import normalize_calendar_date

from ...base import CrawlerProfile
from ...parsing import extract_text, is_same_name
from ..base import ActorCrawler
from ..models import ActorMetadata

if TYPE_CHECKING:
    from ....config import SiteConfig

# 拉丁短语子串大小写不敏感. 日语「女優」= 普通女演员, 单独匹配会带入非 AV 条目.
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

# 英文描述里行业词与角色词分离出现时仍算命中 (如 "Japanese adult video actress").
_AV_INDUSTRY_RE = re.compile(r"\b(?:av|adult|porn|xxx)\b", re.IGNORECASE)
_AV_ROLE_RE = re.compile(r"\b(?:actress|actor|idol|model|star)\b", re.IGNORECASE)

_SEARCH_LANGUAGES: tuple[str, ...] = ("ja", "zh", "en")
_SEARCH_CANDIDATE_LIMIT = 5

# 词条语言只决定取哪一版正文, 不参与上面的实体检索; 取值集合与默认优先级同源, SiteConfig.languages 的枚举从这里收窄.
WIKI_LANGUAGES: tuple[str, ...] = ("zh", "ja", "en")

_PARSER_OUTPUT_CLASS = "mw-parser-output"
_PARSER_OUTPUT = f".{_PARSER_OUTPUT_CLASS}"
_CONTENT_NODES = ".//h1|.//h2|.//h3|.//h4|.//h5|.//h6|.//p|.//ul|.//ol"
_HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
# 列表整块取用 (每个 li 一行), 嵌套列表与列表内的 p 都不再单独取, 避免重复.
_LIST_TAGS = frozenset({"ul", "ol"})
_SKIP_WRAPPERS = frozenset({"table", "figure"})

# 章节白名单: 命中即整节取用, 未命中的章节 (作品 / 出演 / 脚注等) 只保留导语.
# 中 / 日维基的同类章节用字不同 (歷 / 歴 与 历), 折叠成简体后再比对.
_HEADING_FOLD = str.maketrans({"歷": "历", "歴": "历", "經": "经", "経": "经", "來": "来", "簡": "简", "藝": "艺"})
_SECTION_KEYWORDS: tuple[str, ...] = (
    "人物",
    "简历",
    "经历",
    "履历",
    "略历",
    "来历",
    "生平",
    "演艺生涯",
    "プロフィール",
    "生い立ち",
    "early life",
    "career",
    "biography",
    "personal life",
)

# 引用标记 [1] / [ 3 ] / [注 1] / [要出典]; 全角括号与内部空白一并容忍.
_CITATION_RE = re.compile(r"[\[［]\s*(?:\d+|注\s*\d*|注釈\s*\d*|要出典|要ページ番号)\s*[\]］]")
# 标题里的 [編集] / [edit] 是维基皮肤注入的编辑入口, 不属于标题文本.
_EDIT_LINK_RE = re.compile(r"[\[［]\s*(?:編集|編輯|edit)\s*[\]］]", re.IGNORECASE)

# 简介上限: 段落级拼装, 放不下的那一段截到句末; 剩余空间不足 _MIN_TAIL_CHARS 就不再续接.
_OVERVIEW_MAX_CHARS = 1500
_MIN_TAIL_CHARS = 30
_PARAGRAPH_SEP = "\n\n"
_SENTENCE_ENDS = "。．.！!？?…"

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
    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(
            name=SiteName.WIKIPEDIA,
            base_url="https://www.wikidata.org",
            urls=[
                "https://www.wikidata.org",
                "https://ja.wikipedia.org",
                "https://zh.wikipedia.org",
                "https://en.wikipedia.org",
            ],
            capabilities=frozenset({SourceCapability.ACTOR_PROFILE}),
            genders=frozenset({ActorGender.FEMALE, ActorGender.MALE}),
        )

    async def fetch(self, name: str) -> ActorMetadata | None:
        candidates = await self._wikidata_search(name)
        # 逐候选校验实体 (描述 / P106 职业), 并要求名字相符.
        for qid, desc in candidates:
            entity = await self._entity_data(qid)
            if entity is None:
                continue
            if _is_av_entity(entity) and _name_matches(entity, name):
                return await self._build_metadata(qid, entity, desc)
        return None

    async def _search(self, name: str) -> str | None:
        raise NotImplementedError("WikipediaActorCrawler overrides fetch()")

    async def _scrape(self, url: str) -> ActorMetadata | None:
        raise NotImplementedError("WikipediaActorCrawler overrides fetch()")

    async def _wikidata_search(self, name: str) -> list[tuple[str, str | None]]:
        """ja/zh/en wbsearchentities; 描述命中 AV 关键词者按语言顺序去重.

        单语言失败不阻断; 全部失败时冒泡最后一次异常.
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

    async def _fetch_wiki_page(self, sitelinks: dict[str, Any]) -> _WikiPage | None:
        """按配置的语言优先级取第一个有正文的词条.

        单语言失败不阻断; 全部失败时冒泡最后一次异常. 各语言的词条都没有正文时,
        仍返回优先级最高的可解析词条, 信息框字段照常取用.
        """
        fallback: _WikiPage | None = None
        last_error: SourceError | None = None
        for lang in _entry_languages(self.config):
            url = _wiki_url(sitelinks, lang)
            if url is None:
                continue
            try:
                # 维基正文用 get_text: 引用里「年齢認証」等词会让 get_html 误判拦截.
                html = await self.client.get_text(url, cookies=self.cookies)
            except SourceError as exc:
                last_error = exc
                continue
            if not html:
                continue
            page = _parse_wiki_page(url, html)
            if page.overview:
                return page
            if fallback is None:
                fallback = page
        if fallback is None and last_error is not None:
            raise last_error
        return fallback

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
        page = await self._fetch_wiki_page(sitelinks)
        if page is not None:
            source_url = page.url
            overview = page.overview
            birthplace = page.birthplace
            if not birthday:
                birthday = page.birthday

        if not source_url:
            source_url = f"https://www.wikidata.org/wiki/{qid}"

        # tagline 未命中则回退实体 descriptions.
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
    return _descriptions_match(entity) or _occupation_match(entity)


def _name_matches(entity: dict[str, Any], name: str) -> bool:
    """实体名 (标签或别名, 任一语言) 与查找名 NFKC 折叠后相等才算命中.

    ``wbsearchentities`` 是模糊检索: 只按描述与职业筛实体, 会把名字完全不同的他人档案当成本人.
    """
    return any(is_same_name(value, name) for value in _entity_name_values(entity))


def _entity_name_values(entity: dict[str, Any]) -> list[str]:
    """实体名候选: ``labels`` 与 ``aliases`` 的全部语言取值."""
    out: list[str] = []
    for key in ("labels", "aliases"):
        groups = entity.get(key)
        if not isinstance(groups, dict):
            continue
        for items in groups.values():
            for item in items if isinstance(items, list) else [items]:
                value = item.get("value") if isinstance(item, dict) else None
                if isinstance(value, str) and value.strip():
                    out.append(value)
    return out


def _descriptions_match(entity: dict[str, Any]) -> bool:
    descs = entity.get("descriptions")
    if not isinstance(descs, dict):
        return False
    for item in descs.values():
        if isinstance(item, dict) and isinstance(item.get("value"), str) and _match_av_keyword(item["value"]):
            return True
    return False


def _occupation_match(entity: dict[str, Any]) -> bool:
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
    # Special:FilePath 会 302 到实际 upload URL; 空格等必须编码.
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


def _entry_languages(config: SiteConfig | None) -> tuple[str, ...]:
    """未注入 SiteConfig 的构造 (测试) 取默认优先级."""
    return WIKI_LANGUAGES if config is None else tuple(config.languages)


def _wiki_url(sitelinks: dict[str, Any], lang: str) -> str | None:
    link = sitelinks.get(f"{lang}wiki")
    title = link.get("title") if isinstance(link, dict) else None
    if not isinstance(title, str) or not title:
        return None
    return f"https://{lang}.wikipedia.org/wiki/{quote(title.replace(' ', '_'), safe='()_')}"


class _WikiPage(NamedTuple):
    url: str
    overview: str | None
    birthplace: str | None
    birthday: str | None


def _parse_wiki_page(url: str, html_text: str) -> _WikiPage:
    html = Selector(text=html_text)
    overview = _clip_blocks(_overview_blocks(html)) or None
    birthplace, birthday = _parse_infobox(html)
    return _WikiPage(url=url, overview=overview, birthplace=birthplace, birthday=birthday)


def _overview_blocks(html: Selector) -> list[str]:
    """导语段落 + 白名单章节的段落与列表; 表格内的段落 (信息框 / 导航框) 不取.

    标题层级决定归属: 白名单章节层级以下的子章节同属该章节, 遇到同级或更高级标题才退出.
    """
    blocks: list[str] = []
    headings: list[tuple[int, str]] = []
    selected = True
    for node in html.css(_PARSER_OUTPUT).xpath(_CONTENT_NODES):
        tag = node.root.tag
        if not isinstance(tag, str):
            continue
        if tag in _HEADING_TAGS:
            title = _heading_text(node)
            if title:
                level = int(tag[1])
                while headings and headings[-1][0] >= level:
                    headings.pop()
                headings.append((level, title))
                selected = any(_is_section(name) for _, name in headings)
            continue
        if not selected or _has_ancestor(node.root, _SKIP_WRAPPERS):
            continue
        if tag == "p":
            # 列表里的段落随整块列表取用, 不重复.
            if _has_ancestor(node.root, _LIST_TAGS):
                continue
            text = _clean_text(node)
            if text:
                blocks.append(text)
            continue
        if _has_ancestor(node.root, _LIST_TAGS):
            continue
        items = [text for text in (_clean_text(item) for item in node.xpath("./li")) if text]
        if items:
            blocks.append("\n".join(items))
    return blocks


def _heading_text(node: Selector) -> str:
    text = _EDIT_LINK_RE.sub("", "".join(node.xpath(".//text()").getall()))
    return re.sub(r"\s+", " ", text).strip()


def _is_section(title: str) -> bool:
    folded = title.translate(_HEADING_FOLD).lower()
    return any(keyword in folded for keyword in _SECTION_KEYWORDS)


def _clean_text(node: Selector) -> str:
    """节点文本: 取真实文本 (不按子节点补空格), 去引用标记并收拢空白."""
    return _clean_inline("".join(node.xpath(".//text()").getall()))


def _clean_inline(text: str) -> str:
    return re.sub(r"\s+", " ", _CITATION_RE.sub("", text)).strip()


def _has_ancestor(node: Any, tags: frozenset[str]) -> bool:
    """向上查找祖先; 到 ``.mw-parser-output`` 即停, 范围外的节点不参与判定."""
    for ancestor in node.iterancestors():
        tag = ancestor.tag
        if not isinstance(tag, str):
            continue
        if tag in tags:
            return True
        if tag == "div" and _PARSER_OUTPUT_CLASS in (ancestor.get("class") or ""):
            return False
    return False


def _clip_blocks(blocks: list[str]) -> str:
    out = ""
    for block in blocks:
        sep = _PARAGRAPH_SEP if out else ""
        room = _OVERVIEW_MAX_CHARS - len(out) - len(sep)
        if len(block) <= room:
            out = f"{out}{sep}{block}"
            continue
        if room >= _MIN_TAIL_CHARS:
            out = f"{out}{sep}{_cut_at_sentence(block, room)}"
        break
    return out


def _cut_at_sentence(text: str, limit: int) -> str:
    """截到句末并为省略号留位, 保证结果不超过 limit."""
    head = text[: limit - 1]
    cut = max((head.rfind(end) for end in _SENTENCE_ENDS), default=-1)
    if cut >= 0:
        head = head[: cut + 1]
    return f"{head.rstrip()}…"


def _parse_infobox(html: Selector) -> tuple[str | None, str | None]:
    """信息框的出身地与出生日期; 与词条语言无关, 命中即取."""
    birthplace = None
    birthday = None
    for row in html.css("table.infobox tr, .infobox tr"):
        label = (extract_text(row, "string(./th)") or extract_text(row, "string(.//th)") or "").strip()
        value = (extract_text(row, "string(./td)") or extract_text(row, "string(.//td)") or "").strip()
        if not label or not value:
            continue
        if ("出身" in label or "出生地" in label) and not birthplace:
            birthplace = _clean_inline(value)
        if ("生年" in label or "出生" in label) and not birthday:
            birthday = normalize_calendar_date(value)
    return birthplace, birthday
