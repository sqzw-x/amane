"""Cold (环境变量) / Hot (TOML 可热更新) 分层."""

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
from ..enums import DownloadableResource, Language, MetadataField, SiteName
from ..parsing import ContentType
from ..plugins.models import PluginConfig
from ..sr import SrPreset
from ..utils.model import kv

SAFE_DIRS_ALLOW_ALL: Literal["ALLOW_ALL"] = "ALLOW_ALL"

# --- 配置 UI 元数据 (x-visible-keys, field_language 默认值) ---

#: 支持语言选择的元数据字段子集
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
    """r18.dev 离线 PostgreSQL 镜像的连接与导入配置.

    放 Hot (随其他刮削参数同源, 可在 UI 修改并热生效): 改 dsn 走 AppRuntime.rebuild() 链,
    重新创建只读引擎. 项目只持有连接信息, 由用户自备 PostgreSQL 实例; 项目负责建库 / 导入 dump /
    原子换名 / 创建只读用户全套. 未配置 dsn 时整个 r18 数据源静默禁用.

    定时导入不在此配置: 用户像添加其他定时任务一样, 通过 Schedule API 手动创建 r18_import 任务.
    """

    dsn: str | None = None
    """超级用户连接串 (postgresql://user:pass@host:port). 需 CREATEDB/CREATEROLE 权限.
    用于导入 dump、建库、创建只读角色. 为空 = 禁用 r18 数据源 (爬虫返回 None, 导入任务报错跳过)."""

    db_name: str = "r18dev"
    """目标数据库名. 导入时先灌临时库, 校验通过后原子换名为此名."""

    read_user: str = "r18dev_readonly"
    """运行时查询使用的只读角色名. 导入器自动创建并授权."""

    read_password: str = "r18dev_readonly"
    """只读角色密码. 仅本机连接, 但仍不应留默认值用于公网暴露的 PG."""

    read_timeout: int = Field(default=30, ge=1, le=600)
    """只读角色的 statement_timeout (秒). 防止异常查询长占连接."""

    download_url: str | None = None
    """dump 归档 (.sql.gz) 下载地址. 通常是一个 302/307 重定向到 S3 的稳定入口.
    为空 = 不自动导入 (用户可手动准备好 db_name 库)."""

    psql_path: str = "psql"
    """psql 可执行文件路径. 导入 dump 走子进程 psql -f; 容器需带 postgresql-client."""

    @property
    def enabled(self) -> bool:
        """是否已配置可用的连接串. 未配置时整个 r18 数据源禁用."""
        return bool(self.dsn)

    def admin_url(self, database: str | None = None, *, async_mode: bool = True) -> str:
        """超级用户连接 URL, 复用用户 dsn 的 host/port/凭据, 仅替换目标库.

        database=None 时连到 dsn 自带库 (或 'postgres'); 导入流程会显式传 'template1' / 临时库名.
        """
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
        """只读角色连接 URL, 指向已导入的 db_name 库. 运行时查询使用."""
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
    """
    不可变配置, 仅从环境变量加载.

    运行时不可更改 - 需要重启才能生效.
    """

    model_config = SettingsConfigDict(env_prefix="AMANE_", env_file=(".env.dev", ".env"), env_file_encoding="utf-8")

    data_dir: Path = Path("./data")
    """SQLite 数据库, TOML 配置文件及其他持久化数据的目录."""

    log_dir: Path = Path("./logs")
    """日志文件输出目录."""

    safe_dirs: list[Path] | Literal["ALLOW_ALL"] | None = None
    """文件浏览器 / 库路径允许访问的目录. AMANE_SAFE_DIRS 逗号分隔; 整值 ``ALLOW_ALL`` 关闭边界; 未设置时从 library 路径推导."""

    test_log: bool = False
    """启用随机日志发射器, 用于前端开发调试. AMANE_TEST_LOG=1 开启."""

    token: str | None = None
    """API 访问令牌. "off" 显式关闭 (容器反代场景); 为空时自动生成并持久化到 data_dir/token."""

    supervised: bool = False
    """进程外监督者在场 (AMANE_SUPERVISED=1). 为真时挂载重启端点; 由 compose / 桌面壳设置."""

    update_url: str | None = None
    """覆盖 GitHub /releases/latest 地址. 空 = 官方 API; 本地 mock 或镜像时设置 AMANE_UPDATE_URL."""

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
        """TOML 配置文件的路径."""
        return self.data_dir / "config.toml"

    @property
    def db_path(self) -> Path:
        """SQLite 数据库文件路径."""
        return self.data_dir / "amane.db"


