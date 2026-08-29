"""minnano-av.com 演员详情解析: 名字行, 尺寸, 生日."""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

from parsel import Selector

from amane.enums import ActorGender, SiteName
from amane.parsing import split_actor_aliases
from amane.plugins.models import SourceCapability
from amane.utils.dates import normalize_calendar_date

from ...base import CrawlerProfile
from ...parsing import extract_text
from ..base import ActorCrawler
from ..models import ActorMetadata

# 名字行: ``名前（かな / ローマ字）``; 名字段可能带注记括号 (如 ``相川美羽(tenshigao)``),
# kana 排除括号字符以免注记组被吞入读音.
_NAME_RE = re.compile(r"^(?P<name>.+?)\s*[（(]\s*(?P<kana>[^/（）()]+?)\s*/\s*(?P<roma>[^）)]+?)\s*[）)]\s*$")
_FIGURE_RE = re.compile(
    r"T\s*(?P<t>\d+)\s*/\s*B\s*(?P<b>\d+)\s*"
    r"(?:\(\s*(?:<[^>]+>)?(?P<cup>[A-Z]+)(?:カップ)?(?:</[^>]+>)?\s*\))?\s*"
    r"/\s*W\s*(?P<w>\d+)\s*/\s*H\s*(?P<h>\d+)",
    re.IGNORECASE,
)
_CUP_RE = re.compile(r"([A-Z]+)カップ", re.IGNORECASE)
_ACTRESS_HREF_RE = re.compile(r"actress\d+\.html", re.IGNORECASE)


class MinnanoActorCrawler(ActorCrawler):
    """www.minnano-av.com 演员详情."""

    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(
            name=SiteName.MINNANO,
            base_url="https://www.minnano-av.com",
            capabilities=frozenset({SourceCapability.ACTOR_PROFILE}),
            genders=frozenset({ActorGender.FEMALE}),
        )

    async def _search(self, name: str) -> str | None:
        q = quote(name)
        search_url = f"{self.base_url}/search_result.php?search_scope=actress&search_word={q}&search=Go"
        text = await self.client.get_html(search_url, cookies=self.cookies)
        html = Selector(text=text)

        # 精确命中时常 302 到详情页 (curl/HttpClient 跟随重定向后 HTML 已是详情).
        if html.css("div.act-profile").get():
            detail = extract_text(html, '//link[@rel="canonical"]/@href') or extract_text(
                html, '//meta[@property="og:url"]/@content'
            )
            if detail:
                return detail
            # 回退: 从头像路径抠 id
            img = extract_text(html, '//div[contains(@class,"thumb")]//img[contains(@src,"p_actress")]/@src')
            if img:
                m = re.search(r"/(\d+)\.jpg", img)
                if m:
                    return f"{self.base_url}/actress{m.group(1)}.html"
            return None

        return self._pick_search_hit(html, name)

    def _pick_search_hit(self, html: Selector, name: str) -> str | None:
        """从搜索列表取首个合理命中: 精确名 > 首条演员行."""
        rows = html.xpath('//table[contains(@class,"tbllist") and contains(@class,"actress")]//tr[td]')
        exact: str | None = None
        first: str | None = None
        for row in rows:
            title = extract_text(row, './/h2[contains(@class,"ttl")]/a/text()')
            href = extract_text(row, './/h2[contains(@class,"ttl")]/a/@href')
            if not href or not _ACTRESS_HREF_RE.search(href):
                continue
            clean_href = href.split("?", 1)[0]
            url = urljoin(self.base_url + "/", clean_href)
            if first is None:
                first = url
            if title and title.strip() == name:
                exact = url
                break
        return exact or first

    async def _scrape(self, url: str) -> ActorMetadata | None:
        text = await self.client.get_html(url, cookies=self.cookies)
        return parse_minnano_detail(text, page_url=url, base_url=self.base_url)


