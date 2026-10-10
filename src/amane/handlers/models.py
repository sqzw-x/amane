from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field
from starlette.status import HTTP_403_FORBIDDEN

from ..db import Library, MediaFileStatus, Repository
from ..db.models import TaskType
from ..enums import DownloadableResource
from ..observability.models import OrganizeConflict, SiteOutcomeRecord
from ..parsing import ContentType, infer_content_type
from ..utils.path import is_descendant, is_resolved_path, resolved_path

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
            raise HTTPException(status_code=404, detail=f"媒体库 {self.library_id} 不存在")
        if self.path and not is_descendant(self.path, lib.path):
            raise HTTPException(
                status_code=HTTP_403_FORBIDDEN,
                detail=f"路径 {self.path} 不在媒体库路径 {lib.path} 之下",
            )
        # 范围路径与库路径必须同一形式 (文件选择器给的是真实路径): 索引与清理清单都按字面路径比较,
        # 两种写法会把同一个文件算成两条索引. 库路径尚未解析为真实路径的旧库保持原样, 否则范围会与既有索引分家.
        if self.path and is_resolved_path(lib.path):
            self.path = str(resolved_path(self.path))
        self.path = self.path or lib.path
        self._apply_library(lib)

    def _apply_library(self, lib: Library) -> None:
        return


