from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field, field_validator

from amane.utils.dates import normalize_calendar_date

if TYPE_CHECKING:
    from ..aggregate import AggregatedMetadata
    from ..enums import Language
    from ..parsing.file_info import ContentType


@dataclass
class SearchQuery:
    """
    爬虫搜索的结构化输入.

    大部分爬虫只需使用 number 字段.
    高级爬虫可利用 file_path, file_hash, partial_result 等实现更智能的匹配.
    """

    number: str
    """标准化番号 (必填)."""

    file_path: str | None = None
    """原始文件路径 (可选, 用于文件名匹配)."""

    file_hash: str | None = None
    """文件 oshash (可选, 用于 ThePornDB 等支持 hash 匹配的站点)."""

    content_type: ContentType | None = None
    """内容类型: censored/uncensored (可选)."""

    partial_result: AggregatedMetadata | None = None
    """前序爬虫聚合的中间结果 (由 Aggregator 注入, 测试时可不传)."""

    raw_results: dict[str, MediaMetadata | None] | None = None
    """各站原始数据 (由 Aggregator 注入)."""


@dataclass
class FetchOptions:
    """爬虫控制项"""

    language: Language | None = None
    """语言偏好, 如 Language.ZH_CN, Language.JP."""


class MediaMetadata(BaseModel):
    """
    爬虫返回的结构化元数据结果. Pydantic 模型 - 构造时强校验类型.

    除 `number` 外所有字段均为可选. 列表默认为空.
    """

    number: str
    title: str | None = None
    actors: list[str] = Field(default_factory=list)
    studio: str | None = None
    publisher: str | None = None
    release: str | None = None
    runtime: int | None = None
    tags: list[str] = Field(default_factory=list)
    series: str | None = None
    plot: str | None = None
    poster_urls: list[str] = Field(default_factory=list)
    thumb_urls: list[str] = Field(default_factory=list)
    trailer_urls: list[str] = Field(default_factory=list)
    score: float | None = None
    external_id: str | None = None
    source_url: str | None = None
    directors: list[str] = Field(default_factory=list)
    extrafanart: list[str] = Field(default_factory=list)

    @field_validator("release", mode="before")
    @classmethod
    def _normalize_release(cls, value: object) -> str | None:
        """发行日存库为 YYYY-MM-DD; ISO 日期时间只取日; 无法解析视为缺省."""
        if value is None or value == "":
            return None
        if not isinstance(value, str):
            msg = "release must be a string"
            raise TypeError(msg)
        return normalize_calendar_date(value)
