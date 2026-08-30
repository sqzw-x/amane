"""AVSOX (avsox.click) 爬虫 — Vue SPA, 元数据走 Yii JSON API."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

from parsel import Selector
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from ...enums import Language, SiteName
from ..base import Crawler, CrawlerProfile
from ..models import FetchOptions, MediaMetadata, SearchQuery

_LANG: dict[Language, str] = {
    Language.ZH_CN: "cn",
    Language.ZH_TW: "tw",
    Language.JP: "ja",
    Language.EN: "en",
}

_MOVIE_PATH = re.compile(r"/(?:(?P<lang>cn|ja|en|tw)/)?(?:movies|movie)/(?P<id>[A-Za-z0-9]+)")
_NAME_SUFFIXES = ("", "_ja", "_en", "_cn", "_tw")


def _first(*values: object) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _csrf_token(html: str) -> str | None:
    token = Selector(text=html).xpath('//meta[@name="csrf-token"]/@content').get()
    token = (token or "").strip()
    return token or None


def _unwrap(envelope: object) -> object:
    if not isinstance(envelope, dict) or envelope.get("code") != 200:
        return None
    return envelope.get("data")


def _named(entity: AvsoxNamed | None, prefix: str) -> str | None:
    if entity is None:
        return None
    payload = entity.model_dump()
    return _first(*(payload.get(f"{prefix}Name{suffix}") for suffix in _NAME_SUFFIXES))


class AvsoxNamed(BaseModel):
    """演员 / 片商 / 类型等带语言后缀的名字对象."""

    model_config = ConfigDict(extra="allow")


class AvsoxMovie(BaseModel):
    model_config = ConfigDict(extra="ignore")
    movieId: str = ""
    movieFanHao: str = ""
    title: str = ""
    title_ja: str = ""
    title_en: str = ""
    title_cn: str = ""
    title_tw: str = ""
    releaseDate: str | None = None
    length: int | None = None
    description: str | None = None
    description_ja: str | None = None
    description_en: str | None = None
    description_cn: str | None = None
    description_tw: str | None = None
    posterSmall: str | None = None
    posterLarge: str | None = None
    sampleLarge: list[str] = Field(default_factory=list)
    studio: AvsoxNamed | None = None
    star: list[AvsoxNamed] = Field(default_factory=list)
    genre: list[AvsoxNamed] = Field(default_factory=list)
    director: AvsoxNamed | None = None
    label: AvsoxNamed | None = None
    series: AvsoxNamed | None = None

    @field_validator("studio", "director", "label", "series", mode="before")
    @classmethod
    def _coerce_entity(cls, value: object) -> object:
        if isinstance(value, list):
            return value[0] if value else None
        return value

    @field_validator("star", "genre", "sampleLarge", mode="before")
    @classmethod
    def _coerce_list(cls, value: object) -> object:
        return value if isinstance(value, list) else []


class AvsoxCrawler(Crawler):
    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(name=SiteName.AVSOX, base_url="https://avsox.click")

    def _lang(self, options: FetchOptions | None) -> str:
        language = options.language if options else None
        if language is None:
            return "cn"
        return _LANG.get(language, "cn")

    async def _api(self, method: str, payload: list[Any], *, referer: str) -> object:
        html = await self.client.get_html(f"{self.base_url}/cn/")
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Origin": self.base_url,
            "Referer": referer,
            "X-Requested-With": "XMLHttpRequest",
        }
        token = _csrf_token(html)
        if token:
            headers["X-CSRF-Token"] = token
        envelope = await self.client.post_json(
            f"{self.base_url}/javu/data/api/{method}",
            json=payload,
            headers=headers,
        )
        return _unwrap(envelope)

    async def _search(self, query: SearchQuery, options: FetchOptions | None = None) -> str | None:
        lang = self._lang(options)
        number = query.number
        referer = f"{self.base_url}/{lang}/search/{quote(number, safe='')}"
        data = await self._api("search", [{"search": number, "lang": lang}, 60, 1], referer=referer)
        if not isinstance(data, list):
            return None
        movie_id = _pick_movie_id(data, number)
        if not movie_id:
            return None
        return f"{self.base_url}/{lang}/movies/{movie_id}"

    async def _scrape(self, url: str, options: FetchOptions | None = None) -> MediaMetadata | None:
        movie_id, lang = _parse_movie_url(url)
        if not movie_id:
            return None
        lang = lang or self._lang(options)
        data = await self._api("getMovie", [movie_id, lang], referer=url)
        if not isinstance(data, dict):
            return None
        try:
            movie = AvsoxMovie.model_validate(data)
        except ValidationError:
            return None
        if not movie.movieFanHao:
            return None
        director = _named(movie.director, "director")
        return MediaMetadata(
            number=movie.movieFanHao,
            title=_first(movie.title, movie.title_ja, movie.title_en, movie.title_cn, movie.title_tw),
            actors=[name for star in movie.star if (name := _named(star, "star"))],
            studio=_named(movie.studio, "studio"),
            publisher=_named(movie.label, "label"),
            series=_named(movie.series, "series"),
            directors=[director] if director else [],
            release=movie.releaseDate or None,
            runtime=movie.length or None,
            tags=[name for genre in movie.genre if (name := _named(genre, "genre"))],
            plot=_first(
                movie.description,
                movie.description_cn,
                movie.description_ja,
                movie.description_en,
                movie.description_tw,
            ),
            poster_urls=[movie.posterSmall] if movie.posterSmall else [],
            thumb_urls=[movie.posterLarge] if movie.posterLarge else [],
            extrafanart=[item for item in movie.sampleLarge if item],
            source_url=url,
            external_id=movie.movieId or None,
        )


def _fold_number(number: str) -> str:
    """大小写 + 短横线 / 空格. 不折叠下划线: 010115_001 与 010115-001 是两部片."""
    return number.casefold().replace("-", "").replace(" ", "")


def _pick_movie_id(movies: list[object], number: str) -> str | None:
    exact = number.casefold()
    folded = _fold_number(number)
    folded_hit: str | None = None
    for item in movies:
        if not isinstance(item, dict):
            continue
        fanhao = item.get("movieFanHao")
        movie_id = item.get("movieId")
        if not isinstance(fanhao, str) or not isinstance(movie_id, str) or not movie_id:
            continue
        if fanhao.casefold() == exact:
            return movie_id
        if folded_hit is None and _fold_number(fanhao) == folded:
            folded_hit = movie_id
    return folded_hit


def _parse_movie_url(url: str) -> tuple[str | None, str | None]:
    match = _MOVIE_PATH.search(url)
    if not match:
        return None, None
    return match.group("id"), match.group("lang")
