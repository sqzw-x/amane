from pydantic import BaseModel, ConfigDict, Field


class PlaybackSubtitleItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    language: str | None = None
    href: str


class PlaybackSourceItem(BaseModel):
    """列表里的一条流. ``name`` 是主机拼好的展示名 (来源名 · 流的展示名).

    ``key`` 是这条流的标识, 同时出现在 ``href`` 里; 来源整个不可用时为 ``None``.
    """

    model_config = ConfigDict(extra="forbid")

    source_id: str
    key: str | None = None
    name: str
    content_type: str
    seekable: bool
    available: bool
    detail: str | None = None
    href: str
    subtitles: list[PlaybackSubtitleItem] = Field(default_factory=list)


class PlaybackSourceListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[PlaybackSourceItem] = Field(default_factory=list)
