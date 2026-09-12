from pydantic import BaseModel, ConfigDict, Field


class PlaybackSubtitleItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    language: str | None = None
    href: str


class PlaybackSourceItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    name: str
    content_type: str
    seekable: bool
    available: bool
    media_file_id: int | None = None
    detail: str | None = None
    href: str
    subtitles: list[PlaybackSubtitleItem] = Field(default_factory=list)


class PlaybackSourceListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[PlaybackSourceItem] = Field(default_factory=list)
