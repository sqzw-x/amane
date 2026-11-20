from collections.abc import Callable

from sqlalchemy import String, and_, false, func, not_, or_, select
from sqlalchemy import cast as sql_cast
from sqlalchemy.orm import Mapped
from sqlalchemy.sql.elements import ColumnElement
from sqlmodel import col

from ...db.models import (
    Actor,
    ActorAlias,
    ActorUserTag,
    MediaFile,
    Metadata,
    MetadataActor,
    MetadataDirector,
    MetadataTag,
    MetadataUserTag,
    Publisher,
    Series,
    Studio,
    has_subtitle_predicate,
    metadata_vr_predicate,
)
from ...enums import ActorGender
from ...parsing import ContentType, Mosaic
from ..models.filter_expression import FilterNode, FilterRule


def compile_filter_tree(
    expression: FilterNode | None,
    leaf_compiler: Callable[[FilterRule], ColumnElement[bool]],
) -> ColumnElement[bool] | None:
    if expression is None:
        return None

    def compile_node(node: FilterNode) -> ColumnElement[bool]:
        if isinstance(node, FilterRule):
            return func.coalesce(leaf_compiler(node), false())
        children = [compile_node(child) for child in node.children]
        combined = and_(*children) if node.op == "and" else or_(*children)
        if node.negate:
            combined = not_(combined)
        return func.coalesce(combined, false())

    return compile_node(expression)


def _string_value(rule: FilterRule) -> str:
    if not isinstance(rule.value, str):
        raise AssertionError("validated filter value must be a string")
    return rule.value


def _integer_value(rule: FilterRule) -> int:
    if type(rule.value) is not int:
        raise AssertionError("validated filter value must be an integer")
    return rule.value


def _boolean_value(rule: FilterRule) -> bool:
    if type(rule.value) is not bool:
        raise AssertionError("validated filter value must be a boolean")
    return rule.value


def _contains(column: Mapped[str] | Mapped[str | None], value: str) -> ColumnElement[bool]:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return column.ilike(f"%{escaped}%", escape="\\")


def _uncensored_condition(
    is_uncensored_type: ColumnElement[bool], has_uncensored_file: ColumnElement[bool]
) -> ColumnElement[bool]:
    return func.coalesce(or_(is_uncensored_type, has_uncensored_file), false())


def metadata_filter_leaf(rule: FilterRule) -> ColumnElement[bool]:
    if rule.field == "actor_id":
        return (
            select(col(MetadataActor.metadata_id))
            .where(
                col(MetadataActor.metadata_id) == col(Metadata.id), col(MetadataActor.actor_id) == _integer_value(rule)
            )
            .exists()
        )
    if rule.field == "director_id":
        return (
            select(col(MetadataDirector.metadata_id))
            .where(
                col(MetadataDirector.metadata_id) == col(Metadata.id),
                col(MetadataDirector.director_id) == _integer_value(rule),
            )
            .exists()
        )
    if rule.field == "tag_id":
        return (
            select(col(MetadataTag.metadata_id))
            .where(col(MetadataTag.metadata_id) == col(Metadata.id), col(MetadataTag.tag_id) == _integer_value(rule))
            .exists()
        )
    if rule.field == "user_tag_id":
        return (
            select(col(MetadataUserTag.metadata_id))
            .where(
                col(MetadataUserTag.metadata_id) == col(Metadata.id),
                col(MetadataUserTag.user_tag_id) == _integer_value(rule),
            )
            .exists()
        )
    if rule.field == "studio_id":
        return (
            col(Metadata.studio)
            == select(col(Studio.name)).where(col(Studio.id) == _integer_value(rule)).scalar_subquery()
        )
    if rule.field == "publisher_id":
        return (
            col(Metadata.publisher)
            == select(col(Publisher.name)).where(col(Publisher.id) == _integer_value(rule)).scalar_subquery()
        )
    if rule.field == "series_id":
        return (
            col(Metadata.series)
            == select(col(Series.name)).where(col(Series.id) == _integer_value(rule)).scalar_subquery()
        )
    if rule.field == "library_id":
        return (
            select(col(MediaFile.id))
            .where(col(MediaFile.metadata_id) == col(Metadata.id), col(MediaFile.library_id) == _integer_value(rule))
            .exists()
        )
    if rule.field == "has_files":
        file_exists = select(col(MediaFile.id)).where(col(MediaFile.metadata_id) == col(Metadata.id)).exists()
        return file_exists if _boolean_value(rule) else not_(file_exists)
    if rule.field == "has_subtitle":
        subtitle_exists = (
            select(col(MediaFile.id))
            .where(col(MediaFile.metadata_id) == col(Metadata.id), has_subtitle_predicate())
            .exists()
        )
        return subtitle_exists if _boolean_value(rule) else not_(subtitle_exists)
    if rule.field == "vr":
        predicate = metadata_vr_predicate()
        return predicate if _boolean_value(rule) else not_(predicate)
    if rule.field == "uncensored":
        uncensored = _uncensored_condition(
            col(Metadata.content_type) == ContentType.UNCENSORED,
            select(col(MediaFile.id))
            .where(col(MediaFile.metadata_id) == col(Metadata.id), col(MediaFile.mosaic) == Mosaic.UNCENSORED)
            .exists(),
        )
        return uncensored if _boolean_value(rule) else not_(uncensored)
    if rule.field == "mosaic":
        return (
            select(col(MediaFile.id))
            .where(col(MediaFile.metadata_id) == col(Metadata.id), col(MediaFile.mosaic) == Mosaic(_string_value(rule)))
            .exists()
        )
    if rule.field == "definition":
        return (
            select(col(MediaFile.id))
            .where(col(MediaFile.metadata_id) == col(Metadata.id), col(MediaFile.definition) == _string_value(rule))
            .exists()
        )
    if rule.field == "content_type":
        return col(Metadata.content_type) == ContentType(_string_value(rule))
    if rule.field == "number":
        return _contains(col(Metadata.number), _string_value(rule))
    if rule.field == "title":
        return _contains(col(Metadata.title), _string_value(rule))
    raise AssertionError(f"unsupported metadata filter field: {rule.field}")


