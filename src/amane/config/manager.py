import contextlib
import os
import tempfile
import tomllib
from copy import copy
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

import tomli_w
from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

from ..crawlers.site_roles import (
    ACTOR_IMAGE_SITES,
    ACTOR_PROFILE_SITES,
    FILM_METADATA_SITES,
    assert_sites_allowed,
    site_list_schema,
    site_list_value_schema,
)
from ..crawlers.sites.official import Manufacturer
from ..enums import DownloadableResource, Language, MetadataField, SiteName, WatermarkCorner, WatermarkKind
from ..parsing import ContentType
from ..plugins.models import PluginConfig
from ..sr import SrPreset
from ..utils.model import kv

SAFE_DIRS_ALLOW_ALL: Literal["ALLOW_ALL"] = "ALLOW_ALL"

LANG_METADATA_FIELD_SET: frozenset[MetadataField] = frozenset(
    {
        MetadataField.TITLE,
        MetadataField.PLOT,
        MetadataField.ACTORS,
        MetadataField.DIRECTORS,
        MetadataField.TAGS,
        MetadataField.SERIES,
        MetadataField.PUBLISHER,
        MetadataField.STUDIO,
    }
)

# json_schema_extra 需要 JsonValue 兼容类型, 使用 list[Any] 避免 pyright invariance 问题
_SITES_WITH_API_TOKEN: list[Any] = [SiteName.THEPORNDB]

#: 各内容类型默认有序路由 (资格真值 + 该类型默认字段优先级).
_DEFAULT_CONTENT_ROUTES: dict[ContentType, list[SiteName]] = {
    ContentType.CENSORED: [
        SiteName.DMM,
        SiteName.JAVDB,
        SiteName.JAVBUS,
        SiteName.OFFICIAL,
    ],
    ContentType.UNCENSORED: [
        SiteName.JAVDB,
        SiteName.JAVBUS,
        SiteName.AVSOX,
        SiteName.FREEJAVBT,
    ],
    ContentType.FC2: [
        SiteName.JAVDB,
        SiteName.FC2PPVDB,
        SiteName.FC2,
        SiteName.FREEJAVBT,
    ],
    ContentType.CHINESE: [
        SiteName.IQQTV,
        SiteName.JAVDB,
        SiteName.AIRAV,
        SiteName.FREEJAVBT,
    ],
    ContentType.AMATEUR: [
        SiteName.MGSTAGE,
        SiteName.DMM,
        SiteName.JAVDB,
        SiteName.JAVBUS,
    ],
    ContentType.WESTERN: [
        SiteName.THEPORNDB,
        SiteName.JAVDB,
        SiteName.FREEJAVBT,
    ],
    ContentType.HENTAI: [
        SiteName.GETCHU,
        SiteName.DMM,
        SiteName.JAVDB,
    ],
}


class R18Config(BaseModel):
    """放 Hot: 修改 dsn 经 AppRuntime.rebuild() 重建只读引擎. 未配置 dsn 时整个数据源禁用.
    定时导入不在此节, 须经 Schedule API 创建 r18_import.
    """

    dsn: str | None = None
    """超级用户连接串, 须具备 CREATEDB/CREATEROLE. 为空 = 禁用 r18 (爬虫返回 None, 导入任务报错跳过)."""

    db_name: str = "r18dev"
    """导入先灌临时库, 校验通过后原子换名为此名."""

    read_user: str = "r18dev_readonly"
    """运行时查询使用的只读角色; 导入器自动创建并授权."""

    read_password: str = "r18dev_readonly"
    """仅本机连接, 仍不应留默认值用于公网暴露的 PG."""

    read_timeout: int = Field(default=30, ge=1, le=600)
    """只读角色 statement_timeout (秒)."""

    download_url: str | None = None
    """dump 归档下载地址. 为空 = 不自动导入."""

    psql_path: str = "psql"
    """导入 dump 经由子进程 ``psql -f``; 容器须带 postgresql-client."""

    @property
    def enabled(self) -> bool:
        return bool(self.dsn)

    def admin_url(self, database: str | None = None, *, async_mode: bool = True) -> str:
        """复用 dsn 的 host/port/凭据, 只替换目标库. ``database=None`` 时连 dsn 自带库或 postgres."""
        if not self.dsn:
            raise ValueError("r18.dsn 未配置")
        url = make_url(self.dsn)
        if not async_mode:
            url = url.set(drivername="postgresql")
        elif "+" not in url.drivername:
            url = url.set(drivername="postgresql+asyncpg")
        if database is not None:
            url = url.set(database=database)
        elif not url.database:
            url = url.set(database="postgres")
        return url.render_as_string(hide_password=False)

    def read_url(self, *, async_mode: bool = True) -> str:
        """指向已导入的 db_name; 运行时查询使用."""
        if not self.dsn:
            raise ValueError("r18.dsn 未配置")
        drivername = "postgresql+asyncpg" if async_mode else "postgresql"
        return (
            make_url(self.dsn)
            .set(
                drivername=drivername,
                username=self.read_user,
                password=self.read_password,
                database=self.db_name,
            )
            .render_as_string(hide_password=False)
        )


