"""人物字段读写与实体 merge. 别名不在此层, 由 ``repos.facet_helpers`` 的行写入函数处理."""

from ..aggregate.actor import AggregatedActor, merge_actor_rows_fill_empty
from ..enums import ActorField
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


def locked_fields_of(actor: Actor) -> set[ActorField]:
    """人物锁集合; 非法存量值忽略."""
    locked: set[ActorField] = set()
    for name in actor.locked_fields or []:
        try:
            locked.add(ActorField(name))
        except ValueError:
            continue
    return locked


def filter_locked_person_data(
    data: AggregatedActor, *, locked: set[ActorField], current: AggregatedActor
) -> AggregatedActor:
    """AUTO 写入: 锁定字段保留 ``current`` 的值与来源, 其余取 ``data``.

    别名不进 ``AggregatedActor`` 回填: ``current`` 由 ``actor_to_aggregated`` 得到, 不含别名,
    回填即清空; 其锁在别名行写入点判定.
    """
    if not locked:
        return data
    merged = data.model_copy(deep=True)
    for field in locked:
        if field is ActorField.ALIASES:
            continue
        setattr(merged, field, getattr(current, field))
    merged.field_sources = {
        **{key: value for key, value in current.field_sources.items() if key in locked},
        **{key: value for key, value in data.field_sources.items() if key not in locked},
    }
    return merged


def merge_person_fields_into_target(target: Actor, sources: list[Actor]) -> None:
    """源人物字段填空并入 target (须在删源前调用). 别名并入由 ``move_actor_alias_rows`` 处理.
    源的锁集并入 target, 使被保护的值/空位在合并后仍受保护.
    """
    merged = actor_to_aggregated(target)
    for src in sources:
        merged = merge_actor_rows_fill_empty(merged, actor_to_aggregated(src))
    apply_aggregated_to_actor(target, merged)
    locked = locked_fields_of(target)
    for src in sources:
        locked |= locked_fields_of(src)
    target.locked_fields = [str(field) for field in ActorField if field in locked]
