from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import Column, Index, String, Text, UniqueConstraint, text
from sqlmodel import JSON, Field, SQLModel

from amane.enums import ActorGender, DownloadableResource, LibraryAutomation, LinkMode, MoveMode
from amane.organize.path_templates import (
    VIDEO_TEMPLATE_DEFAULT,
    PathTemplate,
)
from amane.utils.extensions import (
    DEFAULT_SUBTITLE_EXTENSIONS,
    DEFAULT_TRAILER_PATTERN,
    BlacklistPattern,
    MinFileSize,
    SubtitleExtensions,
    TrailerPattern,
)


def _utcnow() -> datetime:
    return datetime.now(UTC)


# --- 枚举 ---


class MediaFileStatus(StrEnum):
    PENDING = "pending"
    SCRAPED = "scraped"
    FAILED = "failed"
    SKIP = "skip"


class TaskType(StrEnum):
    SCRAPE = "scrape"
    ORGANIZE = "organize"
    REFRESH = "refresh"
    CLEANUP = "cleanup"
    UPSCALE = "upscale"
    R18_IMPORT = "r18_import"
    ACTOR_SCRAPE = "actor_scrape"
    RESCRAPE = "rescrape"


class RoutineType(StrEnum):
    CLEANUP = "cleanup"
    UPSCALE = "upscale"
    R18_IMPORT = "r18_import"
    RESCRAPE = "rescrape"


class TaskStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class FeedItemState(StrEnum):
    """Feed 历史列表状态过滤."""

    ACTIVE = "active"
    IGNORED = "ignored"
    ALL = "all"


# --- 排序 ---
#
# 列表端点的服务端排序: 字段集合显式枚举 (而非反射任意列名), 既约束 API 输入面,
# 又让 repository 层的 enum->Column 映射保持类型安全, 无需 getattr.


class SortOrder(StrEnum):
    ASC = "asc"
    DESC = "desc"


class MetadataSortField(StrEnum):
    NUMBER = "number"
    TITLE = "title"
    STUDIO = "studio"
    RELEASE = "release"
    CREATED_AT = "created_at"
    UPDATED_AT = "updated_at"
    FILE_COUNT = "file_count"


class MediaSortField(StrEnum):
    NUMBER = "number"
    PATH = "path"
    STATUS = "status"
    SIZE = "size"
    CREATED_AT = "created_at"
    UPDATED_AT = "updated_at"


class TaskSortField(StrEnum):
    TYPE = "type"
    STATUS = "status"
    PRIORITY = "priority"
    CREATED_AT = "created_at"
    STARTED_AT = "started_at"
    FINISHED_AT = "finished_at"


class FacetSortField(StrEnum):
    NAME = "name"
    COUNT = "count"


class ActorSortField(StrEnum):
    """演员浏览列表排序字段."""

    NAME = "name"
    COUNT = "count"
    UPDATED_AT = "updated_at"
    HAS_IMAGE = "has_image"
    BIRTHDAY = "birthday"
    HEIGHT = "height"
    BUST = "bust"
    WAIST = "waist"
    HIP = "hip"
    CUP = "cup"


class FacetKind(StrEnum):
    """分类索引种类 - 爬取侧目录 + 用户 tag."""

    ACTOR = "actor"
    DIRECTOR = "director"
    TAG = "tag"
    STUDIO = "studio"
    PUBLISHER = "publisher"
    SERIES = "series"
    USER_TAG = "user_tag"


class FacetRuleAction(StrEnum):
    """爬取侧分类用户规则动作."""

    ALIAS = "alias"
    BLOCK = "block"


# 可写 FacetRule 的 kind (与刮削投影对应; user_tag 硬删, 不进规则表).
SCRAPE_FACET_KINDS: frozenset[FacetKind] = frozenset(
    {
        FacetKind.ACTOR,
        FacetKind.DIRECTOR,
        FacetKind.TAG,
        FacetKind.STUDIO,
        FacetKind.PUBLISHER,
        FacetKind.SERIES,
    }
)


# --- 表 ---


