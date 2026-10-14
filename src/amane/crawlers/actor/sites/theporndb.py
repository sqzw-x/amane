"""Stash-box searchPerformer; 与影片爬虫共用 api_token.

精确匹配 name / aliases, 不回退首条. deleted 跟随 merged_into_id, 无目标则丢弃.
头像按宽×高排序. urls 写入 provider_ids (跳过本站). disambiguation 为 tagline.
本站别名表会把本人的多个名字拼接为一条, 这类杂糅项按 token 判据丢弃 (见 `_drop_garbled_aliases`).
stash-box 的 death_date / 出道年 / 发色瞳色 / ethnicity / breast_type / 纹身穿孔不写入列.
"""

from __future__ import annotations

import re
from typing import Any

from amane.enums import ActorGender, SiteName
from amane.plugins.models import SourceCapability

from ...base import CrawlerProfile
from ...parsing import is_same_name, normalize_name
from ..base import ActorCrawler
from ..models import ActorMetadata

# 不用顶层 cup_size 等较新字段, 以免旧 stash-box 拒查询.
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

# 批量导入留下的编号后缀: ``Satin Sat008`` 与 ``Mina ID1927`` / ``Mina 1927``.
_IMPORT_CODE_RE = re.compile(r"^([A-Za-z][A-Za-z'\- ]*?)\s+[A-Za-z]{3}\d{2,4}$")
_ID_SUFFIX_RE = re.compile(r"^(.+?)\s+(?:ID\s*\d{3,6}|\d{4,6})$", re.IGNORECASE)


class ThePornDBActorCrawler(ActorCrawler):
    """无 token 不发请求. 精确匹配 name/aliases, 不允许回退首条."""

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
        # 跟随合并目标.
        resolved = await self._follow_merge(hit, headers)
        return performer_to_metadata(resolved) if resolved is not None else None

    async def _search(self, name: str) -> str | None:
        raise NotImplementedError("ThePornDBActorCrawler overrides fetch()")

    async def _scrape(self, url: str) -> ActorMetadata | None:
        raise NotImplementedError("ThePornDBActorCrawler overrides fetch()")

    async def _follow_merge(self, hit: dict[str, Any], headers: dict[str, str]) -> dict[str, Any] | None:
        """deleted 条目跟随 ``merged_into_id``; 无合并目标则丢弃. 环或过深视为未命中."""
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
            self.base_url, json={"query": query, "variables": variables}, headers=headers
        )
        if not isinstance(data, dict):
            return None
        payload = data.get("data")
        return payload if isinstance(payload, dict) else None


def pick_performer(results: list[Any], name: str) -> dict[str, Any] | None:
    """精确匹配 canonical 名或 aliases. 不允许回退首条."""
    for item in results:
        if not isinstance(item, dict):
            continue
        if any(is_same_name(n, name) for n in _performer_names(item)):
            return item
    return None