class ColdSettings(BaseSettings):
    """仅从环境变量加载; 运行时不可更改, 须重启才生效."""

    model_config = SettingsConfigDict(env_prefix="AMANE_", env_file=(".env.dev", ".env"), env_file_encoding="utf-8")

    data_dir: Path = Path("./data")

    log_dir: Path = Path("./logs")

    safe_dirs: list[Path] | Literal["ALLOW_ALL"] | None = None
    """逗号分隔路径. 整值 ``ALLOW_ALL`` 关闭边界; 未设置时从 library 路径推导."""

    test_log: bool = False
    """AMANE_TEST_LOG=1 时启用随机日志发射器."""

    token: str | None = None
    """``off`` 显式关闭; 为空时自动生成并持久化到 data_dir/token."""

    supervised: bool = False
    """为真时挂载重启端点 (AMANE_SUPERVISED=1)."""

    update_url: str | None = None
    """覆盖 GitHub /releases/latest. 空 = 官方 API."""

    @field_validator("update_url", mode="before")
    @classmethod
    def _empty_update_url(cls, v: object) -> object:
        if v == "":
            return None
        return v

    @field_validator("safe_dirs", mode="before")
    @classmethod
    def _parse_safe_dirs(cls, v: object) -> list[Path] | Literal["ALLOW_ALL"] | None:
        """逗号分隔路径; 整段 ``ALLOW_ALL`` 为关闭边界的哨兵 (大小写敏感, 不可混在路径列表里)."""
        if v is None:
            return None
        if isinstance(v, str):
            stripped = v.strip()
            if stripped == SAFE_DIRS_ALLOW_ALL:
                return SAFE_DIRS_ALLOW_ALL
            parts = [p.strip() for p in v.split(",") if p.strip()]
            return [Path(p) for p in parts] if parts else None
        if isinstance(v, (list, tuple)):
            paths: list[Path] = []
            for item in v:
                if isinstance(item, Path):
                    paths.append(item)
                elif isinstance(item, str):
                    paths.append(Path(item))
                else:
                    raise ValueError(f"Invalid AMANE_SAFE_DIRS item: {item!r}")
            return paths
        raise ValueError(f"Invalid AMANE_SAFE_DIRS value: {v!r}")

    @property
    def config_path(self) -> Path:
        return self.data_dir / "config.toml"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "amane.db"


class SiteConfig(BaseModel):
    base_url: str | None = None
    use_proxy: bool = True
    use_browser: bool = Field(default=False, json_schema_extra={"x-hidden": True})
    cookie: dict[str, str] = {}
    api_token: str | None = Field(default=None, json_schema_extra={"x-visible-keys": _SITES_WITH_API_TOKEN})
    official_routes: dict[str, Manufacturer] = Field(
        default_factory=dict, json_schema_extra={"x-visible-keys": [SiteName.OFFICIAL]}
    )
    """OfficialCrawler 的番号前缀→域名路由."""

    rate_limit: float | None = Field(default=2, ge=0.1, le=100)
    """req/s. 全局 network.rate_limits 有此站点域名时全局优先."""