class MediaFile(SQLModel, table=True):
    """文件索引 -- 磁盘上文件的唯一数据源"""

    __tablename__ = "media_files"  # type: ignore[assignment]

    id: int | None = Field(default=None, primary_key=True)
    path: str = Field(unique=True, nullable=False, index=True)
    oshash: str | None = None
    size: int | None = None
    duration: float | None = None
    codec: str | None = None
    number: str | None = Field(default=None, index=True)
    status: MediaFileStatus = Field(default=MediaFileStatus.PENDING, index=True)
    metadata_id: int | None = Field(default=None, foreign_key="metadata.id", index=True)
    library_id: int = Field(foreign_key="libraries.id", index=True)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class Metadata(SQLModel, table=True):
    """聚合元数据 -- 来自多源爬虫的最终结果"""

    __tablename__ = "metadata"  # type: ignore[assignment]
    # number 唯一性大小写不敏感 (COLLATE NOCASE); 存库保留首次写入的原始大小写.
    __table_args__ = (Index("ix_metadata_number", text("number COLLATE NOCASE"), unique=True),)

    id: int | None = Field(default=None, primary_key=True)
    number: str = Field(nullable=False)

    # 标量字段 (优先级选取单值); studio/publisher/series 同时投影到目录表供 facet
    title: str | None = None
    actors: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    studio: str | None = Field(default=None, index=True)
    publisher: str | None = Field(default=None, index=True)
    release: str | None = None
    runtime: int | None = None
    tags: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    series: str | None = Field(default=None, index=True)
    plot: str | None = None
    directors: list[str] = Field(default_factory=list, sa_column=Column(JSON))

    # 多源 URL 列表 (按优先级排序)
    poster_urls: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    thumb_urls: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    trailer_urls: list[str] = Field(default_factory=list, sa_column=Column(JSON))

    # 剧照: 按站点分组
    extrafanart_urls: dict[str, list[str]] = Field(default_factory=dict, sa_column=Column(JSON))
    # {"javdb": [url1, url2], "dmm": [url3, url4]}

    # 评分: 每站独立
    scores: dict[str, float] = Field(default_factory=dict, sa_column=Column(JSON))
    # {"javdb": 85.0, "dmm": 91.0}

    # 来源追踪
    external_ids: dict[str, str] = Field(default_factory=dict, sa_column=Column(JSON))
    # {site: external_id}
    source_urls: dict[str, str] = Field(default_factory=dict, sa_column=Column(JSON))
    # {site: detail_page_url}
    field_sources: dict[str, str] = Field(default_factory=dict, sa_column=Column(JSON))
    # {field_name: site_name}

    # 各站原始数据快照 (用于重新聚合)
    raw: dict[str, dict[str, Any]] = Field(default_factory=dict, sa_column=Column(JSON))
    # {site_name: {field: value, ...}}

    # 时间戳
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    # --- 便捷属性 (ORM 层, 不影响 schema) ---

    @property
    def poster_url(self) -> str | None:
        """返回最高优先级的 poster URL."""
        return self.poster_urls[0] if self.poster_urls else None

    @property
    def thumb_url(self) -> str | None:
        """返回最高优先级的 thumb URL."""
        return self.thumb_urls[0] if self.thumb_urls else None

    @property
    def trailer_url(self) -> str | None:
        """返回最高优先级的 trailer URL."""
        return self.trailer_urls[0] if self.trailer_urls else None

    @property
    def extrafanart(self) -> list[str]:
        """返回最高优先级站点的剧照 URL, 与 poster_url / thumb_url 一致."""
        if not self.extrafanart_urls:
            return []
        return list(next(iter(self.extrafanart_urls.values())))

    @property
    def score(self) -> float | None:
        """返回第一个评分 (最高优先级站点)."""
        if not self.scores:
            return None
        return float(next(iter(self.scores.values())))


class Task(SQLModel, table=True):
    """持久化任务队列"""

    __tablename__ = "tasks"  # type: ignore[assignment]

    id: int | None = Field(default=None, primary_key=True)
    type: TaskType = Field(index=True)
    status: TaskStatus = Field(default=TaskStatus.QUEUED, index=True)
    payload: dict = Field(default_factory=dict, sa_column=Column(JSON))
    result: dict | None = Field(default=None, sa_column=Column(JSON))
    error: str | None = None
    log_file: str | None = None
    retries: int = Field(default=0)
    priority: int = Field(default=0)
    # 后继图聚合真值: 链根任务 id (根任务指向自己, 裸任务为 None).
    # 树查询一次取整链, 免递归; 与 TaskLink 语义对齐, 未来升级 flow_run 只改名不迁移.
    root_task_id: int | None = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=_utcnow)
    started_at: datetime | None = None
    finished_at: datetime | None = None


