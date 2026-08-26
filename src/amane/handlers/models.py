from __future__ import annotations

from contextlib import suppress
from enum import StrEnum
from typing import TYPE_CHECKING

from fastapi import HTTPException
from pydantic import BaseModel, Field
from starlette.status import HTTP_403_FORBIDDEN

from ..db import MediaFileStatus, Repository
from ..enums import DownloadableResource
from ..parsing import ContentType, infer_content_type
from ..utils.path import is_descendant

if TYPE_CHECKING:
    from ..db.models import Feed


class LibraryBase(BaseModel):
    """
    共享基类: 指定一个 Library 和可选覆盖参数.

    务必在 router 中调用 resolve() 方法获取最终参数值, 不要直接访问属性.
    """

    library_id: int = Field(
        description="所属 Library ID; 扫描/整理在该媒体库下进行", json_schema_extra={"x-widget": "LibraryPicker"}
    )
    recursive: bool | None = Field(default=None, description="覆盖 Library 的 recursive; None 沿用库设置")
    patterns: list[str] | None = Field(default=None, description="覆盖 Library 的 patterns; None 沿用库设置")
    path: str = Field(
        default="",
        description="要扫描的目录路径 (覆盖 Library 路径, 必须为 Library 子目录).",
        json_schema_extra={"x-widget": "PathPicker", "x-path-type": "directory"},
    )

    async def resolve(self, repo: Repository):
        """从 Library 解析覆盖参数并写回 self; path 非库子目录时抛 403."""
        lib = await repo.get_library(self.library_id)
        if lib is None:
            raise HTTPException(status_code=404, detail=f"Library {self.library_id} not found")
        self.recursive = self.recursive if self.recursive is not None else lib.recursive
        self.patterns = self.patterns or lib.patterns
        if self.path and not is_descendant(self.path, lib.path):
            raise HTTPException(
                status_code=HTTP_403_FORBIDDEN,
                detail=f"Path {self.path} is not a descendant of library path {lib.path}",
            )
        self.path = self.path or lib.path


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


class RefreshPayload(LibraryBase):
    """REFRESH 任务载荷."""

    scan: set[ScanMode] = {ScanMode.add}
    """扫描模式集合: add=注册新文件, remove=清理失效记录; 空集 = 不扫描."""
    scrape: set[MediaFileStatus] = {MediaFileStatus.PENDING}
    """刮削指定状态的文件."""
    use_cache: set[CacheKind] = {CacheKind.metadata, CacheKind.trans}
    """刮削时启用的缓存种类 (元数据 / 译文). 空集 = 全部强制刷新. 转发给派生的 SCRAPE."""


class RefreshResult(BaseModel):
    """REFRESH 任务结果."""

    added: int
    """新发现并注册的文件数"""
    removed: int
    """删除的失效记录数"""
    scrape: int
    """提交的 SCRAPE 任务数"""


# --- SCRAPE ---


class ScrapePayload(BaseModel):
    """SCRAPE 任务载荷 -- 多源刮削并存储元数据."""

    number: str
    content_type: ContentType = ContentType.CENSORED
    media_file_id: int | None = None
    use_cache: set[CacheKind] = {CacheKind.metadata, CacheKind.trans}
    """启用的缓存种类 (元数据 / 译文). 空集 = 全部强制刷新 (忽略 DB 快照并强制重译)."""


def build_feed_scrape_payload(feed: Feed, number: str) -> ScrapePayload:
    """按 Feed 配置构造番号级 SCRAPE payload."""
    content_type = infer_content_type(number)
    if feed.content_type:
        with suppress(ValueError):
            content_type = ContentType(feed.content_type)

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
    """SCRAPE 任务结果."""

    metadata_id: int
    field_sources: dict[str, str]
    failed_sites: list[str]


# --- ORGANIZE ---


class OrganizePayload(LibraryBase):
    """ORGANIZE 任务载荷. write_nfo / copy_resources 为 None 时沿用 Library 设置."""

    write_nfo: bool | None = Field(default=None, description="覆盖 Library.write_nfo; None 沿用库设置")
    copy_resources: list[DownloadableResource] | None = Field(
        default=None, description="覆盖 Library.copy_resources; None 沿用库设置"
    )

    async def resolve(self, repo: Repository) -> None:
        await super().resolve(repo)
        lib = await repo.get_library(self.library_id)
        if lib is None:
            return
        if self.write_nfo is None:
            self.write_nfo = lib.write_nfo
        if self.copy_resources is None:
            self.copy_resources = list(lib.copy_resources)