# --- 嵌套配置 section ---


class SiteConfig(BaseModel):
    """单个站点的配置."""

    base_url: str | None = None
    """覆盖默认域名."""

    use_proxy: bool = True

    use_browser: bool = Field(default=False, json_schema_extra={"x-hidden": True})
    """是否使用浏览器渲染."""

    cookie: dict[str, str] = {}
    """登录 Cookie."""

    api_token: str | None = Field(default=None, json_schema_extra={"x-visible-keys": _SITES_WITH_API_TOKEN})
    """API 密钥."""

    official_routes: dict[str, Manufacturer] = Field(
        default_factory=dict, json_schema_extra={"x-visible-keys": [SiteName.OFFICIAL]}
    )
    """OfficialCrawler 的番号前缀→域名路由规则."""

    rate_limit: float | None = Field(default=2, ge=0.1, le=100)
    """每站点的速率限制 (req/s). 如全局 network.rate_limits 有此站点域名的覆盖, 全局优先."""


class ScrapingConfig(BaseModel):
    """刮削行为配置."""

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
    """图片保存 JPEG 质量 (1-100). 越高质量越好但文件越大."""

    content_routes: dict[ContentType, list[str]] = Field(
        default_factory=lambda: {
            ct: [site.value for site in _DEFAULT_CONTENT_ROUTES.get(ct, [])] for ct in ContentType
        },
        json_schema_extra=kv(
            {
                "x-frozen-keys": True,
                **{f"v-{k}": v for k, v in site_list_value_schema(FILM_METADATA_SITES, ordered=True).items()},
            }
        ),
    )
    """内容类型 → 有序站点链 (资格真值 + 该类型默认字段优先级).
    该类型实际请求的站点 ⊆ 此表; field_priority 只在表内重排."""

    field_priority: dict[MetadataField, list[str]] = Field(
        default_factory=dict,
        json_schema_extra=kv(
            {
                "v-default": [],
                **{f"v-{k}": v for k, v in site_list_value_schema(FILM_METADATA_SITES, ordered=True).items()},
            }
        ),
    )
    """稀疏字段优先: 只写需要提前尝试的站点. 与该类型 content_routes 求交后前置,
    其余路由站点保序回退. 不在该类型路由中的站无效.
    示例: {"title": ["iqqtv"], "thumb_urls": ["dmm"]}"""

    field_language: dict[MetadataField, Language] = Field(
        default_factory=lambda: {f: Language.ZH_CN for f in MetadataField if f in LANG_METADATA_FIELD_SET},
        json_schema_extra={"x-frozen-keys": True},
    )
    """字段级语言偏好. key=字段名, value=语言代码.
    示例: {"title": "zh_cn", "plot": "jp"}"""

    site_config: dict[str, SiteConfig] = Field(
        default_factory=lambda: {site.value: SiteConfig() for site in SiteName},
        json_schema_extra={"x-frozen-keys": True},
    )
    """按站点的配置覆盖 (key 为爬虫名称). 含影片站与演员站 (限速/cookie 等)."""

    @field_validator("content_routes", mode="before")
    @classmethod
    def _complete_content_routes(cls, v: Any) -> Any:
        return _complete_frozen_dict(
            v, {ct.value: [s.value for s in _DEFAULT_CONTENT_ROUTES.get(ct, [])] for ct in ContentType}
        )

    @field_validator("field_language", mode="before")
    @classmethod
    def _complete_field_language(cls, v: Any) -> Any:
        return _complete_frozen_dict(
            v, {f.value: Language.ZH_CN.value for f in MetadataField if f in LANG_METADATA_FIELD_SET}
        )

    @field_validator("site_config", mode="before")
    @classmethod
    def _complete_site_config(cls, v: Any) -> Any:
        return _complete_frozen_dict(v, {site.value: {} for site in SiteName})

    @field_validator("field_priority")
    @classmethod
    def _film_field_priority(cls, v: dict[MetadataField, list[str]]) -> dict[MetadataField, list[str]]:
        allowed = frozenset(FILM_METADATA_SITES)
        out: dict[MetadataField, list[str]] = {}
        for field, sites in v.items():
            if not sites:
                continue
            assert_sites_allowed(sites, allowed, field=f"field_priority.{field.value}", allow_external=True)
            out[field] = sites
        return out

    @field_validator("content_routes")
    @classmethod
    def _film_content_routes(cls, v: dict[ContentType, list[str]]) -> dict[ContentType, list[str]]:
        allowed = frozenset(FILM_METADATA_SITES)
        for ct, sites in v.items():
            assert_sites_allowed(sites, allowed, field=f"content_routes.{ct.value}", allow_external=True)
        return v

    @model_validator(mode="before")
    @classmethod
    def _drop_removed_fields(cls, data: Any) -> Any:
        """兼容旧 TOML: 丢弃已移除的 skip_existing / write_nfo / debug_capture."""
        if isinstance(data, dict):
            data.pop("skip_existing", None)
            data.pop("write_nfo", None)
            data.pop("debug_capture", None)
        return data

    @model_validator(mode="before")
    @classmethod
    def _migrate_download_flags(cls, data: Any) -> Any:
        """兼容旧 TOML: download_thumb / download_extrafanart → download_resources."""
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
        """兼容旧 TOML: default_priority 折叠进 content_routes 顺序; 去掉空 field_priority."""
        if not isinstance(data, dict):
            return data

        fp = data.get("field_priority")
        if isinstance(fp, dict):
            data["field_priority"] = {k: v for k, v in fp.items() if v}

        default_priority = data.pop("default_priority", None)
        if default_priority is None:
            return data

        order = [_site_value(s) for s in default_priority]
        routes = data.get("content_routes")
        if not isinstance(routes, dict):
            routes = {ct.value: [s.value for s in sites] for ct, sites in _DEFAULT_CONTENT_ROUTES.items()}

        data["content_routes"] = {ct: _reorder_route(eligible, order) for ct, eligible in routes.items()}
        return data