class TaskLink(SQLModel, table=True):
    """任务后继关系 (父 → 子).

    查询与幂等真值: 子任务的 key 语义、去重均以本表为准.
    删除父任务时清理其出边, 不删除子任务节点.
    """

    __tablename__ = "task_links"  # type: ignore[assignment]
    __table_args__ = (UniqueConstraint("parent_task_id", "key", name="uq_task_links_parent_key"),)

    id: int | None = Field(default=None, primary_key=True)
    parent_task_id: int = Field(foreign_key="tasks.id", index=True)
    child_task_id: int = Field(foreign_key="tasks.id", index=True)
    key: str = Field(nullable=False)
    """父节点内后继语义键 (如 scrape:{media_file_id}); UNIQUE(parent, key) 保证同父同名一条边."""
    created_at: datetime = Field(default_factory=_utcnow)


class Library(SQLModel, table=True):
    """媒体库 -- 一个根目录 + 路径模板 + 整理放置方式 + 自动化级别.

    与 Emby 的 Library 概念对齐. 每个 MediaFile 持久关联到唯一 Library
    (MediaFile.library_id), 一切文件操作在 library-file 联合语义下进行.
    """

    __tablename__ = "libraries"  # type: ignore[assignment]

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(nullable=False)
    path: str = Field(nullable=False)
    automation: LibraryAutomation = Field(
        default=LibraryAutomation.SCRAPE, sa_column=Column(String, nullable=False, server_default="scrape")
    )
    """自动化级别: none 不监控 / watch 仅入库 / scrape 入库并自动刮削. 库本身始终有效."""
    recursive: bool = Field(default=True)
    patterns: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    move_mode: MoveMode = Field(default=MoveMode.MOVE, sa_column=Column(String, nullable=False, server_default="move"))
    """整理时如何把源文件放到模板路径."""
    # 路径模板
    video_template: PathTemplate = Field(default=VIDEO_TEMPLATE_DEFAULT)
    link_template: PathTemplate | None = None
    """空则不创建链接. 非空时 ORGANIZE 在视频就位后按此模板写 strm 或软链接, 必须落在库根之外."""
    link_mode: LinkMode = Field(default=LinkMode.STRM, sa_column=Column(String, nullable=False, server_default="strm"))
    """link_template 非空时: strm 写 .strm 文本 (内容为视频绝对路径); symlink 做文件系统软链接."""
    thumb_template: PathTemplate | None = None
    poster_template: PathTemplate | None = None
    fanart_template: PathTemplate | None = None
    extrafanart_template: PathTemplate | None = None
    nfo_template: PathTemplate | None = None
    trailer_template: PathTemplate | None = None
    subtitle_template: PathTemplate | None = None
    subtitle_extensions: SubtitleExtensions = Field(
        default_factory=lambda: list(DEFAULT_SUBTITLE_EXTENSIONS),
        sa_column=Column(JSON, nullable=False),
    )
    """ORGANIZE 时在视频同目录发现字幕的扩展名列表; 空列表关闭."""
    write_nfo: bool = Field(default=True)
    """整理时是否写入 NFO."""
    copy_resources: list[DownloadableResource] = Field(
        default_factory=lambda: [r for r in DownloadableResource if r != DownloadableResource.trailer],
        sa_column=Column(JSON, nullable=False),
    )
    """整理时复制到库路径的附属资源类型."""
    trailer_pattern: TrailerPattern = Field(default=DEFAULT_TRAILER_PATTERN, sa_column=Column(String, nullable=False))
    """匹配文件名 (含扩展名) 的正则; 命中则扫描/监控跳过. 空串关闭."""
    blacklist_patterns: list[BlacklistPattern] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    """文件名正则列表, 命中任一则扫描/监控跳过, 且 ORGANIZE 时移入库根 `.amane_trash`. 空列表关闭."""
    min_file_size: MinFileSize = Field(default=0)
    """视频体积下限 (字节). 小于此值的扫描视频在 REFRESH/监控跳过, ORGANIZE 时进 `.amane_trash`. 0 关闭.

    只对扫描视频扩展名生效 (与 watcher.media_extensions / MEDIA_EXTENSIONS 同一套);
    图片、NFO、字幕、`.strm` 指针都不参与.
    """