class OrganizeResult(BaseModel):
    """ORGANIZE 任务结果."""

    organized: int
    """成功整理的文件数"""
    skipped: int
    """无元数据跳过的文件数"""
    failed: int
    """整理失败的文件数"""
    trashed: int = 0
    """命中库黑名单并移入 `.amane_trash` 的文件数"""


# --- CLEANUP ---


class CleanupPayload(BaseModel):
    """CLEANUP 任务载荷 -- 清理悬空引用 (失效 MediaFile 索引 / 未引用 Resource)."""

    remove_missing_files: bool = Field(default=True, description="删除磁盘上不存在的 MediaFile 记录 (不触碰 Metadata)")
    remove_unreferenced_resources: bool = Field(
        default=True, description="删除不被任何 Metadata URL 字段引用的 Resource (含派生裁剪)"
    )


class CleanupResult(BaseModel):
    """CLEANUP 任务结果."""

    files_removed: int
    """清理的 MediaFile 记录数"""
    resources_removed: int
    """清理的未引用 Resource 数"""


# --- UPSCALE ---


class UpscalePayload(BaseModel):
    """UPSCALE 任务载荷 -- 扫描全部资源, 对低质图就地超分.

    阈值默认沿用 sr 配置; 显式提供则覆盖 (供定时任务调参). 单次批量上限避免长占 worker.
    """

    max_dim_threshold: int | None = None
    """覆盖 sr.max_dim_threshold; None 沿用配置."""
    max_bytes_threshold: int | None = None
    """覆盖 sr.max_bytes_threshold; None 沿用配置."""
    limit: int = 200
    """单次最多处理的资源数 (避免长时间占用 worker)."""


class UpscaleResult(BaseModel):
    """UPSCALE 任务结果."""

    scanned: int
    """扫描的资源数"""
    upscaled: int
    """成功超分的资源数"""
    skipped: int
    """跳过的资源数 (已超分/不达阈值/非图)"""
    failed: int
    """超分失败的资源数"""


# --- R18 IMPORT ---


class R18ImportPayload(BaseModel):
    """R18_IMPORT 任务载荷 -- 下载并导入 r18.dev dump 到用户的 PG 实例.

    force=True 时忽略 ETag 比对, 强制重新下载导入 (用于排障 / 校验逻辑变更后重灌).
    """

    force: bool = False
    """忽略已导入版本的元数据比对, 强制重新导入."""


class R18ImportResult(BaseModel):
    """R18_IMPORT 任务结果."""

    imported: bool
    """是否实际执行了导入 (False = 远程未变化, 跳过)."""
    etag: str | None = None
    """导入后记录的 dump ETag (用于下次比对)."""


# --- ACTOR SCRAPE ---


class ActorScrapePayload(BaseModel):
    """ACTOR_SCRAPE 任务载荷 -- 按 Actor.id 多源刮削人物元数据."""

    actor_id: int = Field(description="Actor 实体 ID")
    use_cache: set[CacheKind] = Field(
        default_factory=lambda: {CacheKind.metadata, CacheKind.trans},
        description="启用的缓存种类 (metadata: 复用 Actor.raw per-site 快照; trans: 预留演员译文). 空集 = 全部强制刷新",
    )


class ActorScrapeResult(BaseModel):
    """ACTOR_SCRAPE 任务结果."""

    actor_id: int
    field_sources: dict[str, str]
    failed_sites: list[str]
    image_count: int


# --- RESCRAPE ---


class RescrapePayload(BaseModel):
    """RESCRAPE 任务载荷 -- 元数据级滚动补刮.

    取最久未更新的 limit 条 Metadata, 逐条提交非 force SCRAPE (复用 raw 快照, 仅补缺失站点,
    聚合阶段重放当前配置), 低优先 (priority=-1) 不抢用户任务.
    content_type 不存表, 运行时推断: 挂载文件路径 → 番号模式 (见 handlers/rescrape.py).
    """

    limit: int = Field(default=100, ge=1, le=1000, description="单次最多补刮的元数据数 (避免长占 worker 队列)")
    min_age_days: int | None = Field(
        default=None, ge=1, description="仅补刮 updated_at 距今超过该天数的条目; None 不设门槛"
    )


class RescrapeResult(BaseModel):
    """RESCRAPE 任务结果."""

    submitted: int
    """提交的 SCRAPE 任务数"""
