"""d2pass 运营方各站共用的影片详情 JSON.

详情都在 ``/dyn/phpauto/movie_details/movie_id/<番号>.json``, 未知番号 404, 站内检索页只是 SPA 外壳.
各站共用同一份接口, 只差 base_url、番号形态与厂牌名, 因此收在一个模块里 (同 official 处理共用 CMS 的厂牌群);
分成多个模块的只有解析方式不同的 カリビアンコム.

番号形态不符时不发请求: 同集团的日期号会跨厂牌撞号, 抽日期段去拼别站的地址会取到无关作品.
站点自报的 ``MovieID`` 与入参不一致时同样判未命中, 不写入无关作品.

``MovieThumb`` 只是列表缩略 (一本道 180×240、天然むすめ 225×300、パコパコママ / ムラムラ 正方形),
一本道与天然むすめ 的原生竖版海报页面不引用, 按命名推导, 取不到时由抓取层从 ``ThumbHigh`` 裁切;
``ThumbHigh`` 是 960×540 横版封面. 剧照在 ``/dyn/dla/json/movie_gallery/<番号>.json``,
行上的 ``Protected`` 区分公开与会员, 只取公开; 详情声明 ``Gallery: false`` 时没有该接口, 不发请求.
预告片按 ``SampleFiles`` 的档位名选, 不按 URL 猜.
"""

from __future__ import annotations

import re
from typing import ClassVar, override

from pydantic import Field, ValidationError

from ...enums import SiteName
from ...net.connectivity import ConnectivityOutcome
from ...net.errors import FailureReason, RequestError, SourceError, parse_detail
from ..base import Crawler, CrawlerProfile
from ..models import FetchOptions, MediaMetadata, ResponseModel, SearchQuery, film_actors

_DETAIL_PATH = "/dyn/phpauto/movie_details/movie_id"
_GALLERY_PATH = "/dyn/dla/json/movie_gallery"
_GALLERY_IMAGE_PATH = "/dyn/dla/images"
# 预告片优先级: 720p 体积与画质平衡; 站点缺档时按序降级.
_SAMPLE_ORDER = ("720p.mp4", "1080p.mp4", "480p.mp4", "360p.mp4", "240p.mp4")


class D2PassSample(ResponseModel):
    FileName: str = ""
    URL: str = ""


class D2PassMovie(ResponseModel):
    MovieID: str = ""
    Title: str = ""
    TitleEn: str = ""
    Desc: str | None = None
    DescEn: str | None = None
    Release: str | None = None
    Duration: int | None = None
    Series: str | None = None
    ActressesJa: list[str] = Field(default_factory=list)
    ActressesEn: list[str] = Field(default_factory=list)
    UCNAME: list[str] = Field(default_factory=list)
    Gallery: bool = False
    MovieThumb: str | None = None
    ThumbHigh: str | None = None
    SampleFiles: list[D2PassSample] = Field(default_factory=list)


class D2PassGalleryRow(ResponseModel):
    Img: str = ""
    Protected: bool = False


class D2PassGallery(ResponseModel):
    MovieID: str = ""
    Rows: list[D2PassGalleryRow] = Field(default_factory=list)


