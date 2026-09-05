from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import Column, Index, String, Text, UniqueConstraint, text
from sqlmodel import JSON, Field, SQLModel

from ..enums import ActorGender, DownloadableResource, LibraryAutomation, LinkMode, MoveMode
from ..library import (
    DEFAULT_SUBTITLE_EXTENSIONS,
    DEFAULT_TRAILER_PATTERN,
    BlacklistPattern,
    MinFileSize,
    SubtitleExtensions,
    TrailerPattern,
)
from ..organize.path_templates import VIDEO_TEMPLATE_DEFAULT, PathTemplate
from ..organize.strm_content import StrmContentTemplate
from ..parsing import ContentType, Mosaic


def _utcnow() -> datetime:
    return datetime.now(UTC)


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
    ACTIVE = "active"
    IGNORED = "ignored"
    ALL = "all"


# 列表排序字段显式枚举, 禁止 getattr 反射任意列名; 未映射的枚举值须在 repository 报错.


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
    ACTOR = "actor"
    DIRECTOR = "director"
    TAG = "tag"
    STUDIO = "studio"
    PUBLISHER = "publisher"
    SERIES = "series"
    USER_TAG = "user_tag"


class FacetRuleAction(StrEnum):
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


class MediaFile(SQLModel, table=True):
    __tablename__ = "media_files"  # type: ignore[assignment]

    id: int | None = Field(default=None, primary_key=True)
    # 库内身份为 NFC. 写入与按路径查找只经 Repository (`nfc_path`).
    path: str = Field(unique=True, nullable=False, index=True)
    oshash: str | None = None
    size: int | None = None
    duration: float | None = None
    codec: str | None = None
    number: str | None = Field(default=None, index=True)
    status: MediaFileStatus = Field(default=MediaFileStatus.PENDING, index=True)
    # 文件相位: path 的投影, 随 path 写入/更新; 不进对外 PATCH.
    content_type: ContentType = Field(default=ContentType.WESTERN, index=True)
    mosaic: Mosaic | None = Field(default=None, index=True)
    has_subtitle: bool = Field(default=False, index=True)
    definition: str | None = Field(default=None, index=True)
    metadata_id: int | None = Field(default=None, foreign_key="metadata.id", index=True)
    library_id: int = Field(foreign_key="libraries.id", index=True)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class Metadata(SQLModel, table=True):
    __tablename__ = "metadata"  # type: ignore[assignment]
    # number 唯一性大小写不敏感 (COLLATE NOCASE); 存库保留首次写入的原始大小写.
    __table_args__ = (Index("ix_metadata_number", text("number COLLATE NOCASE"), unique=True),)

    id: int | None = Field(default=None, primary_key=True)
    number: str = Field(nullable=False)

    title: str | None = None
    actors: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    # studio/publisher/series 同时投影到目录表.
    studio: str | None = Field(default=None, index=True)
    publisher: str | None = Field(default=None, index=True)
    release: str | None = None
    runtime: int | None = None
    tags: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    series: str | None = Field(default=None, index=True)
    plot: str | None = None
    directors: list[str] = Field(default_factory=list, sa_column=Column(JSON))

    poster_urls: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    thumb_urls: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    trailer_urls: list[str] = Field(default_factory=list, sa_column=Column(JSON))

    # 按站点分组, 禁止扁平合并.
    extrafanart_urls: dict[str, list[str]] = Field(default_factory=dict, sa_column=Column(JSON))
    # 每站独立, 禁止折成单值.
    scores: dict[str, float] = Field(default_factory=dict, sa_column=Column(JSON))
    external_ids: dict[str, str] = Field(default_factory=dict, sa_column=Column(JSON))
    source_urls: dict[str, str] = Field(default_factory=dict, sa_column=Column(JSON))
    field_sources: dict[str, str] = Field(default_factory=dict, sa_column=Column(JSON))
    # 各站原始快照, 供离线重新聚合.
    raw: dict[str, dict[str, Any]] = Field(default_factory=dict, sa_column=Column(JSON))

    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    @property
    def poster_url(self) -> str | None:
        return self.poster_urls[0] if self.poster_urls else None

    @property
    def thumb_url(self) -> str | None:
        return self.thumb_urls[0] if self.thumb_urls else None

    @property
    def trailer_url(self) -> str | None:
        return self.trailer_urls[0] if self.trailer_urls else None

    @property
    def extrafanart(self) -> list[str]:
        """最高优先级站点的剧照, 与 poster_url / thumb_url 取首项一致."""
        if not self.extrafanart_urls:
            return []
        return list(next(iter(self.extrafanart_urls.values())))

    @property
    def score(self) -> float | None:
        if not self.scores:
            return None
        return float(next(iter(self.scores.values())))


