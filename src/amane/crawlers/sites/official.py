"""按系列前缀或前序 studio 路由到制作商官网.

优先级: ``config.official_routes`` → ``MANUFACTURER_SERIES`` → ``MANUFACTURER_ALIASES``.
Outvision/Will 集团共用同一 CMS, 详情页 HTML 结构一致.
"""

import re
from enum import StrEnum
from typing import TYPE_CHECKING, override

from parsel import Selector

from ...enums import SiteName
from ..base import Crawler, CrawlerProfile
from ..models import FetchOptions, MediaMetadata, SearchQuery, film_actors
from ..parsing import extract_all_texts, extract_text

if TYPE_CHECKING:
    from ...config import SiteConfig


class Manufacturer(StrEnum):
    ATTACKERS = "attackers"
    BEFREE = "befree"
    BI = "bi"
    BIBIAN = "bibian"
    DAS = "das"
    EBODY = "ebody"
    FITCH = "fitch"
    HAJIME_KIKAKU = "hajime_kikaku"
    HONNAKA = "honnaka"
    HUNTER = "hunter"
    IDEA_POCKET = "idea_pocket"
    KAWAII = "kawaii"
    KIRAKIRA = "kirakira"
    MADONNA = "madonna"
    MIMAN = "miman"
    MKO_LABO = "mko_labo"
    MOODYZ = "moodyz"
    MUKU = "muku"
    MVG = "mvg"
    NANPA_JAPAN = "nanpa_japan"
    OPPAI = "oppai"
    OPERA = "opera"
    PREMIUM_BEAUTY = "premium_beauty"
    ROOKIE = "rookie"
    S1 = "s1"
    TAMEIKEGORO = "tameikegoro"
    TOSATSU = "tosatsu"
    V = "v"
    WANZ_FACTORY = "wanz_factory"


MANUFACTURER_DOMAINS: dict[Manufacturer, str] = {
    Manufacturer.ATTACKERS: "attackers.net",
    Manufacturer.BEFREE: "befreebe.com",
    Manufacturer.BI: "bi-av.com",
    Manufacturer.BIBIAN: "bibian-av.com",
    Manufacturer.DAS: "dasdas.jp",
    Manufacturer.EBODY: "www.av-e-body.com",
    Manufacturer.FITCH: "fitch-av.com",
    Manufacturer.HAJIME_KIKAKU: "hajimekikaku.com",
    Manufacturer.HONNAKA: "honnaka.jp",
    Manufacturer.HUNTER: "hhh-av.com",
    Manufacturer.IDEA_POCKET: "ideapocket.com",
    Manufacturer.KAWAII: "kawaiikawaii.jp",
    Manufacturer.KIRAKIRA: "kirakira-av.com",
    Manufacturer.MADONNA: "www.madonna-av.com",
    Manufacturer.MIMAN: "miman.jp",
    Manufacturer.MKO_LABO: "mko-labo.net",
    Manufacturer.MOODYZ: "moodyz.com",
    Manufacturer.MUKU: "muku.tv",
    Manufacturer.MVG: "mvg.jp",
    Manufacturer.NANPA_JAPAN: "nanpa-japan.jp",
    Manufacturer.OPPAI: "oppai-av.com",
    Manufacturer.OPERA: "av-opera.jp",
    Manufacturer.PREMIUM_BEAUTY: "premium-beauty.com",
    Manufacturer.ROOKIE: "rookie-av.jp",
    Manufacturer.S1: "s1s1s1.com",
    Manufacturer.TAMEIKEGORO: "tameikegoro.jp",
    Manufacturer.TOSATSU: "to-satsu.com",
    Manufacturer.V: "v-av.com",
    Manufacturer.WANZ_FACTORY: "www.wanz-factory.com",
}