class D2PassCrawler(Crawler):
    """番号形态与厂牌名由子类给出."""

    number_pattern: ClassVar[re.Pattern[str]]
    studio: ClassVar[str]
    #: 竖版海报不在详情 JSON 里, 按站点命名推导; 取不到时回退 ``MovieThumb``, 之后由抓取层裁切.
    jacket_path: ClassVar[str | None] = None

    @override
    async def fetch(self, query: SearchQuery, options: FetchOptions | None = None) -> MediaMetadata | None:
        # 只有一次请求: 基类的 _search → _scrape 会对同一份 JSON 取两次.
        url = self._detail_url(query.number)
        if url is None:
            return None
        return await self._load(url)

    @override
    async def check_connectivity(self) -> ConnectivityOutcome:
        """站首页只是 SPA 外壳; 元数据全在同源的 `/dyn/phpauto/` 下, 探测该目录的主数据接口."""
        return await self.client.check(
            f"{self.base_url}/dyn/phpauto/category.json", cookies=self.cookies, headers=self.headers
        )

    async def _search(self, query: SearchQuery, options: FetchOptions | None = None) -> str | None:
        return self._detail_url(query.number)

    async def _scrape(self, url: str, options: FetchOptions | None = None) -> MediaMetadata | None:
        return await self._load(url)

    def _detail_url(self, number: str) -> str | None:
        value = number.strip()
        if self.number_pattern.fullmatch(value) is None:
            return None
        return f"{self.base_url}{_DETAIL_PATH}/{value}.json"

    async def _load(self, url: str) -> MediaMetadata | None:
        number = url.rsplit("/", 1)[-1].removesuffix(".json")
        try:
            payload = await self.client.get_json(url, cookies=self.cookies, headers=self.headers)
        except RequestError as exc:
            # 未知番号是未命中, 其余失败照旧上报.
            if exc.reason is FailureReason.NOT_FOUND:
                return None
            raise
        try:
            movie = D2PassMovie.model_validate(payload)
        except ValidationError as exc:
            raise SourceError(FailureReason.PARSE_ERROR, detail=parse_detail(exc)) from exc
        if not movie.MovieID or movie.MovieID.casefold() != number.casefold():
            return None
        extrafanart = await self._gallery(movie.MovieID) if movie.Gallery else []
        return self._to_metadata(movie, url, extrafanart)

    async def _gallery(self, number: str) -> list[str]:
        """公开剧照. 剧照是附加资源: 取不到就按没有处理, 不让整条刮削失败.

        ``Gallery: false`` 的条目没有该接口, 调用方已跳过; 其余失败同一口径.
        """
        url = f"{self.base_url}{_GALLERY_PATH}/{number}.json"
        try:
            payload = await self.client.get_json(url, cookies=self.cookies, headers=self.headers)
        except RequestError as exc:
            self.logger.debug("gallery unavailable", number=number, reason=exc.reason)
            return []
        try:
            gallery = D2PassGallery.model_validate(payload)
        except ValidationError as exc:
            self.logger.warning("gallery parse failed", number=number, detail=parse_detail(exc))
            return []
        if gallery.MovieID and gallery.MovieID.casefold() != number.casefold():
            return []
        return [
            f"{self.base_url}{_GALLERY_IMAGE_PATH}/{row.Img}" for row in gallery.Rows if row.Img and not row.Protected
        ]

    def _poster_urls(self, movie: D2PassMovie) -> list[str]:
        """原生竖版海报在前, 详情给的缩略在后: 候选是同一张图的回退链, 命中即停."""
        derived = [f"{self.base_url}{self.jacket_path.format(number=movie.MovieID)}"] if self.jacket_path else []
        if not movie.MovieThumb:
            return derived
        return [*derived, movie.MovieThumb]

    def _to_metadata(self, movie: D2PassMovie, url: str, extrafanart: list[str]) -> MediaMetadata:
        actors = movie.ActressesJa or movie.ActressesEn
        return MediaMetadata(
            number=movie.MovieID,
            title=movie.Title or movie.TitleEn or None,
            actors=film_actors(actors),
            studio=self.studio,
            release=movie.Release,
            runtime=minutes_from_seconds(movie.Duration),
            tags=list(movie.UCNAME),
            series=movie.Series or None,
            plot=movie.Desc or movie.DescEn,
            poster_urls=self._poster_urls(movie),
            thumb_urls=[movie.ThumbHigh] if movie.ThumbHigh else [],
            trailer_urls=trailer_urls(movie.SampleFiles),
            extrafanart=extrafanart,
            source_url=f"{self.base_url}/moviepages/{movie.MovieID}/index.html",
        )


class OnePondoCrawler(D2PassCrawler):
    """一本道. 下划线加三位序号, 与 パコパコママ 同形, 归属由路由顺序决定."""

    number_pattern = re.compile(r"\d{6}_\d{3}")
    studio = "一本道"
    jacket_path = "/dyn/dla/images/movies/{number}/jacket/jacket.jpg"

    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(name=SiteName.ONEPONDO, base_url="https://www.1pondo.tv")


class TenMusumeCrawler(D2PassCrawler):
    """天然むすめ. 序号恒两位, 与同集团的三位序号站编号空间基本不相交."""

    number_pattern = re.compile(r"\d{6}_\d{2}")
    studio = "天然むすめ"
    jacket_path = "/moviepages/{number}/images/jacket.jpg"

    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(name=SiteName.TENMUSUME, base_url="https://www.10musume.com")


class PacoPacoMamaCrawler(D2PassCrawler):
    """パコパコママ. 下划线加三位序号, 与 一本道 同形, 归属由路由顺序决定."""

    number_pattern = re.compile(r"\d{6}_\d{3}")
    studio = "パコパコママ"

    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(name=SiteName.PACOPACOMAMA, base_url="https://www.pacopacomama.com")


class MuramuraCrawler(D2PassCrawler):
    """ムラムラ. 序号三位与四位并存, 四位是本厂牌的编号空间."""

    number_pattern = re.compile(r"\d{6}_\d{3,4}")
    studio = "ムラムラ"

    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(name=SiteName.MURAMURA, base_url="https://www.muramura.tv")


def minutes_from_seconds(seconds: int | None) -> int | None:
    """秒 → 分钟; 缺省与非正数视为没有时长."""
    if seconds is None or seconds <= 0:
        return None
    return seconds // 60


def trailer_urls(samples: list[D2PassSample]) -> list[str]:
    by_name = {sample.FileName: sample.URL for sample in samples if sample.URL}
    for name in _SAMPLE_ORDER:
        if url := by_name.get(name):
            return [url]
    # 站点新增档位时按列出的第一项兜底, 不比大小.
    return [next(iter(by_name.values()))] if by_name else []


__all__ = [
    "D2PassCrawler",
    "D2PassMovie",
    "MuramuraCrawler",
    "OnePondoCrawler",
    "PacoPacoMamaCrawler",
    "TenMusumeCrawler",
    "minutes_from_seconds",
    "trailer_urls",
]
