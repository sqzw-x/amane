from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from fastapi import HTTPException
from pydantic import BaseModel, Field
from starlette.status import HTTP_403_FORBIDDEN

from ..db import Library, MediaFileStatus, Repository
from ..enums import DownloadableResource
from ..parsing import ContentType, infer_content_type
from ..utils.path import is_descendant

if TYPE_CHECKING:
    from ..db.models import Feed


class LibraryBase(BaseModel):
    """library_id + path. 覆盖项在 resolve 之前为 None, 不能当最终值读取."""

    library_id: int = Field(
        description="所属 Library ID; 扫描/整理在该媒体库下进行", json_schema_extra={"x-widget": "LibraryPicker"}
    )
    path: str = Field(
        default="",
        description="要扫描的目录路径 (覆盖 Library 路径, 必须为 Library 子目录).",
        json_schema_extra={"x-widget": "PathPicker", "x-path-type": "directory"},
    )

    async def resolve(self, repo: Repository) -> None:
        """就地写回 Library 默认值与覆盖; path 非库子目录时 403."""
        lib = await repo.get_library(self.library_id)
        if lib is None:
            raise HTTPException(status_code=404, detail=f"Library {self.library_id} not found")
        if self.path and not is_descendant(self.path, lib.path):
            raise HTTPException(
                status_code=HTTP_403_FORBIDDEN,
                detail=f"Path {self.path} is not a descendant of library path {lib.path}",
            )
        self.path = self.path or lib.path
        self._apply_library(lib)

    def _apply_library(self, lib: Library) -> None:
        return


class LibraryScanBase(LibraryBase):
    """REFRESH / TRASH 扫描范围: 可覆盖库的 recursive / patterns."""

    recursive: bool | None = Field(default=None, description="覆盖 Library 的 recursive; None 沿用库设置")
    patterns: list[str] | None = Field(default=None, description="覆盖 Library 的 patterns; None 沿用库设置")

    def _apply_library(self, lib: Library) -> None:
        self.recursive = self.recursive if self.recursive is not None else lib.recursive
        self.patterns = self.patterns or lib.patterns


# --- SCAN ---


class ScanMode(StrEnum):
    add = "add"
    """扫描并注册新文件; 已存在的文件不变."""
    remove = "remove"
    """检查并删除文件失效的 MediaFile 记录."""


class CacheKind(StrEnum):
    """刮削可复用的缓存种类. use_cache 为其集合: 含某项 = 该缓存生效, 不含 = 强制刷新该项."""

    metadata = "metadata"
    """元数据缓存: 复用 DB 中既有的 per-site raw 快照, 仅补缺失/失败站点. 不含则全站强制重爬."""
    trans = "trans"
    """译文缓存: 命中则跳过 LLM 调用. 不含则强制重译 (并刷新缓存)."""


class RefreshPayload(LibraryScanBase):
    scan: set[ScanMode] = {ScanMode.add}
    """空集 = 不扫描."""
    scrape: set[MediaFileStatus] = {MediaFileStatus.PENDING}
    use_cache: set[CacheKind] = {CacheKind.metadata, CacheKind.trans}
    """空集 = 全部强制刷新. 原样转发给派生的 SCRAPE."""


class RefreshResult(BaseModel):
    added: int
    removed: int
    scrape: int


# --- SCRAPE ---


class ScrapePayload(BaseModel):
    number: str
    content_type: ContentType = ContentType.CENSORED
    media_file_id: int | None = None
    use_cache: set[CacheKind] = {CacheKind.metadata, CacheKind.trans}


def build_feed_scrape_payload(feed: Feed, number: str) -> ScrapePayload:
    content_type = feed.content_type or infer_content_type(number)

    use_cache: set[CacheKind] = set()
    for raw_kind in feed.use_cache:
        try:
            use_cache.add(CacheKind(raw_kind))
        except TypeError, ValueError:
            continue

    return ScrapePayload(
        number=number,
        content_type=content_type,
        media_file_id=None,
        use_cache=use_cache,
    )


class ScrapeResult(BaseModel):
    metadata_id: int
    field_sources: dict[str, str]
    failed_sites: list[str]


# --- ORGANIZE ---


