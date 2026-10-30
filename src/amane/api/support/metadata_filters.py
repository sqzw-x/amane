"""列表与语义检索共用的筛选参数.

两处端点暴露同一组 query 参数: 列表用它分页, 语义检索用它把候选集收窄到筛选结果内部.
"""

from dataclasses import dataclass
from typing import Annotated

from fastapi import HTTPException, Query

from ...parsing import DEFINITION_VALUES, ContentType, Mosaic


@dataclass(frozen=True)
class MetadataFilters:
    actor_id: list[int] | None = None
    director_id: list[int] | None = None
    tag_id: list[int] | None = None
    studio_id: list[int] | None = None
    publisher_id: list[int] | None = None
    series_id: list[int] | None = None
    user_tag_id: list[int] | None = None
    has_files: bool | None = None
    has_subtitle: bool | None = None
    vr: bool | None = None
    mosaic: Mosaic | None = None
    uncensored: bool | None = None
    definition: str | None = None
    content_type: ContentType | None = None
    saved_query_id: int | None = None

    @property
    def active(self) -> bool:
        """任一项非空才需要查候选 id; 全空时语义检索直接扫整个索引."""
        return any(
            (
                self.actor_id,
                self.director_id,
                self.tag_id,
                self.studio_id,
                self.publisher_id,
                self.series_id,
                self.user_tag_id,
                self.has_files is not None,
                self.has_subtitle is not None,
                self.vr is not None,
                self.mosaic is not None,
                self.uncensored is not None,
                self.definition is not None,
                self.content_type is not None,
                self.saved_query_id is not None,
            )
        )


async def parse_metadata_filters(
    actor_id: Annotated[list[int] | None, Query(description="Filter by actor facet id(s); AND")] = None,
    director_id: Annotated[list[int] | None, Query(description="Filter by director facet id(s); AND")] = None,
    tag_id: Annotated[list[int] | None, Query(description="Filter by scraped tag facet id(s); AND")] = None,
    studio_id: Annotated[list[int] | None, Query(description="Filter by studio facet id(s); OR")] = None,
    publisher_id: Annotated[list[int] | None, Query(description="Filter by publisher facet id(s); OR")] = None,
    series_id: Annotated[list[int] | None, Query(description="Filter by series facet id(s); OR")] = None,
    user_tag_id: Annotated[list[int] | None, Query(description="Filter by user tag id(s); AND")] = None,
    has_files: Annotated[bool | None, Query(description="Filter by presence of linked MediaFile(s)")] = None,
    has_subtitle: Annotated[bool | None, Query(description="Filter by linked file subtitle marker")] = None,
    vr: Annotated[bool | None, Query(description="Filter by VR marker (entry tag vocabulary)")] = None,
    mosaic: Annotated[Mosaic | None, Query(description="Filter by linked file mosaic marker")] = None,
    uncensored: Annotated[
        bool | None, Query(description="Filter by uncensored number (own content type or linked file marker)")
    ] = None,
    definition: Annotated[str | None, Query(description="Filter by linked file definition (8K/4K/1080p/…)")] = None,
    content_type: Annotated[ContentType | None, Query(description="Filter by number content type")] = None,
    saved_query_id: Annotated[
        int | None, Query(description="Saved query preset id; AND with other filters via SQL subquery")
    ] = None,
) -> MetadataFilters:
    return MetadataFilters(
        actor_id=actor_id,
        director_id=director_id,
        tag_id=tag_id,
        studio_id=studio_id,
        publisher_id=publisher_id,
        series_id=series_id,
        user_tag_id=user_tag_id,
        has_files=has_files,
        has_subtitle=has_subtitle,
        vr=vr,
        mosaic=mosaic,
        uncensored=uncensored,
        definition=definition,
        content_type=content_type,
        saved_query_id=saved_query_id,
    )


def require_known_definition(definition: str | None) -> str | None:
    if definition is None:
        return None
    if definition not in DEFINITION_VALUES:
        raise HTTPException(status_code=422, detail=f"未知分辨率: {definition}")
    return definition