class Schedule(SQLModel, table=True):
    """类 cron 的定时任务"""

    __tablename__ = "schedules"  # type: ignore[assignment]

    id: int | None = Field(default=None, primary_key=True)
    name: str | None = None
    cron: str = Field(nullable=False)
    task_type: RoutineType = Field()
    payload: dict = Field(default_factory=dict, sa_column=Column(JSON))
    enabled: bool = Field(default=True)
    last_run: datetime | None = None
    next_run: datetime | None = None


class Feed(SQLModel, table=True):
    """远程发现源: RSS/Atom 拉取 → 解析番号 → 按 auto_enqueue 入队 by-number SCRAPE.

    与 Library (本地目录发现) 并列; 间隔与刮削属性按源绑定, 不进 HotSettings.
    """

    __tablename__ = "feeds"  # type: ignore[assignment]

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(nullable=False)
    url: str = Field(unique=True, nullable=False, index=True)
    group: str = Field(default="", nullable=False)
    """斜杠伪路径 (如 jav/rsshub). 空串为未分组; 前端据此建树, 库内不存目录实体."""
    enabled: bool = Field(default=True)
    """是否纳入定期拉取. 关闭后仍可立即拉取."""
    auto_enqueue: bool = Field(default=True)
    """发现新番号时是否入队 SCRAPE. 关闭后仍写 FeedItem, 刮削改走历史表手动选."""
    interval_seconds: int = Field(default=3600)
    """拉取间隔 (秒). 范围由 API 校验 60–86400; 表单默认按小时."""
    number_pattern: str | None = None
    """可选正则; 设置后只走该正则, 不回退内置 extract_number."""
    content_type: str | None = None
    """显式 ContentType; None 则 infer_content_type(number)."""
    use_cache: list[str] = Field(default_factory=lambda: ["metadata", "trans"], sa_column=Column(JSON, nullable=False))
    etag: str | None = None
    last_modified: str | None = None
    next_fetch_at: datetime | None = None
    last_fetched_at: datetime | None = None
    last_error: str | None = None
    last_enqueued: int = Field(default=0)