class Task(SQLModel, table=True):
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
    # 链根 id: 根任务指向自己, 裸任务为 None. 按此列一次取出整链.
    root_task_id: int | None = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=_utcnow)
    started_at: datetime | None = None
    finished_at: datetime | None = None


class TaskLink(SQLModel, table=True):
    """后继边的 key 与去重以本表为准. 删除父任务只清理出边, 不删除子任务节点."""

    __tablename__ = "task_links"  # type: ignore[assignment]
    __table_args__ = (UniqueConstraint("parent_task_id", "key", name="uq_task_links_parent_key"),)

    id: int | None = Field(default=None, primary_key=True)
    parent_task_id: int = Field(foreign_key="tasks.id", index=True)
    child_task_id: int = Field(foreign_key="tasks.id", index=True)
    key: str = Field(nullable=False)
    """父内后继语义键; UNIQUE(parent, key) 保证同父同名一条边."""
    created_at: datetime = Field(default_factory=_utcnow)


class Library(SQLModel, table=True):
    """每个 MediaFile 必须持久关联到唯一 Library (library_id 非空 FK)."""

    __tablename__ = "libraries"  # type: ignore[assignment]

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(nullable=False)
    path: str = Field(nullable=False)
    automation: LibraryAutomation = Field(default=LibraryAutomation.SCRAPE)
    """自动化级别: none 不监控 / watch 仅入库 / scrape 入库并自动刮削. 库本身始终有效."""
    recursive: bool = Field(default=True)
    patterns: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    move_mode: MoveMode = Field(default=MoveMode.MOVE)
    video_template: PathTemplate = Field(default=VIDEO_TEMPLATE_DEFAULT)
    link_template: PathTemplate | None = None
    """空则不创建链接. 非空时 ORGANIZE 在视频就位后按此模板写 strm 或软链接, 必须在库外."""
    link_mode: LinkMode = Field(default=LinkMode.STRM)
    """link_template 非空时: strm 写 .strm 文本; symlink 做文件系统软链接."""
    strm_content_template: StrmContentTemplate | None = None
    """仅 link_mode=strm: .strm 正文模板. 空则写视频绝对路径. 占位符与路径模板相同."""
    thumb_template: PathTemplate | None = None
    poster_template: PathTemplate | None = None
    fanart_template: PathTemplate | None = None
    extrafanart_template: PathTemplate | None = None
    nfo_template: PathTemplate | None = None
    trailer_template: PathTemplate | None = None
    subtitle_template: PathTemplate | None = None
    subtitle_extensions: SubtitleExtensions = Field(
        default_factory=lambda: list(DEFAULT_SUBTITLE_EXTENSIONS), sa_column=Column(JSON, nullable=False)
    )
    """ORGANIZE 时在视频同目录发现字幕的扩展名列表; 空列表关闭."""
    write_nfo: bool = Field(default=True)
    copy_resources: list[DownloadableResource] = Field(
        default_factory=lambda: [r for r in DownloadableResource if r != DownloadableResource.trailer],
        sa_column=Column(JSON, nullable=False),
    )
    trailer_pattern: TrailerPattern = Field(default=DEFAULT_TRAILER_PATTERN, sa_column=Column(String, nullable=False))
    """匹配文件名 (含扩展名) 的正则; 命中则扫描/监控跳过. 空串关闭."""
    blacklist_patterns: list[BlacklistPattern] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    """文件名正则列表, 命中任一则扫描/监控跳过, 且 ORGANIZE 时移入本库 `.amane_trash`. 空列表关闭."""
    min_file_size: MinFileSize = Field(default=0)
    """视频体积下限 (字节). 小于此值的扫描视频在 REFRESH/监控跳过, ORGANIZE 时进 `.amane_trash`. 0 关闭.

    只对扫描视频扩展名生效 (与 watcher.media_extensions / MEDIA_EXTENSIONS 同一套);
    图片、NFO、字幕、`.strm` 指针都不参与.
    """