def _site_value(site: Any) -> str:
    return site.value if isinstance(site, StrEnum) else str(site)


def _complete_frozen_dict(provided: Any, defaults: dict[str, Any]) -> Any:
    """补齐 x-frozen-keys 字典: 保留已知 key 的用户值, 缺的用 defaults, 未知 key 丢弃."""
    if not isinstance(provided, dict):
        return provided
    overlay = {_site_value(k): val for k, val in provided.items() if _site_value(k) in defaults}
    return {k: overlay[k] if k in overlay else copy(v) for k, v in defaults.items()}


def _reorder_route(eligible: list[Any], order: list[str]) -> list[str]:
    """按 order 重排 eligible, 未出现在 order 里的保原序接上."""
    eligible_vals = [_site_value(s) for s in eligible]
    eligible_set = set(eligible_vals)
    ordered = [s for s in order if s in eligible_set]
    leftover = [s for s in eligible_vals if s not in set(ordered)]
    return ordered + leftover


class NetworkConfig(BaseModel):
    """网络配置."""

    proxy: str | None = None
    """SOCKS/HTTP 代理 URL (例如 socks5://127.0.0.1:7890)."""

    timeout: float = Field(default=10.0, ge=5.0, le=300.0)
    """HTTP 请求默认超时 (秒)."""

    max_retries: int = Field(default=3, ge=0, le=10)
    """请求失败时的最大重试次数."""

    max_clients: int = Field(default=50, ge=5, le=500, json_schema_extra={"x-hidden": True})
    """连接池最大连接数."""

    browser_timeout: int = Field(default=15000, ge=5000, le=120000, json_schema_extra={"x-hidden": True})
    """无头浏览器页面导航超时 (毫秒)."""

    chunked_threshold: int = Field(default=2 * 1024**2, ge=512 * 1024, le=100 * 1024**2)
    """超过此大小 (字节) 启用分块并发下载. 默认 2 MB."""

    chunk_size: int = Field(default=1 * 1024**2, ge=256 * 1024, le=50 * 1024**2)
    """分块下载的每块大小 (字节). 默认 1 MB."""

    concurrency: int = Field(default=10, ge=1, le=50)
    """分块下载的并发数."""

    rate_limits: dict[str, float] = Field(default_factory=dict)
    """按域名的速率限制覆盖 (req/s). key=主机名 (如 javdb.com), value=每秒最大请求数. 优先级高于站点配置."""

    default_rate_limit: float = Field(default=5, ge=0.1, le=100)
    """未单独配置域名的默认请求速率 (req/s). 优先级低于 network.rate_limits 与站点配置."""


