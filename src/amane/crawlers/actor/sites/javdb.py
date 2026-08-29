"""JavDB 演员档案 — 中文别名是该站相对其它档案源的独特字段."""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import quote, urljoin, urlsplit

from parsel import Selector

from amane.enums import ActorGender, SiteName
from amane.plugins.models import SourceCapability

from ...base import CrawlerProfile
from ...parsing import extract_text
from ..base import ActorCrawler
from ..models import ActorMetadata

_ACTOR_PATH_RE = re.compile(r"^/actors/([A-Za-z0-9]+)/?$")
_ACTOR_INDEX_SLUGS = frozenset({"censored", "uncensored", "western"})
_BG_URL_RE = re.compile(r"url\(\s*['\"]?(https?://[^)'\"]+)['\"]?\s*\)", re.IGNORECASE)
_PLACEHOLDER_AVATAR_RE = re.compile(r"actor_unknow", re.IGNORECASE)
# 影片数标注随语言变化: zh「N 部影片」/ en「N movie(s)」; 不匹配会泄进 aliases.
_FILM_COUNT_RE = re.compile(r"\d+\s*(?:部影片|movie\(s\))", re.IGNORECASE)
_GENDER_TOKENS: dict[str, ActorGender] = {
    "男優": ActorGender.MALE,
    "男优": ActorGender.MALE,
    # javdb 无 locale cookie 时按 Accept-Language 走英文页, 性别标注为英文
    "Male": ActorGender.MALE,
    "女優": ActorGender.FEMALE,
    "女优": ActorGender.FEMALE,
    "Female": ActorGender.FEMALE,
}


class JavDBActorCrawler(ActorCrawler):
    """javdb.com 演员搜索 + 详情. 与影片 JavDBCrawler 共用 SiteName / site_config."""

    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(
            name=SiteName.JAVDB,
            base_url="https://javdb.com",
            capabilities=frozenset({SourceCapability.ACTOR_PROFILE}),
            genders=frozenset({ActorGender.FEMALE, ActorGender.MALE}),
        )

    async def _search(self, name: str) -> str | None:
        q = quote(name)
        search_url = f"{self.base_url}/search?q={q}&f=actor&locale=zh"
        text = await self.client.get_html(search_url, cookies=self.cookies)
        html = Selector(text=text)
        # 精确命中偶发落到详情页 (与 minnano 同型).
        if html.css("span.actor-section-name").get():
            detail = extract_text(html, '//link[@rel="canonical"]/@href')
            return detail or None
        return pick_javdb_actor_search_hit(html, name, base_url=self.base_url)

    async def _scrape(self, url: str) -> ActorMetadata | None:
        text = await self.client.get_html(url, cookies=self.cookies)
        return parse_javdb_actor_detail(text, page_url=url, base_url=self.base_url)


def pick_javdb_actor_search_hit(html: Selector, name: str, *, base_url: str) -> str | None:
    """精确匹配 canonical 名或 title 别名; 比较大小写不敏感 (javdb 搜索本身不区分大小写);
    不回退首条, 避免子串误伤."""
    needle = _norm(name).casefold()
    if not needle:
        return None
    for box in html.xpath('//div[contains(@class,"actor-box")]//a[@href]'):
        href = extract_text(box, "@href")
        actor_id = _actor_id_from_href(href)
        if not actor_id:
            continue
        canonical = _norm(extract_text(box, ".//strong/text()") or extract_text(box, "string(.//strong)"))
        aliases = _split_names(extract_text(box, "@title"))
        names = [n for n in [canonical, *aliases] if n]
        if any(_norm(n).casefold() == needle for n in names):
            return urljoin(base_url + "/", href)
    return None


def parse_javdb_actor_detail(html_text: str, *, page_url: str, base_url: str) -> ActorMetadata | None:
    """解析演员详情页 HTML → ActorMetadata."""
    html = Selector(text=html_text)
    raw_names = extract_text(html, "string(//span[contains(@class,'actor-section-name')])")
    names = _split_names(raw_names)
    if not names:
        return None
    name, extra = names[0], names[1:]

    aliases: list[str] = list(extra)
    gender: ActorGender | None = None
    for meta in html.xpath("//span[contains(@class,'section-meta')]"):
        raw = extract_text(meta, "string(.)")
        for token in _split_names(raw):
            if token in _GENDER_TOKENS:
                gender = _GENDER_TOKENS[token]
                continue
            if _FILM_COUNT_RE.search(token):
                continue
            aliases.append(token)
    if gender is None:
        # javdb 站点契约: 详情页只为男优标注性别 (zh「男優」/ en「Male」), 女优无标记, 未标注默认女.
        gender = ActorGender.FEMALE
    aliases = _dedupe_preserve([a for a in aliases if a != name])

    image_urls: list[str] = []
    style = extract_text(html, '//div[contains(@class,"actor-avatar")]//span[contains(@class,"avatar")]/@style')
    avatar = _background_image_url(style)
    if avatar and not _PLACEHOLDER_AVATAR_RE.search(avatar):
        image_urls.append(urljoin(base_url + "/", avatar))

    source = extract_text(html, '//link[@rel="canonical"]/@href') or page_url
    actor_id = _actor_id_from_href(urlsplit(source).path) or _actor_id_from_href(urlsplit(page_url).path)

    return ActorMetadata(
        name=name,
        aliases=aliases,
        gender=gender,
        image_urls=image_urls,
        source_url=source,
        provider_ids={"javdb": actor_id} if actor_id else {},
    )


def _norm(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip()


def _split_names(raw: str) -> list[str]:
    if not raw:
        return []
    return [p for p in (_norm(part) for part in re.split(r"[,，]", raw)) if p]


def _dedupe_preserve(values: list[str]) -> list[str]:
    out: list[str] = []
    for v in values:
        if v not in out:
            out.append(v)
    return out


def _actor_id_from_href(href: str) -> str | None:
    if not href:
        return None
    path = urlsplit(href).path if "://" in href else href
    m = _ACTOR_PATH_RE.match(path)
    if not m:
        return None
    slug = m.group(1)
    if slug.lower() in _ACTOR_INDEX_SLUGS:
        return None
    return slug


def _background_image_url(style: str) -> str | None:
    if not style:
        return None
    m = _BG_URL_RE.search(style)
    return m.group(1) if m else None