def parse_minnano_detail(html_text: str, *, page_url: str, base_url: str) -> ActorMetadata | None:
    """解析详情页 HTML → ActorMetadata (供爬虫与单测复用)."""
    html = Selector(text=html_text)
    profiles = html.xpath('//div[contains(@class,"act-profile")]')
    if not profiles:
        return None
    profile = profiles[0]

    raw_name = extract_text(profile, ".//h2/text()") or extract_text(profile, ".//h2//text()")
    if not raw_name:
        return None
    name, aliases = _parse_name_line(raw_name)

    # 別名 行 (可重复): 每行一个旧名 + 读音, 与名字行同构解析 (含西序罗马音与注记后缀).
    for row in profile.xpath('.//tr[td/span[normalize-space()="別名"]]'):
        raw_alias = extract_text(row, "string(./td/p)") or ""
        alias_name, alias_readings = _parse_name_line(raw_alias.strip())
        if alias_name:
            aliases.append(alias_name)
            aliases.extend(alias_readings)
    aliases = _dedupe_preserve([a for a in aliases if a and a != name])

    fields = _profile_fields(profile)
    birthday = _parse_birthday(fields.get("生年月日", ""))
    birthplace = _plain(fields.get("出身地", "")) or None
    height, bust, waist, hip, cup = _parse_figure(fields.get("サイズ", ""))

    image = extract_text(html, '//meta[@property="og:image"]/@content') or extract_text(
        html, '//div[contains(@class,"thumb")]//img[contains(@src,"p_actress")]/@src'
    )
    image_urls: list[str] = []
    if image:
        image_urls.append(_stable_url(urljoin(base_url + "/", image)))

    source = (
        extract_text(html, '//link[@rel="canonical"]/@href')
        or extract_text(html, '//meta[@property="og:url"]/@content')
        or page_url
    )

    return ActorMetadata(
        name=name,
        aliases=aliases,
        birthday=birthday,
        birthplace=birthplace,
        height=height,
        bust=bust,
        waist=waist,
        hip=hip,
        cup=cup,
        image_urls=image_urls,
        source_url=source,
        provider_ids={"minnano": source} if source else {},
    )


def _parse_name_line(raw: str) -> tuple[str, list[str]]:
    text = unicodedata.normalize("NFKC", raw).strip()
    m = _NAME_RE.match(text)
    if not m:
        return text, []
    name = _strip_annotations(m.group("name").strip())
    aliases = [a.strip() for a in (m.group("kana"), m.group("roma")) if a and a.strip()]
    return name, aliases


def _strip_annotations(name: str) -> str:
    """剥离名字段尾部的注记括号 (如 ``相川美羽(tenshigao)``), 注记不进别名."""
    while True:
        stripped, _ = split_actor_aliases(name)
        if stripped == name:
            return name
        name = stripped


def _dedupe_preserve(values: list[str]) -> list[str]:
    out: list[str] = []
    for v in values:
        if v not in out:
            out.append(v)
    return out


def _profile_fields(profile: Selector) -> dict[str, str]:
    out: dict[str, str] = {}
    for row in profile.xpath(".//tr"):
        key = extract_text(row, "./td/span/text()")
        if not key:
            continue
        # 取 span 之后的文案 (含链接文字)
        val = extract_text(row, "string(./td/p)") or extract_text(row, "string(./td)")
        if val:
            # 去掉开头重复的 key 文本
            val = val.strip()
            if val.startswith(key):
                val = val[len(key) :].strip()
            out[key] = val
    return out


def _plain(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _parse_birthday(raw: str) -> str | None:
    return normalize_calendar_date(raw)


def _parse_figure(raw: str) -> tuple[int | None, int | None, int | None, int | None, str | None]:
    text = unicodedata.normalize("NFKC", raw)
    # 去掉 HTML 残留后再匹配纯文本
    text = re.sub(r"<[^>]+>", "", text)
    m = _FIGURE_RE.search(text)
    cup_m = _CUP_RE.search(text)
    cup = (m.group("cup") if m and m.group("cup") else None) or (cup_m.group(1) if cup_m else None)
    if cup:
        cup = cup.upper()
    if not m:
        return None, None, None, None, cup
    return (
        int(m.group("t")),
        int(m.group("b")),
        int(m.group("w")),
        int(m.group("h")),
        cup,
    )


def _stable_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