class WorkerConfig(BaseModel):
    """任务引擎配置."""

    concurrency: int = Field(default=10, ge=1, le=64)
    """最大并发任务执行数."""

    poll_interval: float = Field(default=2.0, ge=0.1, le=10, json_schema_extra={"x-hidden": True})
    """空闲时任务队列的轮询间隔 (秒)."""

    shutdown_timeout: float = Field(default=0, ge=0, le=120.0, json_schema_extra={"x-hidden": True})
    """关闭时等待活跃任务完成的最长时间 (秒)."""


class WatcherConfig(BaseModel):
    """文件监控配置."""

    use_polling: bool = False
    """使用轮询观察器代替原生 OS 事件. 适用于 NAS/NFS 挂载、Docker Desktop (macOS)、WSL2 等 inotify 不可靠的场景."""

    debounce_seconds: float = Field(default=3.0, ge=0.5, le=30.0)
    """文件事件防抖窗口 (秒). 文件变动后等待此时间再处理, 避免重复触发."""

    media_extensions: list[str] = Field(
        default_factory=lambda: [".mp4", ".mkv", ".avi", ".wmv", ".flv", ".mov", ".ts", ".iso", ".strm"]
    )
    """媒体文件扩展名白名单. 仅匹配这些扩展名的文件会被监控和扫描."""


class LoggingConfig(BaseModel):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(default="INFO")
    """日志级别"""

    debug_capture: bool = False
    """打开时每个任务始终落盘 HTTP 响应 body 到任务记录; 关闭时仅任务失败才落盘 body."""


class SrConfig(BaseModel):
    """图像超分增强配置."""

    enabled: bool = False
    """在 scrape 时立刻超分."""

    max_dim_threshold: int = Field(default=1200, ge=64, le=8192)
    """超分触发的尺寸阈值. 图像最长边 max(w,h) 小于此值才超分."""

    max_bytes_threshold: int = Field(default=2 * 1024**2, ge=64 * 1024, le=64 * 1024**2)
    """超分触发的文件大小阈值 (字节). 默认 2 MB."""

    preset: SrPreset = SrPreset.WAIFU_PHOTO_2X
    """超分预设 — 工具 + 模型 + 倍率 + 降噪的固定组合."""

    output_format: Literal["jpg", "png", "webp"] = "jpg"
    """输出格式."""

    tta: bool = Field(default=False, json_schema_extra={"x-hidden": True})
    """启用 TTA 提升质量."""