class ScrapingConfig(BaseModel):
    download_resources: list[DownloadableResource] = Field(
        default_factory=lambda: [r for r in DownloadableResource if r != DownloadableResource.trailer],
        description="刮削时自动下载到 Resource 目录的资源类型",
    )
    crop_poster: bool = True

    poster_ratio: float = Field(default=0.7, ge=0.3, le=1.0)
    """海报裁剪宽高比 (w/h). 从缩略图右侧裁剪生成海报. 默认 0.7 (贴近常见 379x538 / 高清海报)."""

    poster_crop_skip_ratio: float = Field(default=0.9, ge=0.5, le=1.0)
    """海报裁剪跳过阈值. 当 poster 候选高度已达 thumb 高度的此比例以上时, 视为候选够用, 不再从 thumb 裁剪
    (裁剪有错位风险, 非必要不做). 默认 0.9."""

    jpeg_quality: int = Field(default=95, ge=50, le=100, json_schema_extra={"x-hidden": True})

    content_routes: dict[ContentType, list[str]] = Field(
        default_factory=lambda: {ct: [str(site) for site in _DEFAULT_CONTENT_ROUTES.get(ct, [])] for ct in ContentType},
        json_schema_extra=kv(
            {
                "x-frozen-keys": True,
                **{f"v-{k}": v for k, v in site_list_value_schema(FILM_METADATA_SITES, ordered=True).items()},
            }
        ),
    )
    """该类型实际请求的站点 ⊆ 此表; field_priority 只在表内重排."""

    field_priority: dict[MetadataField, list[str]] = Field(
        default_factory=dict,
        json_schema_extra=kv(
            {
                "v-default": [],
                **{f"v-{k}": v for k, v in site_list_value_schema(FILM_METADATA_SITES, ordered=True).items()},
            }
        ),
    )
    """只写需要提前尝试的站点. 与该类型 content_routes 求交后前置; 不在路由中的站无效."""

    field_blacklist: dict[MetadataField, list[str]] = Field(
        default_factory=dict,
        json_schema_extra=kv(
            {
                "v-default": [],
                **{f"v-{k}": v for k, v in site_list_value_schema(FILM_METADATA_SITES, ordered=False).items()},
            }
        ),
    )
    """该字段取值时跳过这些站点. 编译时从该字段链上剔除; 不在路由中的站无效."""

    field_language: dict[MetadataField, Language] = Field(
        default_factory=lambda: {f: Language.ZH_CN for f in MetadataField if f in LANG_METADATA_FIELD_SET},
        json_schema_extra={"x-frozen-keys": True},
    )

    site_config: dict[str, SiteConfig] = Field(
        default_factory=lambda: {str(site): SiteConfig() for site in SiteName},
        json_schema_extra={"x-frozen-keys": True},
    )
    """含影片站与演员站."""

    @field_validator("content_routes", mode="before")
    @classmethod
    def _complete_content_routes(cls, v: Any) -> Any:
        return _complete_frozen_dict(
            v, {ct: [str(site) for site in _DEFAULT_CONTENT_ROUTES.get(ct, [])] for ct in ContentType}
        )

    @field_validator("field_language", mode="before")
    @classmethod
    def _complete_field_language(cls, v: Any) -> Any:
        return _complete_frozen_dict(v, {f: Language.ZH_CN for f in MetadataField if f in LANG_METADATA_FIELD_SET})

    @field_validator("site_config", mode="before")
    @classmethod
    def _complete_site_config(cls, v: Any) -> Any:
        return _complete_frozen_dict(v, {str(site): {} for site in SiteName})

    @field_validator("field_priority")
    @classmethod
    def _film_field_priority(cls, v: dict[MetadataField, list[str]]) -> dict[MetadataField, list[str]]:
        return _validate_film_field_map(v, prefix="field_priority")

    @field_validator("field_blacklist")
    @classmethod
    def _film_field_blacklist(cls, v: dict[MetadataField, list[str]]) -> dict[MetadataField, list[str]]:
        return _validate_film_field_map(v, prefix="field_blacklist")

    @field_validator("content_routes")
    @classmethod
    def _film_content_routes(cls, v: dict[ContentType, list[str]]) -> dict[ContentType, list[str]]:
        allowed = frozenset(FILM_METADATA_SITES)
        for ct, sites in v.items():
            assert_sites_allowed(sites, allowed, field=f"content_routes.{ct}", allow_external=True)
        return v

    @model_validator(mode="before")
    @classmethod
    def _drop_removed_fields(cls, data: Any) -> Any:
        """丢弃已移除的 skip_existing / write_nfo / debug_capture."""
        if isinstance(data, dict):
            data.pop("skip_existing", None)
            data.pop("write_nfo", None)
            data.pop("debug_capture", None)
        return data

    @model_validator(mode="before")
    @classmethod
    def _migrate_download_flags(cls, data: Any) -> Any:
        """download_thumb / download_extrafanart → download_resources."""
        if not isinstance(data, dict):
            return data
        has_legacy = "download_thumb" in data or "download_extrafanart" in data
        thumb = data.pop("download_thumb", None)
        extra = data.pop("download_extrafanart", None)
        if "download_resources" in data or not has_legacy:
            return data
        resources: list[DownloadableResource] = []
        if thumb is None or thumb:
            resources.extend(
                (
                    DownloadableResource.thumb,
                    DownloadableResource.poster,
                    DownloadableResource.trailer,
                )
            )
        if extra is None or extra:
            resources.append(DownloadableResource.extrafanart)
        data["download_resources"] = resources
        return data

    @model_validator(mode="before")
    @classmethod
    def _migrate_priority(cls, data: Any) -> Any:
        """default_priority 折叠进 content_routes 顺序; 去掉空 field_priority / field_blacklist."""
        if not isinstance(data, dict):
            return data

        for key in ("field_priority", "field_blacklist"):
            mapping = data.get(key)
            if isinstance(mapping, dict):
                data[key] = {k: v for k, v in mapping.items() if v}

        default_priority = data.pop("default_priority", None)
        if default_priority is None:
            return data

        order = [_site_value(s) for s in default_priority]
        routes = data.get("content_routes")
        if not isinstance(routes, dict):
            routes = {ct: [str(s) for s in sites] for ct, sites in _DEFAULT_CONTENT_ROUTES.items()}

        data["content_routes"] = {ct: _reorder_route(eligible, order) for ct, eligible in routes.items()}
        return data


