from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field, field_validator

from ..enums import ActorGender
from ..utils.dates import normalize_calendar_date

if TYPE_CHECKING:
    from ..aggregate import AggregatedMetadata
    from ..enums import Language
    from ..parsing.file_info import ContentType


@dataclass
class SearchQuery:
    number: str
    file_path: str | None = None
    file_hash: str | None = None
    content_type: ContentType | None = None
    alternate_numbers: tuple[str, ...] = ()
    """同一部片在别处的番号写法, 按顺序在 ``number`` 之后尝试; 去重与剔除同形项在构造期完成."""
    # 前序来源的单源字段聚合结果, 只读; 仅 ``SourceTrait.NEEDS_PARTIAL`` 来源非空.
    # 为 ``None`` 当且仅当本次来源不在第二段; 单源路由下是空对象而非 ``None``.
    partial_result: AggregatedMetadata | None = None

    def __post_init__(self) -> None:
        """同一番号不重复检索属于本类型的不变量, 因此在构造期收口 (含测试里手工构造的实例).

        折叠口径与跨来源比对一致 (大小写 / 短横线 / 空格), 但下划线不折叠.
        """
        seen = {self.number.casefold().replace("-", "").replace(" ", "")}
        alternates: list[str] = []
        for raw in self.alternate_numbers:
            term = raw.strip()
            folded = term.casefold().replace("-", "").replace(" ", "")
            if not term or folded in seen:
                continue
            seen.add(folded)
            alternates.append(term)
        self.alternate_numbers = tuple(alternates)


@dataclass
class FetchOptions:
    language: Language | None = None


class FilmActor(BaseModel):
    """影片出演者. ``gender`` 缺省 ``unknown``; 名单语义能判定时爬虫必须写出 ``female`` / ``male``."""

    name: str
    gender: ActorGender = ActorGender.UNKNOWN


def film_actors(names: Iterable[str], *, gender: ActorGender = ActorGender.FEMALE) -> list[FilmActor]:
    """按同一性别构造出演者列表; 空名丢弃. 缺省女优, 与多数 JAV 出演栏语义一致."""
    return [FilmActor(name=name, gender=gender) for name in names if name]


class MediaMetadata(BaseModel):
    number: str
    title: str | None = None
    actors: list[FilmActor] = Field(default_factory=list)
    studio: str | None = None
    publisher: str | None = None
    release: str | None = None
    # 分钟. 外部 API 多以秒计时, 爬虫负责换算.
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

    @field_validator("actors", mode="before")
    @classmethod
    def _coerce_actors(cls, value: object) -> object:
        # 旧 raw / 插件 / 单测的 list[str] 收成 FilmActor, 性别 unknown.
        if not isinstance(value, list):
            return value
        out: list[object] = []
        for item in value:
            if isinstance(item, str):
                out.append({"name": item})
            else:
                out.append(item)
        return out

    @field_validator("release", mode="before")
    @classmethod
    def _normalize_release(cls, value: object) -> str | None:
        # 存库为 YYYY-MM-DD; ISO 日期时间只取日; 无法解析视为缺省.
        if value is None or value == "":
            return None
        if not isinstance(value, str):
            msg = "release must be a string"
            raise TypeError(msg)
        return normalize_calendar_date(value)