class OrganizePayload(LibraryBase):
    """write_nfo / copy_resources 为 None 时沿用 Library 设置.

    范围三选一: 缺省 (resolve 后 path 为库根) 处理该库全部索引; 显式 path 按前缀过滤;
    `media_file_ids` 为勾选快照 (空列表空跑). 显式 path 与 `media_file_ids` 不能同时给出.
    不含 `recursive` / `patterns`.
    """

    write_nfo: bool | None = Field(default=None, description="覆盖 Library.write_nfo; None 沿用库设置")
    copy_resources: list[DownloadableResource] | None = Field(
        default=None, description="覆盖 Library.copy_resources; None 沿用库设置"
    )
    media_file_ids: list[int] | None = Field(
        default=None,
        description="勾选快照; 与 path 不能同时指定. None 表示 path 范围内的全部索引",
    )

    async def resolve(self, repo: Repository) -> None:
        if self.media_file_ids is not None and self.path:
            raise HTTPException(status_code=422, detail="media_file_ids 与 path 不能同时指定")
        if self.media_file_ids:
            found = await repo.list_media_files(ids=self.media_file_ids, limit=None)
            if any(mf.library_id != self.library_id for mf in found):
                raise HTTPException(status_code=422, detail="media_file_ids 含其它库的文件")
        await super().resolve(repo)

    def _apply_library(self, lib: Library) -> None:
        if self.write_nfo is None:
            self.write_nfo = lib.write_nfo
        if self.copy_resources is None:
            # JSON 列读回是 str; Pydantic dump 要 enum, 否则 UnexpectedValue 警告.
            self.copy_resources = [DownloadableResource(r) for r in lib.copy_resources]


class OrganizeResult(BaseModel):
    organized: int
    skipped: int
    failed: int


# --- TRASH ---


class TrashPayload(LibraryScanBase):
    """扫描 path 范围内的黑名单与过小视频, 移入 `.amane_trash`. path 缺省为库根."""


class TrashResult(BaseModel):
    trashed: int
    failed: int = 0


# --- CLEANUP ---


class CleanupPayload(BaseModel):
    remove_missing_files: bool = Field(default=True, description="删除磁盘上不存在的 MediaFile 记录 (不触碰 Metadata)")
    remove_unreferenced_resources: bool = Field(
        default=True, description="删除不被任何 Metadata URL 字段引用的 Resource (含派生裁剪)"
    )


class CleanupResult(BaseModel):
    files_removed: int
    resources_removed: int


# --- UPSCALE ---


class UpscalePayload(BaseModel):
    max_dim_threshold: int | None = None
    """覆盖 sr.max_dim_threshold; None 沿用配置."""
    max_bytes_threshold: int | None = None
    """覆盖 sr.max_bytes_threshold; None 沿用配置."""
    limit: int = 200
    """单次最多处理的资源数; 超出则本批结束, 避免长时间占用 worker."""


class UpscaleResult(BaseModel):
    scanned: int
    upscaled: int
    skipped: int
    failed: int


# --- R18 IMPORT ---


class R18ImportPayload(BaseModel):
    force: bool = False
    """忽略已导入版本的元数据比对, 强制重新导入."""


class R18ImportResult(BaseModel):
    imported: bool
    """False 表示远程未变化而跳过, 不是导入失败."""
    etag: str | None = None
    """导入后记录的 dump ETag, 供下次比对."""


# --- ACTOR SCRAPE ---


class ActorScrapePayload(BaseModel):
    actor_id: int = Field(description="Actor 实体 ID")
    use_cache: set[CacheKind] = Field(
        default_factory=lambda: {CacheKind.metadata, CacheKind.trans},
        description="启用的缓存种类 (metadata: 复用 Actor.raw per-site 快照; trans: 预留演员译文). 空集 = 全部强制刷新",
    )


class ActorScrapeResult(BaseModel):
    actor_id: int
    field_sources: dict[str, str]
    failed_sites: list[str]
    image_count: int


# --- RESCRAPE ---


class RescrapePayload(BaseModel):
    """按 updated_at 取最久未更新的 Metadata, 派生 priority=-1 的非 force SCRAPE.
    content_type 不存表, 运行时按挂载文件路径或番号推断.
    """

    limit: int = Field(default=100, ge=1, le=1000, description="单次最多补刮的元数据数 (避免长占 worker 队列)")
    min_age_days: int | None = Field(
        default=None, ge=1, description="仅补刮 updated_at 距今超过该天数的条目; None 不设门槛"
    )


class RescrapeResult(BaseModel):
    submitted: int
