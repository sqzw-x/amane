"""人物字段读写与实体 merge. 别名不在此层, 由 ``repos.facet_helpers`` 的行写入函数处理."""

from ..aggregate.actor import AggregatedActor, merge_actor_rows_fill_empty
from ..utils.text import normalize_long_text
from .models import Actor


def actor_to_aggregated(actor: Actor) -> AggregatedActor:
    """不含别名."""
    return AggregatedActor(
        gender=actor.gender,
        birthday=actor.birthday,
        birthplace=actor.birthplace,
        height=actor.height,
        bust=actor.bust,
        waist=actor.waist,
        hip=actor.hip,
        cup=actor.cup,
        overview=actor.overview,
        tagline=actor.tagline,
        image_urls=list(actor.image_urls or []),
        provider_ids=dict(actor.provider_ids or {}),
        source_urls=dict(actor.source_urls or {}),
        field_sources=dict(actor.field_sources or {}),
        raw=dict(actor.raw or {}),
    )


def apply_aggregated_to_actor(actor: Actor, data: AggregatedActor) -> None:
    """不修改 name/id; 别名由调用方经别名行写入."""
    actor.gender = data.gender
    actor.birthday = data.birthday
    actor.birthplace = data.birthplace
    actor.height = data.height
    actor.bust = data.bust
    actor.waist = data.waist
    actor.hip = data.hip
    actor.cup = data.cup
    actor.overview = normalize_long_text(data.overview)
    actor.tagline = data.tagline
    actor.image_urls = list(data.image_urls)
    actor.provider_ids = dict(data.provider_ids)
    actor.source_urls = dict(data.source_urls)
    actor.field_sources = dict(data.field_sources)
    actor.raw = dict(data.raw)


def merge_person_fields_into_target(target: Actor, sources: list[Actor]) -> None:
    """源人物字段填空并入 target (须在删源前调用). 别名并入由 ``move_actor_alias_rows`` 处理."""
    merged = actor_to_aggregated(target)
    for src in sources:
        merged = merge_actor_rows_fill_empty(merged, actor_to_aggregated(src))
    apply_aggregated_to_actor(target, merged)