def _validate_film_field_map(v: dict[MetadataField, list[str]], *, prefix: str) -> dict[MetadataField, list[str]]:
    allowed = frozenset(FILM_METADATA_SITES)
    out: dict[MetadataField, list[str]] = {}
    for field, sites in v.items():
        if not sites:
            continue
        assert_sites_allowed(sites, allowed, field=f"{prefix}.{field}", allow_external=True)
        out[field] = sites
    return out


def _site_value(site: Any) -> str:
    return str(site)


def _complete_frozen_dict(provided: Any, defaults: dict[str, Any]) -> Any:
    """保留已知 key 的用户值, 缺的用 defaults, 未知 key 丢弃."""
    if not isinstance(provided, dict):
        return provided
    overlay = {_site_value(k): val for k, val in provided.items() if _site_value(k) in defaults}
    return {k: overlay[k] if k in overlay else copy(v) for k, v in defaults.items()}


def _reorder_route(eligible: list[Any], order: list[str]) -> list[str]:
    """未出现在 order 里的保原序接上."""
    eligible_vals = [_site_value(s) for s in eligible]
    eligible_set = set(eligible_vals)
    ordered = [s for s in order if s in eligible_set]
    leftover = [s for s in eligible_vals if s not in set(ordered)]
    return ordered + leftover