MANUFACTURER_SERIES: dict[Manufacturer, list[str]] = {
    Manufacturer.S1: ["sivr", "ssis", "ssni", "snis", "soe", "oned", "one", "onsd", "ofje", "sps", "tksoe"],
    Manufacturer.MOODYZ: [
        "mdvr",
        "midv",
        "mide",
        "midd",
        "mibd",
        "mimk",
        "miid",
        "migd",
        "mifd",
        "miae",
        "miad",
        "miaa",
        "mdl",
        "mdj",
        "mdi",
        "mdg",
        "mdf",
        "mde",
        "mdld",
        "mded",
        "mizd",
        "mird",
        "mdjd",
        "rmid",
        "mdid",
        "mdmd",
        "mimu",
        "mdpd",
        "mivd",
        "mdud",
        "mdgd",
        "mdvd",
        "mias",
        "miqd",
        "mint",
        "rmpd",
        "mdrd",
        "tkmide",
        "tkmidd",
        "kmide",
        "tkmigd",
        "mdfd",
        "rmwd",
        "miab",
    ],
    Manufacturer.MADONNA: [
        "juvr",
        "jusd",
        "juq",
        "juy",
        "jux",
        "jul",
        "juk",
        "juc",
        "jukd",
        "oba",
        "roeb",
        "roe",
        "ure",
        "mdon",
        "obe",
        "jums",
    ],
    Manufacturer.WANZ_FACTORY: ["wavr", "waaa", "bmw", "wanz"],
    Manufacturer.IDEA_POCKET: [
        "ipvr",
        "ipx",
        "ipz",
        "iptd",
        "ipsd",
        "idbd",
        "supd",
        "ipit",
        "and",
        "hpd",
        "tkipz",
        "ipzz",
        "cosd",
        "anpd",
        "dan",
        "alad",
        "kipx",
    ],
    Manufacturer.KIRAKIRA: ["kivr", "blk", "kibd", "kifd", "kird", "kisd", "set"],
    Manufacturer.EBODY: ["ebvr", "ebod", "mkck", "eyan"],
    Manufacturer.BI: ["cjvr", "cjod", "bbi", "bib", "cjob", "beb", "bid", "bist", "bwb"],
    Manufacturer.PREMIUM_BEAUTY: ["prvr", "pgd", "pred", "pbd", "pjd", "prtd", "pxd", "pid", "ptv"],
    Manufacturer.MIMAN: ["mmvr", "mmnd", "mmxd", "aom"],
    Manufacturer.TAMEIKEGORO: ["mevr", "meyd", "mbyd", "mdyd", "mnyd"],
    Manufacturer.FITCH: ["fcvr", "jufe", "jufd", "jfb", "juny", "nyb", "finh", "gcf", "nima"],
    Manufacturer.KAWAII: ["kavr", "cawd", "kwbd", "kawd", "kwsr", "kwsd", "kane"],
    Manufacturer.BEFREE: ["bf"],
    Manufacturer.MUKU: ["mucd", "mudr", "mukd", "smcd", "mukc"],
    Manufacturer.ATTACKERS: [
        "atvr",
        "rbk",
        "rbd",
        "same",
        "shkd",
        "atid",
        "adn",
        "atkd",
        "jbd",
        "sspd",
        "atad",
        "azsd",
    ],
    Manufacturer.MKO_LABO: ["mvr", "mism", "emlb"],
    Manufacturer.DAS: ["dsvr", "dass", "dazd", "dasd", "pla"],
    Manufacturer.MVG: ["mvbd", "mvsd"],
    Manufacturer.OPERA: ["opvr", "opbd", "opud"],
    Manufacturer.OPPAI: ["ppvr", "pppe", "ppbd", "pppd", "ppsd", "ppfd"],
    Manufacturer.V: ["vvvd", "vicd", "vizd", "vspd"],
    Manufacturer.TOSATSU: ["clvr", "stol", "club"],
    Manufacturer.BIBIAN: ["bbvr", "bban"],
    Manufacturer.HONNAKA: ["hnvr", "hmn", "hndb", "hnd", "krnd", "hnky", "hnjc", "hnse"],
    Manufacturer.ROOKIE: ["rvr", "rbb", "rki"],
    Manufacturer.NANPA_JAPAN: ["njvr", "nnpj", "npjb"],
    Manufacturer.HAJIME_KIKAKU: ["hjbb", "hjmo", "avgl"],
    Manufacturer.HUNTER: ["huntb", "hunta", "hunt", "hunbl", "royd", "tysf"],
}


