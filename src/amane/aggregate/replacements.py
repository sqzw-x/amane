"""Apply configured replacements to newly generated integrated metadata."""

import re
from collections.abc import Sequence

from ..config.manager import FieldReplacementRule
from ..crawlers.models import FilmActor
from ..enums import MetadataField
from .models import AggregatedMetadata


def replace_text(field: str, value: str, rules: Sequence[FieldReplacementRule]) -> str:
    for rule in rules:
        if rule.field != field:
            continue
        if rule.find.startswith("re:"):
            value = re.sub(rule.find.removeprefix("re:"), rule.replace, value)
        else:
            value = value.replace(rule.find, rule.replace)
    return value


def replace_text_list(field: str, values: Sequence[str], rules: Sequence[FieldReplacementRule]) -> list[str]:
    if not any(rule.field == field for rule in rules):
        return list(values)
    replaced = (replace_text(field, value, rules) for value in values)
    return list(dict.fromkeys(value for value in replaced if value))


def apply_metadata_replacements(metadata: AggregatedMetadata, rules: Sequence[FieldReplacementRule]) -> None:
    if metadata.title is not None:
        metadata.title = replace_text(MetadataField.TITLE, metadata.title, rules)
    if metadata.studio is not None:
        metadata.studio = replace_text(MetadataField.STUDIO, metadata.studio, rules)
    if metadata.publisher is not None:
        metadata.publisher = replace_text(MetadataField.PUBLISHER, metadata.publisher, rules)
    if metadata.series is not None:
        metadata.series = replace_text(MetadataField.SERIES, metadata.series, rules)
    if metadata.plot is not None:
        metadata.plot = replace_text(MetadataField.PLOT, metadata.plot, rules)

    metadata.actors = _replace_actors(metadata.actors, rules)
    metadata.tags = replace_text_list(MetadataField.TAGS, metadata.tags, rules)
    metadata.directors = replace_text_list(MetadataField.DIRECTORS, metadata.directors, rules)


def _replace_actors(actors: Sequence[FilmActor], rules: Sequence[FieldReplacementRule]) -> list[FilmActor]:
    if not any(rule.field == MetadataField.ACTORS for rule in rules):
        return list(actors)
    result: list[FilmActor] = []
    seen: set[str] = set()
    for actor in actors:
        name = replace_text(MetadataField.ACTORS, actor.name, rules)
        if name and name not in seen:
            seen.add(name)
            result.append(actor.model_copy(update={"name": name}))
    return result
