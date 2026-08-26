"""Actor 人物字段读写与实体 merge 辅助.

别名不在此层: 以 ``ActorAlias`` 行存储, 读写走 ``repos/facet_helpers`` 的行写入函数
(``add_actor_aliases`` / ``replace_actor_aliases`` / ``move_actor_alias_rows``).
"""

from amane.aggregate.actor import AggregatedActor, merge_actor_rows_fill_empty
from amane.db.models import Actor
from amane.enums import ActorGender


def actor_to_aggregated(actor: Actor) -> AggregatedActor:
    """DB Actor 行 → AggregatedActor (merge / 回写共用); 不含别名 (见模块说明)."""
    gender = actor.gender if isinstance(actor.gender, ActorGender) else ActorGender(actor.gender)
    return AggregatedActor(
        gender=gender,
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
    """将聚合结果写回 Actor 行 (不改 name/id); 别名由调用方经别名行写入."""
    actor.gender = data.gender
    actor.birthday = data.birthday
    actor.birthplace = data.birthplace
    actor.height = data.height
    actor.bust = data.bust
    actor.waist = data.waist
    actor.hip = data.hip
    actor.cup = data.cup
    actor.overview = data.overview
    actor.tagline = data.tagline
    actor.image_urls = list(data.image_urls)
    actor.provider_ids = dict(data.provider_ids)
    actor.source_urls = dict(data.source_urls)
    actor.field_sources = dict(data.field_sources)
    actor.raw = dict(data.raw)


def merge_person_fields_into_target(target: Actor, sources: list[Actor]) -> None:
    """实体 merge: 源人物字段填空并入 target (删源前调用); 别名并行走
    ``move_actor_alias_rows``, 不在此层."""
    merged = actor_to_aggregated(target)
    for src in sources:
        merged = merge_actor_rows_fill_empty(merged, actor_to_aggregated(src))
    apply_aggregated_to_actor(target, merged)