MANUFACTURER_ALIASES: dict[Manufacturer, list[str]] = {
    Manufacturer.ATTACKERS: ["Attackers", "Atakkaazu", "アタッカーズ"],
    Manufacturer.BEFREE: ["BeFree"],
    Manufacturer.BI: ["痴女Heaven", "痴女ヘブン", "痴女天堂", "Bi", "美"],
    Manufacturer.BIBIAN: ["ビビアン", "Bibian"],
    Manufacturer.DAS: ["Das!", "ダスッ！", "DAS"],
    Manufacturer.EBODY: ["E-BODY", "E-body", "イーボディー", "イー-ボディー"],
    Manufacturer.FITCH: ["Fitch", "フィッチ"],
    Manufacturer.HAJIME_KIKAKU: ["はじめ企画", "はじめきかく", "Hajime Kikaku"],
    Manufacturer.HONNAKA: ["本中", "ほんなか"],
    Manufacturer.HUNTER: ["Hunter", "HHH"],
    Manufacturer.IDEA_POCKET: ["IDEA POCKET", "IDEAPOCKET", "Idea Pocket", "アイデアポケット"],
    Manufacturer.KAWAII: ["kawaii", "kawaii*", "カワイイ*"],
    Manufacturer.KIRAKIRA: ["kira☆kira", "kira*kira"],
    Manufacturer.MADONNA: ["Madonna", "マドンナ"],
    Manufacturer.MIMAN: ["未満", "Miman"],
    Manufacturer.MKO_LABO: ["えむっ娘ラボ", "Mko Labo"],
    Manufacturer.MOODYZ: ["MOODYZ", "ムーディーズ"],
    Manufacturer.MUKU: ["無垢"],
    Manufacturer.MVG: ["エムズ・ビデオ・グループ", "エムズビデオグループ", "M's Video Group", "MVG"],
    Manufacturer.NANPA_JAPAN: ["ナンパJAPAN", "Nanpa Japan"],
    Manufacturer.OPPAI: ["OPPAI"],
    Manufacturer.OPERA: ["OPERA", "Opera"],
    Manufacturer.PREMIUM_BEAUTY: ["プレミアム", "Premium Beauty", "PREMIUM", "Premium"],
    Manufacturer.ROOKIE: ["Rookie"],
    Manufacturer.S1: ["S1 NO.1 STYLE", "S1", "エスワン", "S1 No.1 Style"],
    Manufacturer.TAMEIKEGORO: ["溜池ゴロー", "溜池五郎", "溜池GORO", "Tameikegoro"],
    Manufacturer.TOSATSU: ["変態紳士倶楽部", "变态绅士俱乐部"],
    Manufacturer.V: ["ヴィ", "V"],
    Manufacturer.WANZ_FACTORY: ["ワンズファクトリー", "Wanz Factory"],
}


def _build_series_index() -> dict[str, Manufacturer]:
    index: dict[str, Manufacturer] = {}
    for maker, prefixes in MANUFACTURER_SERIES.items():
        for prefix in prefixes:
            index[prefix] = maker
    return index


def _build_alias_index() -> dict[str, Manufacturer]:
    index: dict[str, Manufacturer] = {}
    for maker, aliases in MANUFACTURER_ALIASES.items():
        for alias in aliases:
            key = alias.lower()
            if key not in index:
                index[key] = maker
    return index


_SERIES_TO_MAKER: dict[str, Manufacturer] = _build_series_index()
_ALIAS_TO_MAKER: dict[str, Manufacturer] = _build_alias_index()


def _extract_series_prefix(number: str) -> str:
    cleaned = number.replace("-", "").replace("_", "").lstrip("0123456789")
    match = re.match(r"^([a-zA-Z]+)", cleaned)
    return match.group(1).lower() if match else ""


