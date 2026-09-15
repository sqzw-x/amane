"""同一 ``works`` 主题下的厂牌官网共用实现 (faleno.jp / dahlia-av.jp).

两站结构与字段命名一致, 差异只有站内路径前缀与厂牌命名, 由子类类属性注入.

检索与详情都读取 WP REST ``/wp-json/wp/v2/works?slug=``: slug 精确匹配, 不存在时返回空数组.
**不允许改用 HTML 详情页判定存在性**: 站点对前缀命中但未命中的 slug 会 302 到别的作品
(``/works/fns1/`` → ``/works/fns108/``), 得到的是另一部作品的页面.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from ...net.errors import FailureReason, SourceError, parse_detail
from ..base import Crawler
from ..models import FetchOptions, MediaMetadata, SearchQuery, film_actors

_ACTORS = re.compile(r"[/、\s]+")
_SLUG_CHARS = re.compile(r"[^0-9a-z]")
_SLUG_SHAPE = re.compile(r"^([a-z]+)(\d+)([a-z]*)$")


class _Rendered(BaseModel):
    rendered: str | None = None


class _WorksAcf(BaseModel):
    """作品自定义字段. 字段名即站点 ACF 键; 键改名时该项降级为空, 不影响其余字段."""

    model_config = ConfigDict(extra="ignore")

    title: str | None = Field(default=None, alias="作品名")
    actors: str | None = Field(default=None, alias="出演女優")
    plot: str | None = Field(default=None, alias="作品紹介文")
    runtime: str | None = Field(default=None, alias="収録時間")
    release: str | None = Field(default=None, alias="発売日（表示用）")


def _only_strings(value: Any) -> Any:
    # 列表里出现 null 时只丢弃该项, 不让整条响应解析失败.
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str) and item]
    return value


class _WorksImages(BaseModel):
    """主题按用途预生成的图片 URL: 横版主图 / 竖版海报 / 剧照全尺寸."""

    model_config = ConfigDict(extra="ignore")

    main: str | None = Field(default=None, alias="main_photo_url")
    sub: str | None = Field(default=None, alias="sub_photo_url")
    gallery: list[str] = Field(default_factory=list, alias="gallery_urls")

    @field_validator("gallery", mode="before")
    @classmethod
    def _drop_empty(cls, value: Any) -> Any:
        return _only_strings(value)


class _WorksPost(BaseModel):
    model_config = ConfigDict(extra="ignore")

    slug: str | None = None
    title: _Rendered | None = None
    acf: _WorksAcf = Field(default_factory=_WorksAcf)
    images: _WorksImages = Field(default_factory=_WorksImages, alias="acf_custom_images")


def slugify(number: str) -> str:
    """番号 → 站点 slug: 只保留字母数字并转为小写 (``FNS-257`` → ``fns257``)."""
    return _SLUG_CHARS.sub("", number.lower())


def number_from_slug(slug: str) -> str:
    """slug → 展示用番号: 前导字母段后补横线 (``fns241sp`` → ``FNS-241SP``)."""
    match = _SLUG_SHAPE.match(slug.lower())
    if match is None:
        return slug.upper()
    return f"{match.group(1).upper()}-{match.group(2)}{match.group(3).upper()}"


def split_actors(value: str | None) -> list[str]:
    """出演女優字段由 ``/``、 ``、`` 或空白分隔, 允许整段为空."""
    if not value:
        return []
    return [name for name in (part.strip() for part in _ACTORS.split(value)) if name]


def strip_actor_suffix(title: str, actors: list[str]) -> str:
    """站点把出演女優拼在作品名末尾, 与实际出演者一致时剥掉.

    多出演者作品的拼接形式不固定, 因此只在作品名确实以某个出演者名结尾时处理.
    """
    text = title.strip()
    for name in actors:
        if text.endswith(name):
            return text[: -len(name)].strip()
    return text


def parse_minutes(value: str | None) -> int | None:
    match = re.search(r"\d+", value or "")
    return int(match.group(0)) if match else None


class WordPressWorksCrawler(Crawler):
    """``works`` 主题站点的检索与解析; 子类只需给出 profile 与厂牌命名."""

    rest_prefix: ClassVar[str] = ""
    """站点安装在子目录时非空 (faleno.jp 为 ``/top``)."""

    studio: ClassVar[str] = ""
    """片商 (メーカー), 同站所有厂牌共用."""

    default_publisher: ClassVar[str] = ""
    """发行商 (レーベル) 缺省值, 番号前缀未命中时使用."""

    publisher_prefixes: ClassVar[Mapping[str, str]] = {}
    """番号前缀 → 发行商. 站点只在厂牌列表页公布该分组, 因此在此固定."""

    def _api_url(self, slug: str) -> str:
        return f"{self.base_url}{self.rest_prefix}/wp-json/wp/v2/works?slug={slug}"

    def _page_url(self, slug: str) -> str:
        return f"{self.base_url}{self.rest_prefix}/works/{slug}/"

    def _publisher(self, slug: str) -> str | None:
        match = re.match(r"^[a-z]+", slug.lower())
        prefix = match.group(0) if match else ""
        return self.publisher_prefixes.get(prefix, self.default_publisher) or None

    @staticmethod
    def _posts(data: Any) -> list[_WorksPost]:
        if not isinstance(data, list):
            raise SourceError(FailureReason.PARSE_ERROR, detail=f"expected list, got {type(data).__name__}")
        try:
            return [_WorksPost.model_validate(item) for item in data]
        except ValidationError as exc:
            raise SourceError(FailureReason.PARSE_ERROR, detail=parse_detail(exc)) from exc

    async def _search(self, query: SearchQuery, options: FetchOptions | None = None) -> str | None:
        slug = slugify(query.number)
        if not slug:
            return None

        url = self._api_url(slug)
        posts = self._posts(await self.client.get_json(url))
        if not any(post.slug and post.slug.lower() == slug for post in posts):
            self.logger.debug("search miss: slug not published", number=query.number, slug=slug)
            return None
        return url

    async def _scrape(self, url: str, options: FetchOptions | None = None) -> MediaMetadata | None:
        posts = self._posts(await self.client.get_json(url))
        post = posts[0] if posts else None
        if post is None or not post.slug:
            return None

        acf = post.acf
        actors = split_actors(acf.actors)
        raw_title = acf.title or (post.title.rendered if post.title else None) or ""

        return MediaMetadata(
            number=number_from_slug(post.slug),
            title=strip_actor_suffix(raw_title, actors) or None,
            actors=film_actors(actors),
            studio=self.studio or None,
            publisher=self._publisher(post.slug),
            release=acf.release,
            runtime=parse_minutes(acf.runtime),
            plot=(acf.plot or "").strip() or None,
            poster_urls=[post.images.sub] if post.images.sub else [],
            thumb_urls=[post.images.main] if post.images.main else [],
            extrafanart=post.images.gallery,
            source_url=self._page_url(post.slug),
        )
