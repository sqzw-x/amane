"""数据落在页面内嵌的 Next.js 服务端结构, 站点无公开 API (``/api`` 404 且 robots 禁止), 因此解析 ``__NEXT_DATA__`` 而不做 DOM 启发式.

番号不能直达: ``/works/{番号}`` 返回 404, 规范条目是 ``/works/{prefix}:{work_id}``. 检索 ``/works?q=``
是模糊匹配, 取首条会误命中同前缀的其它作品, 因此只接受 ``work_id`` 归一后相等的候选.
站点对分隔符的容忍度不一致 (有的番号去掉短横线可检索到, 有的必须带), 故逐个尝试候选查询形态.

``casts[].actor.note`` 是唯一性别线索, 只有 ``男優`` 判为男优, 其余保持 ``unknown`` —— 自由文本无法
推出女优. 封面 / 剧照 / 预告全部指向 DMM CDN, ``iteminfo.description`` 是截断文本, 均不取.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

from amane.enums import ActorGender, SiteName

from ..base import Crawler, CrawlerProfile
from ..models import FetchOptions, FilmActor, MediaMetadata, SearchQuery
from ..parsing import next_data_props

# JS Date.toString(): "Thu Feb 18 2021 09:00:00 GMT+0900 (Japan Standard Time)"; 星期前缀可选.
_JS_DATE_RE = re.compile(r"^(?:[A-Za-z]{3} )?(?P<mon>[A-Za-z]{3}) (?P<day>\d{1,2}) (?P<year>\d{4})\b")
# iteminfo.volume 兼有分钟数字串与时长时钟串两种形态.
_CLOCK_RE = re.compile(r"^(?P<h>\d+):(?P<min>\d{2}):(?P<sec>\d{2})$")
# 无分隔符番号插入短横线: 字母结尾 + 数字尾段 (SSIS497 → SSIS-497); 含 "_" 的日期号不匹配.
_DASHABLE_RE = re.compile(r"^(?P<head>.*[A-Za-z])(?P<digits>\d+)$")
_MONTHS: dict[str, int] = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}
_MALE_NOTE = "男優"


class AvbaseCrawler(Crawler):
    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(name=SiteName.AVBASE, base_url="https://www.avbase.net")

    async def _search(self, query: SearchQuery, options: FetchOptions | None = None) -> str | None:
        number = query.number.strip()
        if not number:
            return None
        key = work_key(number)
        if not key:
            return None
        for form in query_forms(number):
            url = await self._search_form(form, key)
            if url:
                return url
        return None

    async def _search_form(self, term: str, key: str) -> str | None:
        text = await self.client.get_html(f"{self.base_url}/works?q={quote(term)}", cookies=self.cookies)
        props = next_data_props(text)
        works = props.get("works") if props else None
        if not isinstance(works, list):
            return None
        candidates = [w for w in works if isinstance(w, dict) and work_key(_text(w.get("work_id"))) == key]
        picked = pick_candidate(candidates, key)
        if picked is None:
            return None
        return work_url(self.base_url, _text(picked.get("prefix")), _text(picked.get("work_id")))

    async def _scrape(self, url: str, options: FetchOptions | None = None) -> MediaMetadata | None:
        text = await self.client.get_html(url, cookies=self.cookies)
        props = next_data_props(text)
        work = props.get("work") if props else None
        if not isinstance(work, dict):
            return None
        return build_metadata(work, url=url)


def work_url(base_url: str, prefix: str, work_id: str) -> str:
    """规范条目 URL: 有 prefix 时 ``/works/{prefix}:{work_id}``, 无 prefix 时 ``/works/{work_id}``."""
    path = f"{quote(prefix, safe='')}:{quote(work_id, safe='')}" if prefix else quote(work_id, safe="")
    return f"{base_url}/works/{path}"


def query_forms(number: str) -> list[str]:
    """检索候选形态: 原样 → 去分隔符 → 补短横线. 顺序即尝试顺序, 重复项丢弃.

    站点对同一形态的处理不一致, 因此不能只发一种; 补短横线只在原串没有短横线时产生新形态.
    """
    forms: list[str] = []
    for form in (number, number.replace("-", "").replace(" ", "")):
        if form and form not in forms:
            forms.append(form)
    if "-" not in number:
        match = _DASHABLE_RE.fullmatch(number.replace(" ", ""))
        if match and (dashed := f"{match.group('head')}-{match.group('digits')}") not in forms:
            forms.append(dashed)
    return forms


def work_key(value: str) -> str:
    """番号归一: 去除分隔符与大小写差异, 并消除数字段的前导零 (SSIS-049 与 SSIS-49 同一部)."""
    compact = re.sub(r"[^0-9a-z]+", "", value.casefold())
    return "".join(str(int(part)) if part.isdigit() else part for part in re.findall(r"\d+|[a-z]+", compact))


def pick_candidate(candidates: list[dict[str, Any]], key: str) -> dict[str, Any] | None:
    """同 ``work_id`` 多条目时择一: 先取 products 含本番号的候选, 再取无 prefix 的规范条目.

    两个信号都无法唯一确定时返回 ``None`` —— 站点不存在把无关番号拼成详情页的行为, 但名寄せ前缀会
    让不同作品共用同一个 ``work_id``, 猜错等于把无关作品写成目标番号.
    """
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    ranked = [w for w in candidates if has_number_product(w, key)]
    pool = ranked or candidates
    if len(pool) == 1:
        return pool[0]
    plain = [w for w in pool if not _text(w.get("prefix"))]
    return plain[0] if len(plain) == 1 else None


def has_number_product(work: dict[str, Any], key: str) -> bool:
    """同一作品的多个 SKU 商品 (midv00852 / midv852 / midv852bod) 同在一份 products 里, 因此本判据只在多个 ``work`` 候选之间打破平局.

    命中可信, 未命中不构成对候选的否定: FANZA content_id 带前导数字的厂牌 (118abf355 / 1dldss00529 /
    1jimmy011) 归一后不等于番号, 实测 29 件有 FANZA 商品的作品中仅 21 件命中. ``pick_candidate`` 因此取
    ``ranked or candidates``, 不得改为按本判据过滤 —— 那样会把正确条目判掉.
    """
    products = work.get("products")
    if not isinstance(products, list):
        return False
    return any(work_key(_text(p.get("product_id"))) == key for p in products if isinstance(p, dict))


def build_metadata(work: dict[str, Any], *, url: str) -> MediaMetadata | None:
    number = _text(work.get("work_id"))
    if not number:
        return None
    raw_products = work.get("products")
    products = [p for p in raw_products if isinstance(p, dict)] if isinstance(raw_products, list) else []
    return MediaMetadata(
        number=number,
        title=_text(work.get("title")) or None,
        actors=_build_actors(work.get("casts")),
        studio=_product_name(products, "maker"),
        publisher=_product_name(products, "label"),
        series=_product_name(products, "series"),
        release=parse_js_date(_text(work.get("min_date"))),
        runtime=first_runtime(products),
        tags=_build_tags(work.get("genres")),
        directors=_build_directors(products),
        source_url=url,
    )


def _build_actors(casts: object) -> list[FilmActor]:
    if not isinstance(casts, list):
        return []
    actors: list[FilmActor] = []
    for entry in casts:
        if not isinstance(entry, dict):
            continue
        actor = entry.get("actor")
        if not isinstance(actor, dict):
            continue
        name = _text(actor.get("name"))
        if not name:
            continue
        gender = ActorGender.MALE if _text(actor.get("note")) == _MALE_NOTE else ActorGender.UNKNOWN
        actors.append(FilmActor(name=name, gender=gender))
    return actors


def _build_tags(genres: object) -> list[str]:
    if not isinstance(genres, list):
        return []
    return [name for g in genres if isinstance(g, dict) and (name := _text(g.get("name")))]


def _build_directors(products: list[dict[str, Any]]) -> list[str]:
    for product in products:
        director = _iteminfo(product, "director")
        if director:
            return [director]
    return []


def first_runtime(products: list[dict[str, Any]]) -> int | None:
    for product in products:
        runtime = parse_runtime(_iteminfo(product, "volume"))
        if runtime is not None:
            return runtime
    return None


def _product_name(products: list[dict[str, Any]], key: str) -> str | None:
    for product in products:
        named = product.get(key)
        if isinstance(named, dict) and (name := _text(named.get("name"))):
            return name
    return None


def _iteminfo(product: dict[str, Any], key: str) -> str:
    iteminfo = product.get("iteminfo")
    if not isinstance(iteminfo, dict):
        return ""
    return _text(iteminfo.get(key))


def parse_runtime(value: str) -> int | None:
    """``"143"`` 取分钟数; ``"1:49:00"`` 按时钟串换算; 其它形态不猜."""
    if not value:
        return None
    if value.isdigit():
        return int(value)
    match = _CLOCK_RE.fullmatch(value)
    if not match:
        return None
    return int(match.group("h")) * 60 + int(match.group("min"))


def parse_js_date(value: str) -> str | None:
    """``min_date`` 只有 JS ``Date.toString()`` 一种形态 (全部样本无反例); 正则止于年份, 不依赖时刻分量."""
    match = _JS_DATE_RE.match(value)
    if not match:
        return None
    month = _MONTHS.get(match.group("mon").casefold())
    if month is None:
        return None
    return f"{int(match.group('year')):04d}-{month:02d}-{int(match.group('day')):02d}"


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""