class LLMConfig(BaseModel):
    """LLM 翻译配置.

    凭据放 Hot 而非 Cold: 与其他刮削参数同源, 可在 UI 修改并热生效.
    后端经端口抽象 (amane/llm), 当前为 OpenAI 兼容后端.
    """

    enabled: bool = False
    """是否启用 LLM 翻译. 关闭时刮削管线跳过翻译步骤."""

    translate_fields: list[MetadataField] = Field(default_factory=lambda: [MetadataField.TITLE, MetadataField.PLOT])
    """需 LLM 翻译的字段子集. 当前仅支持文本标量字段 (title/plot)."""

    api_key: str | None = None
    """OpenAI 兼容 API 密钥. 为空时即使 enabled 也不翻译."""

    base_url: str = "https://api.openai.com/v1"
    """OpenAI 兼容端点. 可指向自建/第三方代理."""

    model: str = ""
    """聊天补全模型名."""

    max_retries: int = Field(default=3, ge=0, le=10)
    """请求失败重试次数 (指数退避)."""

    rate_limit: float = Field(default=2.0, ge=0.1, le=100)
    """LLM 端点请求速率 (req/s). 与站点限速隔离."""


class AgentApiType(StrEnum):
    """助理 Agent 上游 LLM API 协议."""

    CHAT = "chat"
    """OpenAI Chat Completions (`/v1/chat/completions`)."""

    RESPONSE = "response"
    """OpenAI Responses API."""

    ANTHROPIC = "anthropic"
    """Anthropic Messages API."""


class AgentThinkingMode(StrEnum):
    """思考/推理强度. 会话可覆盖; 全局为新建回落默认."""

    OFF = "off"
    MINIMAL = "minimal"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"


class AgentConfig(BaseModel):
    """助理 Agent 配置 (产品面称 Amane).

    与 llm 翻译 section 分离: 凭据/模型/限速各自独立.
    """

    api_type: AgentApiType = AgentApiType.RESPONSE
    """上游 API 协议: chat / response / anthropic."""

    api_key: str | None = None
    """对应提供商的 API 密钥."""

    base_url: str = "https://api.openai.com/v1"
    """对应提供商的 API 端点. 可指向自建/第三方代理; anthropic 需填 Anthropic 端点."""

    model: str = "gpt-4o-mini"
    """模型名 (随提供商而定)."""

    thinking: AgentThinkingMode | None = None
    """新建会话回落的思考强度默认; None 表示不传 thinking (跟模型默认). 会话可在 meta 覆盖."""

    rate_limit: float = Field(default=2.0, ge=0.1, le=100)
    """助理 Agent LLM 端点请求速率 (req/s)."""

    sql_timeout_ms: int = Field(default=1000, ge=50, le=60_000, json_schema_extra={"x-hidden": True})
    """只读 SQL 默认超时 (毫秒). 超过需用户批准放宽."""

    result_cache_ttl_s: int = Field(default=3600, ge=60, le=86_400, json_schema_extra={"x-hidden": True})
    """交付结果内存缓存 TTL (秒). 过期后按 SQL 重跑."""

    result_cache_max_entries: int = Field(default=64, ge=1, le=1000, json_schema_extra={"x-hidden": True})
    """内存结果缓存最大条目数 (LRU)."""

    history_ttl_s: int = Field(default=3600, ge=60, le=86_400, json_schema_extra={"x-hidden": True})
    """会话 message_history 内存热缓存 TTL (秒). 逐出后可从 data_dir messages.json 装回."""

    history_max_sessions: int = Field(default=32, ge=1, le=500, json_schema_extra={"x-hidden": True})
    """内存中同时保留的会话历史上限 (LRU)."""

    @model_validator(mode="before")
    @classmethod
    def _drop_legacy_fields(cls, data: Any) -> Any:
        """兼容旧 TOML: 丢弃已移除的 enabled / max_retries."""
        if isinstance(data, dict):
            data.pop("enabled", None)
            data.pop("max_retries", None)
        return data