class WatermarkConfig(BaseModel):
    enabled: bool = False
    scale: float = Field(default=0.08, ge=0.03, le=0.25)
    """角标高度 = 图高 × scale."""

    corners: dict[WatermarkKind, WatermarkCorner] = Field(
        default_factory=lambda: dict.fromkeys(WatermarkKind, WatermarkCorner.TOP_LEFT),
        json_schema_extra={"x-frozen-keys": True},
    )
    """缺 key 补左上; 未知 key 丢弃."""

    @field_validator("corners", mode="before")
    @classmethod
    def _complete_corners(cls, v: Any) -> Any:
        return _complete_frozen_dict(v, dict.fromkeys(WatermarkKind, WatermarkCorner.TOP_LEFT))


class NetworkConfig(BaseModel):
    proxy: str | None = None
    timeout: float = Field(default=10.0, ge=5.0, le=300.0)
    max_retries: int = Field(default=3, ge=0, le=10)
    max_clients: int = Field(default=50, ge=5, le=500, json_schema_extra={"x-hidden": True})
    browser_timeout: int = Field(default=15000, ge=5000, le=120000, json_schema_extra={"x-hidden": True})

    chunked_threshold: int = Field(default=2 * 1024**2, ge=512 * 1024, le=100 * 1024**2)
    """超过此大小 (字节) 启用分块并发下载."""

    chunk_size: int = Field(default=1 * 1024**2, ge=256 * 1024, le=50 * 1024**2)
    concurrency: int = Field(default=10, ge=1, le=50)
    rate_limits: dict[str, float] = Field(default_factory=dict)
    """优先级高于站点配置."""

    default_rate_limit: float = Field(default=5, ge=0.1, le=100)
    """优先级低于 network.rate_limits 与站点配置."""


class WorkerConfig(BaseModel):
    concurrency: int = Field(default=10, ge=1, le=64)
    poll_interval: float = Field(default=2.0, ge=0.1, le=10, json_schema_extra={"x-hidden": True})
    shutdown_timeout: float = Field(default=0, ge=0, le=120.0, json_schema_extra={"x-hidden": True})


class WatcherConfig(BaseModel):
    use_polling: bool = False
    """轮询观察器代替原生 OS 事件. NAS/NFS、Docker Desktop (macOS)、WSL2 等 inotify 不可靠时启用."""

    debounce_seconds: float = Field(default=3.0, ge=0.5, le=30.0)
    """文件变动后等待此时间再处理, 避免重复触发."""

    media_extensions: list[str] = Field(
        default_factory=lambda: [".mp4", ".mkv", ".avi", ".wmv", ".flv", ".mov", ".ts", ".iso", ".strm"]
    )


class LoggingConfig(BaseModel):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(default="INFO")
    debug_capture: bool = False
    """打开时每个任务始终落盘 HTTP 响应 body; 关闭时仅任务失败才落盘."""


class SrConfig(BaseModel):
    enabled: bool = False

    max_dim_threshold: int = Field(default=1200, ge=64, le=8192)
    """最长边 max(w,h) 小于此值才超分."""

    max_bytes_threshold: int = Field(default=2 * 1024**2, ge=64 * 1024, le=64 * 1024**2)
    preset: SrPreset = SrPreset.WAIFU_PHOTO_2X
    output_format: Literal["jpg", "png", "webp"] = "jpg"
    tta: bool = Field(default=False, json_schema_extra={"x-hidden": True})


class LLMConfig(BaseModel):
    """凭据放 Hot, 可在 UI 修改并热生效. 与 agent section 隔离."""

    enabled: bool = False
    """关闭时刮削管线跳过翻译步骤."""

    translate_fields: list[MetadataField] = Field(default_factory=lambda: [MetadataField.TITLE, MetadataField.PLOT])
    """当前仅支持文本标量字段 (title/plot)."""

    api_key: str | None = None
    """为空时即使 enabled 也不翻译."""

    base_url: str = "https://api.openai.com/v1"
    model: str = ""
    max_retries: int = Field(default=3, ge=0, le=10)
    rate_limit: float = Field(default=2.0, ge=0.1, le=100)
    """与站点限速隔离."""