class OfficialCrawler(Crawler):
    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(
            name=SiteName.OFFICIAL, base_url="", urls=[f"https://{d}" for d in MANUFACTURER_DOMAINS.values()]
        )

    async def _search(self, query: SearchQuery, options: FetchOptions | None = None) -> str | None:
        domain = self._resolve_domain(query, self.config)
        if not domain:
            return None

        number = query.number.upper().replace("-", "").lower()
        return f"https://{domain}/works/detail/{number}"

    @override
    async def _scrape(self, url: str, options: FetchOptions | None = None) -> MediaMetadata | None:
        text = await self.client.get_html(url)

        html = Selector(text=text)

        number = self._extract_number(html)
        if not number:
            self.logger.warning("extract number failed")
            return None

        title = self._extract_title(html)

        # 仅取作品信息表「女優」栏; 推荐作品区也有 /actress/detail/ 链接, 不允许用整页 XPath.
        actors = extract_all_texts(
            html,
            '//div[@class="th"][contains(text(),"女優")]'
            '/following-sibling::div[@class="td"]'
            '//a[contains(@href,"/actress/detail/")]/text()',
        )
        seen: set[str] = set()
        actors = [a for a in actors if not (a in seen or seen.add(a))]

        release = extract_text(
            html, '//div[@class="th"][contains(text(),"発売日")]/following-sibling::div[@class="td"]//a/text()'
        )

        runtime_text = extract_text(
            html, '//div[@class="th"][contains(text(),"収録時間")]/following-sibling::div[@class="td"]//p/text()'
        )
        runtime = self._parse_minutes(runtime_text) if runtime_text else None

        tags = extract_all_texts(
            html,
            '//div[@class="th"][contains(text(),"ジャンル")]'
            '/following-sibling::div[@class="td"]'
            '//a[contains(@href,"/works/list/genre/")]/text()',
        )

        # 官网只提供横版封面, 无竖版海报; thumb 取作品轮播首图.
        thumb_url = html.xpath(
            '//div[contains(@class,"p-slider")]//img[contains(@class,"swiper-lazy")]/@data-src'
        ).get()

        series = extract_text(
            html, '//div[@class="th"][contains(text(),"シリーズ")]/following-sibling::div[@class="item"]//a/text()'
        )

        studio = self._extract_studio(html)

        return MediaMetadata(
            number=number,
            title=title or None,
            actors=film_actors(actors),
            studio=studio or None,
            release=release or None,
            runtime=runtime,
            tags=tags,
            series=series or None,
            thumb_urls=[thumb_url] if thumb_url else [],
            source_url=url,
        )

    def _resolve_domain(self, query: SearchQuery, config: SiteConfig | None = None) -> str | None:
        number = query.number.upper()

        routes: dict[str, Manufacturer] = {}
        if config and config.official_routes:
            routes = config.official_routes

        # 用户配置的番号前缀.
        for prefix, manufacturer in routes.items():
            if number.startswith(prefix.upper()):
                self.logger.debug(
                    "official route: user config matched", number=number, prefix=prefix, manufacturer=manufacturer
                )
                return MANUFACTURER_DOMAINS[manufacturer]

        # 系列前缀.
        series_prefix = _extract_series_prefix(number)
        maker = _SERIES_TO_MAKER.get(series_prefix)
        if maker:
            domain = MANUFACTURER_DOMAINS[maker]
            self.logger.debug(
                "official route: series prefix matched",
                number=number,
                prefix=series_prefix,
                maker=maker,
                domain=domain,
            )
            return domain

        # 前序 studio 别名.
        if query.partial_result and query.partial_result.studio:
            studio = query.partial_result.studio
            maker, matched_alias = self._resolve_by_studio(studio)
            if maker:
                domain = MANUFACTURER_DOMAINS[maker]
                self.logger.debug(
                    "official route: studio alias matched",
                    number=number,
                    studio=studio,
                    alias=matched_alias,
                    maker=maker,
                    domain=domain,
                )
                return domain

        self.logger.debug(
            "official route: no match",
            number=number,
            prefix=series_prefix,
            studio=(query.partial_result and query.partial_result.studio) or None,
        )
        return None

    @staticmethod
    def _resolve_by_series(number: str) -> Manufacturer | None:
        prefix = _extract_series_prefix(number)
        return _SERIES_TO_MAKER.get(prefix)

    @staticmethod
    def _resolve_by_studio(studio_name: str) -> tuple[Manufacturer | None, str | None]:
        key = studio_name.strip().lower()
        if key in _ALIAS_TO_MAKER:
            return _ALIAS_TO_MAKER[key], key
        # 子串互含: 别名在 studio 中, 或 studio 在别名中.
        for alias_lower, maker in _ALIAS_TO_MAKER.items():
            if alias_lower in key or key in alias_lower:
                return maker, alias_lower
        return None, None

    @staticmethod
    def _extract_number(html: Selector) -> str:
        # 品番栏: div.th + div.td > p > span(DVD/BD) + text; 号码在 span 之后.
        raw = (
            html.xpath(
                '//div[@class="th"][contains(text(),"品番")]/following-sibling::div[@class="td"]//p/text()'
            ).get()
            or ""
        ).strip()
        if not raw:
            return ""
        # number format: "SSIS960" (may have leading DVD/BD span text)
        match = re.search(r"([A-Z]+[\d]+)", raw)
        return match.group(1) if match else raw

    @staticmethod
    def _extract_title(html: Selector) -> str:
        raw = (html.xpath("//title/text()").get() or "").strip()
        if " | " in raw:
            raw = raw.rsplit(" | ", 1)[0]
        return raw

    @staticmethod
    def _extract_studio(html: Selector) -> str | None:
        raw = (html.xpath("//title/text()").get() or "").strip()
        if " | " in raw:
            suffix = raw.rsplit(" | ", 1)[1]
            # title 后缀形如「…【S1 NO.1 STYLE (エスワン…)】公式サイト」; 取【】内主名称.
            match = re.search(r"【(.+?)】", suffix)
            if match:
                inner = match.group(1)
                return inner.split("(")[0].strip()
        return None

    @staticmethod
    def _parse_minutes(text: str) -> int | None:
        if not text:
            return None
        match = re.search(r"(\d+)", text)
        return int(match.group(1)) if match else None
