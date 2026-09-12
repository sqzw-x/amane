"""Assemble PlaybackQuery from repository rows."""

from __future__ import annotations

from ..db.models import MediaFile, Metadata
from ..plugins.api import PlaybackMediaFile, PlaybackQuery


def playback_query(
    metadata: Metadata,
    files: list[MediaFile],
    *,
    selected_file_id: int | None = None,
) -> PlaybackQuery:
    if metadata.id is None:
        raise ValueError("metadata id is required")
    snapshots: list[PlaybackMediaFile] = []
    for item in files:
        if item.id is None:
            continue
        snapshots.append(
            PlaybackMediaFile(
                id=item.id,
                path=item.path,
                size=item.size,
                content_type=item.content_type,
                mosaic=item.mosaic,
                has_subtitle=item.has_subtitle,
                definition=item.definition,
                library_id=item.library_id,
            )
        )
    return PlaybackQuery(
        metadata_id=metadata.id,
        number=metadata.number,
        title=metadata.title,
        actors=list(metadata.actors),
        studio=metadata.studio,
        publisher=metadata.publisher,
        release=metadata.release,
        runtime=metadata.runtime,
        tags=list(metadata.tags),
        series=metadata.series,
        plot=metadata.plot,
        directors=list(metadata.directors),
        source_urls=dict(metadata.source_urls),
        external_ids=dict(metadata.external_ids),
        files=tuple(snapshots),
        selected_file_id=selected_file_id,
    )