class ActorScrapingConfig(BaseModel):
    """演员元数据刮削 - 档案站顺序填空, 头像站优先."""

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
    """影片刮削成功后自动链式入队该片演员的 ACTOR_SCRAPE 任务 (已刮过的 Actor 跳过)."""

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
    """
    运行时可更新的配置, 持久化到 TOML.

    嵌套结构直接对应 TOML section, 无需映射转换.
    禁止额外字段以便尽早捕获拼写错误.
    """

    model_config = {"extra": "forbid"}

    @model_validator(mode="before")
    @classmethod
    def _drop_removed_sections(cls, data: Any) -> Any:
        """兼容旧 TOML: 丢弃已移除的 paths section."""
        if isinstance(data, dict):
            data.pop("paths", None)
        return data

    @model_validator(mode="before")
    @classmethod
    def _migrate_debug_capture(cls, data: Any) -> Any:
        """兼容旧 TOML: scraping.debug_capture → logging.debug_capture."""
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
    worker: WorkerConfig = WorkerConfig()
    watcher: WatcherConfig = WatcherConfig()
    logging: LoggingConfig = LoggingConfig()
    sr: SrConfig = SrConfig()
    llm: LLMConfig = LLMConfig()
    r18: R18Config = R18Config()
    plugins: dict[str, PluginConfig] = Field(default_factory=dict, json_schema_extra={"x-hidden": True})


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """将 patch 深度合并到 base 的副本中 (仅两层)."""
    result = {}
    for key in base:
        if key in patch and isinstance(base[key], dict) and isinstance(patch[key], dict):
            result[key] = {**base[key], **patch[key]}
        elif key in patch:
            result[key] = patch[key]
        else:
            result[key] = base[key]
    # patch 中有但 base 中没有的 key
    for key in patch:
        if key not in result:
            result[key] = patch[key]
    return result


class ConfigManager:
    """
    管理由 TOML 文件支持的热重载配置.

    加载优先级: TOML 文件值 > 代码默认值.
    """

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
        """
        从 TOML 文件加载配置.

        若 TOML 文件不存在, 则使用默认值创建.
        """
        if cold is None:
            cold = ColdSettings()

        config_path = cold.config_path

        # 读取 TOML (若存在), 否则创建默认文件
        if config_path.exists():
            with open(config_path, "rb") as f:
                toml_data = tomllib.load(f)
        else:
            toml_data = {}
            # 创建父目录并写入默认值
            config_path.parent.mkdir(parents=True, exist_ok=True)
            default_content = HotSettings().model_dump(mode="json", exclude_none=True, exclude_defaults=True)
            cls._write_toml(config_path, default_content)

        # 直接用 TOML 数据构造 HotSettings (Pydantic 处理嵌套 dict)
        hot = HotSettings(**toml_data)

        return cls(cold=cold, hot=hot)

    def update(self, patch: HotSettings | dict) -> None:
        """
        应用分段补丁, 校验, 持久化, 并交换引用.

        Raises:
            ValidationError: 若合并后的值未通过校验.
        """
        if isinstance(patch, HotSettings):
            patch = patch.model_dump(exclude_unset=True)
        if not patch:
            return

        new_hot = self.preview(patch)

        # 原子化持久化
        self._persist(new_hot)

        # 交换引用
        self._hot = new_hot

    def preview(self, patch: HotSettings | dict) -> HotSettings:
        """Validate a patch without persisting or changing the active config."""
        if isinstance(patch, HotSettings):
            patch = patch.model_dump(exclude_unset=True)
        if not patch:
            return self._hot.model_copy(deep=True)
        current = self._hot.model_dump()
        merged = _deep_merge(current, patch)
        return HotSettings(**merged)

    def _persist(self, hot: HotSettings) -> None:
        """原子化写入配置到 TOML (临时文件 + os.replace)."""
        config_path = self._cold.config_path
        config_path.parent.mkdir(parents=True, exist_ok=True)

        data = hot.model_dump(mode="json", exclude_none=True, exclude_defaults=True)
        self._write_toml(config_path, data)

    @staticmethod
    def _write_toml(path: Path, data: dict[str, dict[str, Any]]) -> None:
        """原子化写入 TOML 数据到文件."""
        path.parent.mkdir(parents=True, exist_ok=True)

        # 写入同目录下的临时文件, 然后原子化重命名
        fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=".config_", suffix=".toml.tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                tomli_w.dump(data, f)
            Path(tmp_path).replace(path)
        except BaseException:
            # 任何失败时清理临时文件
            with contextlib.suppress(OSError):
                Path(tmp_path).unlink()
            raise