class Schedule(SQLModel, table=True):
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
    """远程发现源. 间隔与刮削属性按源绑定, 不写入 HotSettings."""

    __tablename__ = "feeds"  # type: ignore[assignment]

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(nullable=False)
    url: str = Field(unique=True, nullable=False, index=True)
    group: str = Field(default="", nullable=False)
    """斜杠伪路径 (如 jav/rsshub). 空串为未分组; 前端据此建树, 库内不存目录实体."""
    enabled: bool = Field(default=True)
    """是否纳入定期拉取. 关闭后仍可立即拉取."""
    auto_enqueue: bool = Field(default=True)
    """发现新番号时是否入队 SCRAPE. 关闭后仍写 FeedItem, 刮削改由历史表手动选."""
    interval_seconds: int = Field(default=3600)
    """拉取间隔 (秒). 范围由 API 校验 60–86400; 表单默认按小时."""
    number_pattern: str | None = None
    """可选正则; 设置后只使用该正则, 不回退 extract_number."""
    content_type: ContentType | None = None
    """显式片种; None 则 infer_content_type."""
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
    # 列表 ORDER BY coalesce(published_at, created_at), id; 表达式必须与查询一致才能命中该索引.
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
    """一等存储 (非 LRU). url 为 locator: 外部图用真实 URL; 裁剪派生用
    ``derived:{sha256(src_url)}:crop:{args}`` (args 为右侧比或 ``box:L,T,R,B``).
    """

    __tablename__ = "resources"  # type: ignore[assignment]

    id: int | None = Field(default=None, primary_key=True)
    url: str = Field(unique=True, nullable=False, index=True)
    file_path: str = Field(nullable=False)
    """相对 resources 目录的两级散列路径."""
    content_hash: str | None = None
    """文件 SHA-256; 就地超分后必须更新."""
    size: int | None = None
    mime_type: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    """派生/处理追溯. 裁剪: {'op':'crop','src':源url,'args':str};
    任意资源被超分后追加 {'sr': {tool,model,scale}}. 原始未处理图为 {}."""
    downloaded_at: datetime = Field(default_factory=_utcnow)


# --- 分类索引 (爬取侧投影) ---
#
# Metadata 上 JSON/标量列仍是刮削与 NFO 真值; 下列实体 + 关联表是查询投影.
# Actor 为一等人物实体 (孤儿保留): name 为展示名, 别名行在 ActorAlias; 跨名屏蔽见 FacetRule(block).


class Actor(SQLModel, table=True):
    """人物元数据宿主; 无影片关联时保留. name 为展示名 (全局唯一); 别名见 ActorAlias."""

    __tablename__ = "actors"  # type: ignore[assignment]

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(unique=True, nullable=False, index=True)

    gender: ActorGender = Field(default=ActorGender.UNKNOWN, index=True)
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
    """ID→名称一对多. ``(actor_id, name)`` 唯一; ``name`` 列不设全局唯一.
    不存展示名; 切换展示名须同时交换行 (旧展示名入表, 新展示名出表).
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
    """无影片关联时不自动删除."""

    __tablename__ = "directors"  # type: ignore[assignment]

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(unique=True, nullable=False, index=True)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class Tag(SQLModel, table=True):
    """爬取侧标签; 与 UserTag 隔离."""

    __tablename__ = "tags"  # type: ignore[assignment]

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(unique=True, nullable=False, index=True)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class Studio(SQLModel, table=True):
    __tablename__ = "studios"  # type: ignore[assignment]

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(unique=True, nullable=False, index=True)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class Publisher(SQLModel, table=True):
    __tablename__ = "publishers"  # type: ignore[assignment]

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(unique=True, nullable=False, index=True)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class Series(SQLModel, table=True):
    __tablename__ = "series"  # type: ignore[assignment]

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(unique=True, nullable=False, index=True)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class FacetRule(SQLModel, table=True):
    """爬取侧分类规则: 单跳 alias 或 block; 按 (kind, source_name) 唯一."""

    __tablename__ = "facet_rules"  # type: ignore[assignment]
    __table_args__ = (UniqueConstraint("kind", "source_name", name="uq_facet_rules_kind_source"),)

    id: int | None = Field(default=None, primary_key=True)
    kind: FacetKind = Field(index=True)
    source_name: str = Field(nullable=False, index=True)
    action: FacetRuleAction = Field()
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
    """用户标签; 刮削路径永不触碰."""

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
    __tablename__ = "comments"  # type: ignore[assignment]

    id: int | None = Field(default=None, primary_key=True)
    metadata_id: int = Field(foreign_key="metadata.id", index=True, ondelete="CASCADE")
    body: str = Field(nullable=False)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class AgentSessionStatus(StrEnum):
    ACTIVE = "active"
    AWAITING_APPROVAL = "awaiting_approval"
    CLOSED = "closed"


class SavedQueryEntity(StrEnum):
    """交付目标, 决定 Browse 深链与主键语义."""

    METADATA = "metadata"
    ACTOR = "actor"
    DATA = "data"


class AgentSession(SQLModel, table=True):
    """会话索引; 完整 trace 落盘, 不在本表."""

    __tablename__ = "agent_sessions"  # type: ignore[assignment]

    id: int | None = Field(default=None, primary_key=True)
    title: str = Field(default="新会话", nullable=False)
    status: AgentSessionStatus = Field(default=AgentSessionStatus.ACTIVE, index=True)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class SavedQuery(SQLModel, table=True):
    """查询预设; 权威为 SQL, 结果仅内存缓存."""

    __tablename__ = "saved_queries"  # type: ignore[assignment]

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(nullable=False)
    sql: str = Field(nullable=False)
    entity: SavedQueryEntity = Field(index=True)
    session_id: int | None = Field(default=None, foreign_key="agent_sessions.id", index=True)
    persisted: bool = Field(default=False, index=True)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