def performer_to_metadata(perf: dict[str, Any]) -> ActorMetadata | None:
    name = normalize_name(str(perf.get("name") or ""))
    if not name:
        return None

    raw_aliases = perf.get("aliases")
    alias_src = raw_aliases if isinstance(raw_aliases, list) else []
    aliases = _dedupe_preserve([a for a in (normalize_name(str(x)) for x in alias_src if x) if a != name])
    aliases = _drop_garbled_aliases(aliases, name)
    raw_meas = perf.get("measurements")
    meas = raw_meas if isinstance(raw_meas, dict) else {}
    performer_id = str(perf.get("id") or "").strip()
    birthday = perf.get("birth_date")
    tagline = normalize_name(str(perf.get("disambiguation") or "")) or None

    return ActorMetadata(
        name=name,
        aliases=aliases,
        gender=_GENDER.get(str(perf.get("gender") or "")),
        birthday=birthday if isinstance(birthday, str) else None,
        birthplace=normalize_name(str(perf.get("country") or "")) or None,
        height=_positive_int(perf.get("height")),
        bust=_positive_int(meas.get("band_size")),
        waist=_positive_int(meas.get("waist")),
        hip=_positive_int(meas.get("hip")),
        cup=normalize_name(str(meas.get("cup_size") or "")) or None,
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
        url = normalize_name(str(img.get("url") or ""))
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
        url = normalize_name(str(item.get("url") or ""))
        if not url or "theporndb.net" in url.casefold():
            continue
        key = _URL_TYPE_KEYS.get(normalize_name(str(item.get("type") or "")).casefold())
        if key is None or key in out:
            continue
        out[key] = url
    return out


def _performer_names(perf: dict[str, Any]) -> list[str]:
    names = [str(perf.get("name") or "")]
    aliases = perf.get("aliases") or []
    if isinstance(aliases, list):
        names.extend(str(a) for a in aliases if a)
    return [n for n in names if normalize_name(n)]


def _dedupe_preserve(values: list[str]) -> list[str]:
    out: list[str] = []
    for v in values:
        if v not in out:
            out.append(v)
    return out


def _drop_garbled_aliases(aliases: list[str], name: str) -> list[str]:
    """丢弃本站数据缺陷产生的杂糅别名.

    本站把本人的名字重复一遍 (``Himari Himari``), 与其他名字拼接为一条
    (``Himari Kinoshita Himari Hanazawa``), 或附带批量导入的编号 (``Satin Sat008``).
    判据只看 token, 无法切分的长别名 (``Cruz De La Vega De Paris``) 一律保留.

    同词集只丢弃后出现的那条 — 姓名换序的写法 (``Sasaki Aki``) 是与影片演员名匹配的实际形态,
    一条不剩时保留最后一条; 与 ``name`` 词集相同但词序不同的别名一律保留.
    """
    normalized = [relaxed for alias in aliases if (relaxed := _strip_import_code(alias, name)) is not None]
    signatures = [_token_signature(value) for value in normalized]
    kept: list[str] = []
    for position, (value, signature) in enumerate(zip(normalized, signatures, strict=True)):
        if value == name:
            continue
        tokens = _tokens(value)
        if _is_repeat(tokens, name):
            continue
        if _is_splice(tokens, name, kept):
            continue
        if _shares_signature(signature, kept) and signature in signatures[position + 1 :]:
            continue
        kept.append(value)
    return kept


def _strip_import_code(value: str, name: str) -> str | None:
    """删除批量导入留下的编号后缀; 编号底名不是本人名字时返回 None.

    两种形态都要求编号独立成段且与名字用空格分开, 以免删除 ``kickstand523`` 这类真实用户名:
    ``Satin Sat008`` (三字母前缀 + 2-4 位序号) 与 ``Mina ID1927`` / ``Mina 1927``.
    底名不符说明编号来自其他记录 (``Mina Luxx`` 的 ``Mina ID1927``), 整条丢弃.
    """
    match = _IMPORT_CODE_RE.match(value) or _ID_SUFFIX_RE.match(value)
    if match is None:
        return value
    base = match.group(1).strip()
    last = _tokens(name)[-1] if _tokens(name) else ""
    return base if _tokens(base)[:1] == [last] else None


def _is_repeat(tokens: list[str], name: str) -> bool:
    """``name`` 被重复了一遍: ``Himari Himari`` / ``Hitomi Hitomi Tanaka`` / ``Rei Mizuna Rei Mizuna``.

    要求别名只使用名字里的词、词序与名字一致、词数为名字的一到两倍. 后两条排除名字本身重复的
    介词 —— ``Cruz De La Vega De Paris`` 的 ``paris`` 不在名字里, 不命中.
    """
    name_tokens = _tokens(name)
    if not len(name_tokens) < len(tokens) <= 2 * len(name_tokens):
        return False
    if not set(tokens) <= set(name_tokens):
        return False
    return _contains_in_order(tokens, name_tokens)


def _contains_in_order(tokens: list[str], expected: list[str]) -> bool:
    """``expected`` 是否为 ``tokens`` 的子序列 (贪心扫描, 不允许回溯)."""
    cursor = 0
    for want in expected:
        while cursor < len(tokens) and tokens[cursor] != want:
            cursor += 1
        if cursor == len(tokens):
            return False
        cursor += 1
    return True


def _shares_signature(signature: frozenset[str], known: list[str]) -> bool:
    """词集与本人另一个别名相同 (仅词序或标点不同)."""
    return any(signature == _token_signature(other) for other in known)


def _is_splice(tokens: list[str], name: str, known: list[str]) -> bool:
    """别名由本人两个已有名字拼接而成, 两个片段各自的词可以交错.

    ``Kinoshita Himari Hanazawa Himari`` 命中: 切分为 ``Kinoshita Himari`` 与 ``Hanazawa Himari``.
    每个片段至少 2 个词, 以免把单字词当作名字; 两个片段不能都匹配时保留.
    """
    signatures = {_token_signature(other) for other in (name, *known)}
    if len(tokens) < 4:
        return False
    return any(
        frozenset(tokens[:cut]) in signatures and frozenset(tokens[cut:]) in signatures
        for cut in range(2, len(tokens) - 1)
    )


def _tokens(value: str) -> list[str]:
    return re.findall(r"[\w']+", value.casefold())


def _token_signature(value: str) -> frozenset[str]:
    return frozenset(_tokens(value))


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value