def _actor_has_person() -> ColumnElement[bool]:
    return or_(
        and_(col(Actor.gender).is_not(None), col(Actor.gender) != ActorGender.UNKNOWN),
        and_(col(Actor.birthday).is_not(None), col(Actor.birthday) != ""),
        and_(col(Actor.birthplace).is_not(None), col(Actor.birthplace) != ""),
        col(Actor.height).is_not(None),
        col(Actor.bust).is_not(None),
        col(Actor.waist).is_not(None),
        col(Actor.hip).is_not(None),
        and_(col(Actor.cup).is_not(None), col(Actor.cup) != ""),
        and_(col(Actor.overview).is_not(None), col(Actor.overview) != ""),
        and_(col(Actor.tagline).is_not(None), col(Actor.tagline) != ""),
    )


def _actor_has_image() -> ColumnElement[bool]:
    return func.json_array_length(col(Actor.image_urls)) > 0


def actor_filter_leaf(rule: FilterRule) -> ColumnElement[bool]:
    if rule.field == "gender":
        return col(Actor.gender) == ActorGender(_string_value(rule))
    if rule.field == "user_tag_id":
        return (
            select(col(ActorUserTag.actor_id))
            .where(col(ActorUserTag.actor_id) == col(Actor.id), col(ActorUserTag.user_tag_id) == _integer_value(rule))
            .exists()
        )
    if rule.field == "has_person":
        predicate = _actor_has_person()
        return predicate if _boolean_value(rule) else not_(predicate)
    if rule.field == "has_image":
        if _boolean_value(rule):
            return _actor_has_image()
        return or_(
            col(Actor.image_urls).is_(None),
            func.json_array_length(col(Actor.image_urls)) == 0,
            sql_cast(col(Actor.image_urls), String) == "[]",
        )
    if rule.field == "birthday":
        value = _string_value(rule)
        return col(Actor.birthday) >= value if rule.operator == "gte" else col(Actor.birthday) <= value
    if rule.field in {"height", "bust", "waist", "hip"}:
        value = _integer_value(rule)
        column = {
            "height": col(Actor.height),
            "bust": col(Actor.bust),
            "waist": col(Actor.waist),
            "hip": col(Actor.hip),
        }[rule.field]
        return column >= value if rule.operator == "gte" else column <= value
    if rule.field == "cup":
        value = func.upper(col(Actor.cup))
        target = _string_value(rule)
        present = and_(col(Actor.cup).is_not(None), value != "")
        comparison = value >= target if rule.operator == "gte" else value <= target
        return and_(present, comparison)
    if rule.field == "name":
        return _contains(col(Actor.name), _string_value(rule))
    if rule.field == "alias":
        return (
            select(col(ActorAlias.id))
            .where(col(ActorAlias.actor_id) == col(Actor.id), _contains(col(ActorAlias.name), _string_value(rule)))
            .exists()
        )
    if rule.field == "birthplace":
        return _contains(col(Actor.birthplace), _string_value(rule))
    raise AssertionError(f"unsupported actor filter field: {rule.field}")


def compile_metadata_filter(expression: FilterNode | None) -> ColumnElement[bool] | None:
    return compile_filter_tree(expression, metadata_filter_leaf)


def compile_actor_filter(expression: FilterNode | None) -> ColumnElement[bool] | None:
    return compile_filter_tree(expression, actor_filter_leaf)