class AgentApiType(StrEnum):
    CHAT = "chat"
    RESPONSE = "response"
    ANTHROPIC = "anthropic"


class AgentThinkingMode(StrEnum):
    """会话可覆盖; 全局为新建会话的回退默认."""

    OFF = "off"
    MINIMAL = "minimal"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"


class AgentConfig(BaseModel):
    """与 llm 翻译 section 分离: 凭据/模型/限速各自独立."""

    api_type: AgentApiType = AgentApiType.RESPONSE
    api_key: str | None = None
    base_url: str = "https://api.openai.com/v1"
    """可指向自建/第三方代理; anthropic 须填 Anthropic 端点."""

    model: str = "gpt-4o-mini"
    thinking: AgentThinkingMode | None = None
    """新建会话的回退默认; None 表示不传 thinking. 会话可在 meta 覆盖."""

    rate_limit: float = Field(default=2.0, ge=0.1, le=100)
    sql_timeout_ms: int = Field(default=1000, ge=50, le=60_000, json_schema_extra={"x-hidden": True})
    """只读 SQL 默认超时 (毫秒). 超过须用户批准放宽."""

    result_cache_ttl_s: int = Field(default=3600, ge=60, le=86_400, json_schema_extra={"x-hidden": True})
    """过期后按 SQL 重新执行."""

    result_cache_max_entries: int = Field(default=64, ge=1, le=1000, json_schema_extra={"x-hidden": True})
    history_ttl_s: int = Field(default=3600, ge=60, le=86_400, json_schema_extra={"x-hidden": True})
    """逐出后可从 data_dir messages.json 装回."""

    history_max_sessions: int = Field(default=32, ge=1, le=500, json_schema_extra={"x-hidden": True})

    @model_validator(mode="before")
    @classmethod
    def _drop_legacy_fields(cls, data: Any) -> Any:
        """丢弃已移除的 enabled / max_retries."""
        if isinstance(data, dict):
            data.pop("enabled", None)
            data.pop("max_retries", None)
        return data


class ActorScrapingConfig(BaseModel):
    """档案站顺序填空, 头像站优先."""

    profile_sites: list[SiteName] = Field(
        default_factory=lambda: list(ACTOR_PROFILE_SITES),
        json_schema_extra=site_list_schema(ACTOR_PROFILE_SITES, ordered=True),
        description="档案源顺序 (标量填空优先级); 仅演员档案站",
    )
    image_sites: list[SiteName] = Field(
        default_factory=lambda: list(ACTOR_IMAGE_SITES),
        json_schema_extra=site_list_schema(ACTOR_IMAGE_SITES, ordered=True),
        description="头像源顺序 (优先于档案站附图); 仅演员头像站",
    )
    download_images: bool = True
    auto_scrape: bool = True
    """影片刮削成功后自动入队该片演员的 ACTOR_SCRAPE; 已刮过的 Actor 跳过."""

    gfriends_repo: str = Field(default="https://github.com/gfriends/gfriends", description="gFriends GitHub 仓库 URL")

    @field_validator("profile_sites")
    @classmethod
    def _actor_profile_sites(cls, v: list[SiteName]) -> list[SiteName]:
        return assert_sites_allowed(v, frozenset(ACTOR_PROFILE_SITES), field="profile_sites")

    @field_validator("image_sites")
    @classmethod
    def _actor_image_sites(cls, v: list[SiteName]) -> list[SiteName]:
        return assert_sites_allowed(v, frozenset(ACTOR_IMAGE_SITES), field="image_sites")