class FeedItem(SQLModel, table=True):
    """某源曾见过的 RSS/Atom 条目. (feed_id, item_key) 是去重真值."""

    __tablename__ = "feed_items"  # type: ignore[assignment]
    # 列表 ORDER BY coalesce(published_at, created_at), id; 表达式必须与查询一致才能走索引.
    __table_args__ = (
        UniqueConstraint("feed_id", "item_key", name="uq_feed_items_feed_id_item_key"),
        Index("ix_feed_items_list_order", text("coalesce(published_at, created_at)"), "id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    feed_id: int = Field(foreign_key="feeds.id", index=True)
    item_key: str = Field(nullable=False)
    title: str | None = None
    link: str | None = None
    description: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    """条目正文 (RSS description / Atom content), 供阅读器渲染. 首次写入后不随源更新."""
    number: str | None = None
    ignored_at: datetime | None = Field(default=None, index=True)
    published_at: datetime | None = None
    """源给出的发布时间 (RSS pubDate / Atom published, 否则 updated). 列表按此新→旧; 空则回退 created_at."""
    created_at: datetime = Field(default_factory=_utcnow)


class Resource(SQLModel, table=True):
    """下载资源缓存 - URL 到本地文件的映射.

    一等存储 (非临时缓存): 派生产物 (裁剪) 与被超分的资源持久保留.
    `url` 作通用 locator key:
     - 原始外部图: url = 真实外部 URL (dedup 天然).
     - 裁剪派生: url = 合成串 `derived:{sha256(src_url)}:crop:{args}`
        (args 为自动右侧比如 `0.7000`, 或手动像素框 `box:L,T,R,B`; 外部不存在, 经后端 serve).
    `meta` 仅派生/被处理资源写入 (原始未处理图为 None).
    """

    __tablename__ = "resources"  # type: ignore[assignment]

    id: int | None = Field(default=None, primary_key=True)
    url: str = Field(unique=True, nullable=False, index=True)
    file_path: str = Field(nullable=False)
    """相对于 resources 目录的路径 (如 'a3/a3f1c2d4e5b6.jpg')"""
    content_hash: str | None = None
    """SHA-256 of file content, 用于完整性校验; 就地超分后更新"""
    size: int | None = None
    mime_type: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    """派生/处理追溯. 裁剪: {'op':'crop','src':源url,'args':str};
    任意资源被超分后追加 {'sr': {tool,model,scale}}. 原始未处理图为 {}."""
    downloaded_at: datetime = Field(default_factory=_utcnow)
    last_accessed_at: datetime | None = None


# --- 分类索引 (爬取侧投影) ---
#
# Metadata 上 JSON/标量列仍是刮削与 NFO 真值; 下列实体 + 关联表是查询投影.
# Actor 为一等人物实体 (孤儿保留): name 为展示名, 别名行在 ActorAlias; 跨名屏蔽见 FacetRule(block).


class Actor(SQLModel, table=True):
    """演员实体 - 人物元数据宿主; 无影片关联时保留.

    name 为展示名 (全局唯一); 别名见 :class:`ActorAlias` (ID→名称一对多, 跨演员可共享).
    """

    __tablename__ = "actors"  # type: ignore[assignment]

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(unique=True, nullable=False, index=True)

    gender: ActorGender = Field(
        default=ActorGender.UNKNOWN, sa_column=Column(String, nullable=False, server_default="unknown", index=True)
    )
    birthday: str | None = None
    birthplace: str | None = None
    height: int | None = None
    bust: int | None = None
    waist: int | None = None
    hip: int | None = None
    cup: str | None = None
    overview: str | None = None
    tagline: str | None = None
    image_urls: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    provider_ids: dict[str, str] = Field(default_factory=dict, sa_column=Column(JSON))
    source_urls: dict[str, str] = Field(default_factory=dict, sa_column=Column(JSON))
    field_sources: dict[str, str] = Field(default_factory=dict, sa_column=Column(JSON))
    raw: dict[str, dict[str, Any]] = Field(default_factory=dict, sa_column=Column(JSON))

    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class ActorAlias(SQLModel, table=True):
    """演员 ID→名称映射 (一对多): 别名行, 供查找/搜索/展示.

    ``(actor_id, name)`` 唯一 (同演员不重复); ``name`` 列**不设全局唯一** — 两个演员
    可共享同一别名. 不存展示名 (``Actor.name`` 不入表); 展示名切换 = 改 ``Actor.name``
    并交换行 (旧展示名入表, 新展示名出表).
    """

    __tablename__ = "actor_aliases"  # type: ignore[assignment]
    __table_args__ = (UniqueConstraint("actor_id", "name", name="uq_actor_aliases_actor_name"),)

    id: int | None = Field(default=None, primary_key=True)
    actor_id: int = Field(foreign_key="actors.id", ondelete="CASCADE", nullable=False, index=True)
    name: str = Field(nullable=False, index=True)
    position: int = Field(default=0)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class Director(SQLModel, table=True):
    """导演实体 - 预留人物元数据扩展空间; 无影片关联时不自动删除."""

    __tablename__ = "directors"  # type: ignore[assignment]

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(unique=True, nullable=False, index=True)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class Tag(SQLModel, table=True):
    """爬取侧标签目录 (与 UserTag 隔离)."""

    __tablename__ = "tags"  # type: ignore[assignment]

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(unique=True, nullable=False, index=True)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class Studio(SQLModel, table=True):
    """厂商目录 - 与 Metadata.studio 字符串同步."""

    __tablename__ = "studios"  # type: ignore[assignment]

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(unique=True, nullable=False, index=True)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class Publisher(SQLModel, table=True):
    """发行商目录 - 与 Metadata.publisher 字符串同步."""

    __tablename__ = "publishers"  # type: ignore[assignment]

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(unique=True, nullable=False, index=True)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class Series(SQLModel, table=True):
    """系列目录 - 与 Metadata.series 字符串同步."""

    __tablename__ = "series"  # type: ignore[assignment]

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(unique=True, nullable=False, index=True)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class FacetRule(SQLModel, table=True):
    """爬取侧分类用户规则 - 别名 (单跳) / 黑名单; 按 (kind, source_name) 唯一."""

    __tablename__ = "facet_rules"  # type: ignore[assignment]
    __table_args__ = (UniqueConstraint("kind", "source_name", name="uq_facet_rules_kind_source"),)

    id: int | None = Field(default=None, primary_key=True)
    # 存 FacetKind / FacetRuleAction 的 value 字符串, 避免 SQLite Enum 名值漂移.
    kind: FacetKind = Field(sa_column=Column(String(), nullable=False, index=True))
    source_name: str = Field(nullable=False, index=True)
    action: FacetRuleAction = Field(sa_column=Column(String(), nullable=False))
    target_name: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class MetadataActor(SQLModel, table=True):
    __tablename__ = "metadata_actors"  # type: ignore[assignment]

    metadata_id: int = Field(foreign_key="metadata.id", primary_key=True, ondelete="CASCADE")
    actor_id: int = Field(foreign_key="actors.id", primary_key=True)
    position: int = Field(default=0)


class MetadataDirector(SQLModel, table=True):
    __tablename__ = "metadata_directors"  # type: ignore[assignment]

    metadata_id: int = Field(foreign_key="metadata.id", primary_key=True, ondelete="CASCADE")
    director_id: int = Field(foreign_key="directors.id", primary_key=True)
    position: int = Field(default=0)


class MetadataTag(SQLModel, table=True):
    __tablename__ = "metadata_tags"  # type: ignore[assignment]

    metadata_id: int = Field(foreign_key="metadata.id", primary_key=True, ondelete="CASCADE")
    tag_id: int = Field(foreign_key="tags.id", primary_key=True)
    position: int = Field(default=0)


# --- 用户注解 (与爬取数据隔离) ---


class UserTag(SQLModel, table=True):
    """用户自定义标签 - 可扩展实体; 刮削路径永不触碰."""

    __tablename__ = "user_tags"  # type: ignore[assignment]

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(unique=True, nullable=False, index=True)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class MetadataUserTag(SQLModel, table=True):
    __tablename__ = "metadata_user_tags"  # type: ignore[assignment]

    metadata_id: int = Field(foreign_key="metadata.id", primary_key=True, ondelete="CASCADE")
    user_tag_id: int = Field(foreign_key="user_tags.id", primary_key=True, ondelete="CASCADE")


class Comment(SQLModel, table=True):
    """挂在 Metadata 上的用户评论."""

    __tablename__ = "comments"  # type: ignore[assignment]

    id: int | None = Field(default=None, primary_key=True)
    metadata_id: int = Field(foreign_key="metadata.id", index=True, ondelete="CASCADE")
    body: str = Field(nullable=False)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


# --- 助理 Agent ---


class AgentSessionStatus(StrEnum):
    ACTIVE = "active"
    AWAITING_APPROVAL = "awaiting_approval"
    CLOSED = "closed"


class SavedQueryEntity(StrEnum):
    """Saved Query 交付目标 - 决定 Browse 深链与主键语义."""

    METADATA = "metadata"
    ACTOR = "actor"
    DATA = "data"


class AgentSession(SQLModel, table=True):
    """助理 Agent 会话索引; 完整 trace 落盘."""

    __tablename__ = "agent_sessions"  # type: ignore[assignment]

    id: int | None = Field(default=None, primary_key=True)
    title: str = Field(default="新会话", nullable=False)
    status: AgentSessionStatus = Field(
        default=AgentSessionStatus.ACTIVE, sa_column=Column(String(), nullable=False, index=True)
    )
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class SavedQuery(SQLModel, table=True):
    """可引用的查询预设 - 权威为 SQL; 结果仅内存缓存."""

    __tablename__ = "saved_queries"  # type: ignore[assignment]

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(nullable=False)
    sql: str = Field(nullable=False)
    entity: SavedQueryEntity = Field(sa_column=Column(String(), nullable=False, index=True))
    session_id: int | None = Field(default=None, foreign_key="agent_sessions.id", index=True)
    persisted: bool = Field(default=False, index=True)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
