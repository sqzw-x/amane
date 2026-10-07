"""Stable identifiers and descriptors exposed by the Amane source plugin API."""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..enums import SiteName

SourceId = str

PLUGIN_API_VERSION = "1"
_SOURCE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_SEGMENT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

RESERVED_SOURCE_NAMESPACES = frozenset({"amane", "plugin", "official", "builtin"})
_BLOCKED_NAMESPACES = RESERVED_SOURCE_NAMESPACES | frozenset(SiteName)


def validate_external_source_id(source_id: str) -> str:
    """Require a third-party source id of the form ``namespace.local``.

    Official / in-tree sources stay single-segment (``javdb``). Third-party
    drop-ins must declare a developer namespace so they cannot squat builtin names
    or the reserved words ``amane`` / ``plugin`` / ``official`` / ``builtin``.
    """
    parts = source_id.split(".")
    if len(parts) < 2 or not all(_SEGMENT_RE.fullmatch(part) for part in parts):
        raise ValueError("third-party source id must be namespace.local using lowercase letters, digits, '_' or '-'")
    namespace = parts[0]
    if namespace in _BLOCKED_NAMESPACES:
        raise ValueError(f"source namespace {namespace!r} is reserved")
    return source_id


def is_external_source_id(source_id: str) -> bool:
    """Return whether ``source_id`` is a well-formed third-party namespaced id."""
    try:
        validate_external_source_id(source_id)
    except ValueError:
        return False
    return True


class SourceCapability(StrEnum):
    """Capabilities understood by the core source pipeline."""

    FILM_METADATA = "film_metadata"
    ACTOR_PROFILE = "actor_profile"
    ACTOR_IMAGE = "actor_image"
    PLAYBACK = "playback"


class SourceTrait(StrEnum):
    """Source-declared behavior switches the host reads to schedule or call a source.

    Unknown values are ignored with a warning, so a plugin may declare values added by a
    newer host. Distinct from ``SourceCapability``: this says how the host calls the
    source, not what the source provides.
    """

    # 依赖前序来源的聚合结果: 排第二段执行, 收到只读 ``partial_result``.
    # 单层依赖: 同段来源彼此不可见, 不支持来源之间互相声明依赖.
    NEEDS_PARTIAL = "needs_partial"
    # 消费 ``FetchOptions.language``: 聚合按 (来源, 语言) 展开获取节点.
    MULTI_LANGUAGE = "multi_language"
    # 刮削前按需计算 oshash, 经 ``SearchQuery.file_hash`` 传入.
    USES_FILE_HASH = "uses_file_hash"


class SourceDescriptor(BaseModel):
    """Stable, serializable description of a metadata source."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: SourceId = Field(description="Stable source identifier")
    name: str = Field(description="Human-readable source name")
    version: str = "builtin"
    api_version: str = PLUGIN_API_VERSION
    capabilities: frozenset[str] = frozenset({SourceCapability.FILM_METADATA})
    content_types: frozenset[str] = frozenset()
    metadata_fields: frozenset[str] = frozenset()
    languages: frozenset[str] = frozenset()
    urls: tuple[str, ...] = ()
    traits: frozenset[str] = frozenset()
    """行为开关, 取值见 ``SourceTrait``; 未知取值被忽略, 允许插件先声明宿主后续新增的值."""
    rate_limit: float | None = Field(default=None, ge=0.1, le=100)

    @field_validator("id")
    @classmethod
    def _valid_id(cls, value: str) -> str:
        if not _SOURCE_ID_RE.fullmatch(value):
            raise ValueError(
                "source id must contain only lowercase letters, digits, '.', '_' or '-' "
                "and start with a letter or digit"
            )
        return value

    @field_validator("name")
    @classmethod
    def _non_empty_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("source name must not be empty")
        return value

    def supports(self, capability: SourceCapability) -> bool:
        """Return whether the source advertises a core capability."""
        return capability in self.capabilities


class PluginConfig(BaseModel):
    """Persisted configuration envelope for one external plugin."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    config: dict[str, object] = Field(default_factory=dict)


class PluginOrigin(BaseModel):
    """On-disk identity of a discovered source drop-in."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    plugin_id: str
    path: str
    module: str