class HotSettings(BaseModel):
    """运行时可更新, 持久化到 TOML. extra=forbid, 未知字段须校验失败."""

    model_config = {"extra": "forbid"}

    @model_validator(mode="before")
    @classmethod
    def _drop_removed_sections(cls, data: Any) -> Any:
        """丢弃已移除的 paths section."""
        if isinstance(data, dict):
            data.pop("paths", None)
        return data

    @model_validator(mode="before")
    @classmethod
    def _migrate_debug_capture(cls, data: Any) -> Any:
        """scraping.debug_capture → logging.debug_capture."""
        if not isinstance(data, dict):
            return data
        scraping = data.get("scraping")
        if not isinstance(scraping, dict) or "debug_capture" not in scraping:
            return data
        flag = scraping.pop("debug_capture")
        logging = data.get("logging")
        if not isinstance(logging, dict):
            logging = {}
            data["logging"] = logging
        logging.setdefault("debug_capture", flag)
        return data

    scraping: ScrapingConfig = ScrapingConfig()
    actor_scraping: ActorScrapingConfig = ActorScrapingConfig()
    agent: AgentConfig = AgentConfig()
    network: NetworkConfig = NetworkConfig()
    sr: SrConfig = SrConfig()
    watermark: WatermarkConfig = WatermarkConfig()
    llm: LLMConfig = LLMConfig()
    r18: R18Config = R18Config()
    watcher: WatcherConfig = WatcherConfig()
    worker: WorkerConfig = WorkerConfig()
    logging: LoggingConfig = LoggingConfig()
    plugins: dict[str, PluginConfig] = Field(default_factory=dict, json_schema_extra={"x-hidden": True})


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """仅两层. patch 中有而 base 中没有的 key 一并纳入."""
    result = {}
    for key in base:
        if key in patch and isinstance(base[key], dict) and isinstance(patch[key], dict):
            result[key] = {**base[key], **patch[key]}
        elif key in patch:
            result[key] = patch[key]
        else:
            result[key] = base[key]
    for key in patch:
        if key not in result:
            result[key] = patch[key]
    return result


class ConfigManager:
    """热重载配置. 加载优先级: TOML 文件值 > 代码默认值."""

    def __init__(self, cold: ColdSettings, hot: HotSettings) -> None:
        self._cold = cold
        self._hot = hot

    @property
    def cold(self) -> ColdSettings:
        return self._cold

    @property
    def hot(self) -> HotSettings:
        return self._hot

    @classmethod
    def with_cold(cls, cold: ColdSettings | None = None) -> ConfigManager:
        """TOML 不存在时写入默认文件再加载."""
        if cold is None:
            cold = ColdSettings()

        config_path = cold.config_path

        if config_path.exists():
            with open(config_path, "rb") as f:
                toml_data = tomllib.load(f)
        else:
            toml_data = {}
            config_path.parent.mkdir(parents=True, exist_ok=True)
            default_content = HotSettings().model_dump(mode="json", exclude_none=True, exclude_defaults=True)
            cls._write_toml(config_path, default_content)

        hot = HotSettings(**toml_data)

        return cls(cold=cold, hot=hot)

    def update(self, patch: HotSettings | dict) -> None:
        """校验通过后持久化并交换引用. 校验失败不改动当前配置."""
        if isinstance(patch, HotSettings):
            patch = patch.model_dump(exclude_unset=True)
        if not patch:
            return

        new_hot = self.preview(patch)

        self._persist(new_hot)
        self._hot = new_hot

    def preview(self, patch: HotSettings | dict) -> HotSettings:
        """校验补丁但不持久化, 也不改动当前配置."""
        if isinstance(patch, HotSettings):
            patch = patch.model_dump(exclude_unset=True)
        if not patch:
            return self._hot.model_copy(deep=True)
        current = self._hot.model_dump()
        merged = _deep_merge(current, patch)
        return HotSettings(**merged)

    def _persist(self, hot: HotSettings) -> None:
        """临时文件 + os.replace."""
        config_path = self._cold.config_path
        config_path.parent.mkdir(parents=True, exist_ok=True)

        data = hot.model_dump(mode="json", exclude_none=True, exclude_defaults=True)
        self._write_toml(config_path, data)

    @staticmethod
    def _write_toml(path: Path, data: dict[str, dict[str, Any]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)

        fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=".config_", suffix=".toml.tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                tomli_w.dump(data, f)
            Path(tmp_path).replace(path)
        except BaseException:
            with contextlib.suppress(OSError):
                Path(tmp_path).unlink()
            raise