class LibraryScanBase(LibraryBase):
    """REFRESH / SCAN_INVALID 扫描范围: 可覆盖库的 recursive / patterns."""

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
    type: Literal[TaskType.REFRESH] = TaskType.REFRESH
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
    type: Literal[TaskType.SCRAPE] = TaskType.SCRAPE
    metadata_id: int | None = None
    """刮削失败时没有元数据行, 为 None."""
    field_sources: dict[str, str]
    failed_sites: list[str]
    outcomes: list[SiteOutcomeRecord] = Field(default_factory=list)
    """各来源的结果明细; 与记录里的 summary 同源."""


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
    prune_empty_dirs: bool = Field(
        default=True,
        description="移动后删除本次腾空的目录 (库根与 .amane_trash 除外); 复制 / 硬链接 / 符号链接方式不移走源文件, 该开关无效",
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


ORGANIZE_CONFLICT_LIMIT = 200
"""conflicts 的条数上限; 超出只累加 conflicted, 不写进任务结果."""


class OrganizeResult(BaseModel):
    type: Literal[TaskType.ORGANIZE] = TaskType.ORGANIZE
    organized: int
    skipped: int
    conflicted: int
    failed: int
    pruned_dirs: int
    conflicts: list[OrganizeConflict]
    """未处理的条目, 最多 ORGANIZE_CONFLICT_LIMIT 条; 恒为 conflicted 条目的前缀, 差额只留在任务日志里."""


# --- SCAN INVALID ---


class ScanInvalidPayload(LibraryScanBase):
    """只读遍历 path 范围内的无效文件与空目录, 产出清单. path 缺省为库根."""


class ScanInvalidResult(BaseModel):
    type: Literal[TaskType.SCAN_INVALID] = TaskType.SCAN_INVALID
    inventory_id: str
    entries: int
    dirs: int
    scope_path: str | None = None
    truncated: bool = False
    skipped_dirs: int = 0
    skipped_files: int = 0
    blocked_dirs: int = 0
    """因子树里有无法识别的文件而未登记的候选目录数; 与「读不到」的 skipped 分开计."""


# --- DELETE ---


class DeletePayload(BaseModel):
    """按清单标识删除; 执行集合取自清单条目 — 命中排除项的不删除, 命中更深的纳入项的仍删除."""

    library_id: int = Field(description="清单所属 Library ID")
    inventory_id: str = Field(description="后端生成的清单标识; 不存在或已过期则失败")
    exclude: list[str] = Field(
        default_factory=list,
        description="排除项: 库内为清单库根下的相对路径, 库外为绝对路径; 按路径分量匹配",
    )
    include: list[str] = Field(
        default_factory=list,
        description=(
            "在排除项内重新纳入的路径 (路径约定同 exclude); 与排除项互为祖先时按最深的一条判定, "
            "同一路径同时命中两组时按纳入处理"
        ),
    )
    prune_empty_dirs: bool = Field(default=True, description="删除本次腾空的目录 (库根与 .amane_trash 除外)")


class DeleteResult(BaseModel):
    type: Literal[TaskType.DELETE] = TaskType.DELETE
    deleted: int
    changed: int
    failed: int
    freed_bytes: int = 0
    """按 inode 归并后的释放空间; 硬链接名字不齐时不计入."""
    hardlink_items: int = 0
    """已删除但不释放空间的硬链接项数."""
    pruned_dirs: int = 0
    excluded: int = 0
    """因排除项而不删除、且未被纳入项恢复的清单条目数."""
    indexed: int = 0
    """随之删除的 MediaFile 行数."""
    reverify_rejected: int = 0
    """执行前复验未通过的条目数; 拒绝原因见 `delete.py::_reverify` (目录里出现媒体 / 未识别的
    文件 / 下载进度, 目录不再为空或无法读取), 这些条目同时计入 `failed`."""


# --- CLEANUP ---


class CleanupPayload(BaseModel):
    remove_missing_files: bool = Field(default=True, description="删除磁盘上不存在的 MediaFile 记录 (不触碰 Metadata)")
    remove_unreferenced_resources: bool = Field(
        default=True, description="删除不被任何 Metadata URL 字段引用的 Resource (含派生裁剪)"
    )


class CleanupResult(BaseModel):
    type: Literal[TaskType.CLEANUP] = TaskType.CLEANUP
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
    type: Literal[TaskType.UPSCALE] = TaskType.UPSCALE
    scanned: int
    upscaled: int
    skipped: int
    failed: int


# --- R18 IMPORT ---


class R18ImportPayload(BaseModel):
    force: bool = False
    """忽略已导入版本的元数据比对, 强制重新导入."""


class R18ImportResult(BaseModel):
    type: Literal[TaskType.R18_IMPORT] = TaskType.R18_IMPORT
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
    type: Literal[TaskType.ACTOR_SCRAPE] = TaskType.ACTOR_SCRAPE
    actor_id: int
    field_sources: dict[str, str]
    failed_sites: list[str]
    image_count: int
    outcomes: list[SiteOutcomeRecord] = Field(default_factory=list)
    """各来源的结果明细; 与记录里的 summary 同源."""


# --- RESCRAPE ---


class RescrapeTarget(StrEnum):
    """滚动补刮选取的实体种类. 每个已选项各自选取 limit 条."""

    metadata = "metadata"
    """影片 Metadata."""
    actor = "actor"
    """演员 Actor."""


class RescrapePayload(BaseModel):
    """按 updated_at 选取最久未更新的目标, 派生 priority=-1 的非 force 刮削.
    影片 content_type 不存表, 运行时按挂载文件路径或番号推断.
    """

    limit: int = Field(default=100, ge=1, le=1000, description="每个已选目标单次最多补刮的条数 (避免长占 worker 队列)")
    min_age_days: int | None = Field(
        default=None, ge=1, description="仅补刮 updated_at 距今超过该天数的条目; None 不设门槛"
    )
    targets: set[RescrapeTarget] = Field(
        default={RescrapeTarget.metadata},
        min_length=1,
        description="补刮对象; 每个已选项各自选取 limit 条. 缺省仅影片, 兼容既有 Schedule.payload",
    )


class RescrapeResult(BaseModel):
    type: Literal[TaskType.RESCRAPE] = TaskType.RESCRAPE
    submitted: int
    metadata: int = 0
    actors: int = 0


# --- EMBY SYNC ---


class EmbySyncPayload(BaseModel):
    actor_id: int | None = Field(default=None, description="只同步该演员; 缺省同步全部演员")
    force: bool = Field(default=False, description="覆盖服务器上已有的头像与字段, 忽略 emby.overwrite")


class EmbySyncResult(BaseModel):
    type: Literal[TaskType.EMBY_SYNC] = TaskType.EMBY_SYNC
    actors: int
    """处理的演员数."""
    persons: int
    """服务器上命中的同名人物条目数; 一个演员可能命中多条 (显示名与别名各占一条)."""
    images: int
    """上传的头像数."""
    updated: int
    """回写字段的人物条目数."""
    skipped: int
    """按覆盖规则无需写入的人物条目数."""
    not_found: int
    """服务器上没有同名人物的演员数."""
    no_image: int
    """本地没有可推送头像的演员数; ``emby.sync_images=false`` 时不计数."""
    failed: int
    """失败的人物条目数 (服务器不可达的整任务失败不走这里); 明细见 ``failures``."""
    failures: list[str]
    """失败明细 (人物名: 原因), 每条一个字符串, 有上限."""
