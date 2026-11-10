"""关联筛选的聚合子查询: 与旧的逐 id AND 写法结果一致, 含未知 / 重复 / 跨 kind."""

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, TypedDict

import pytest
from sqlmodel import col, select

from amane.db.models import FacetKind, Metadata, MetadataActor, MetadataDirector, MetadataTag, MetadataUserTag
from amane.utils.collections import unique_ids

if TYPE_CHECKING:
    from amane.db.repository import Repository

_MOVIES: dict[str, tuple[list[str], list[str], list[str]]] = {
    "AGG-001": (["Alice", "Bob"], ["DirA", "DirB"], ["t1", "t2"]),
    "AGG-002": (["Alice"], ["DirA"], ["t1"]),
    "AGG-003": (["Bob", "Carol"], ["DirB"], ["t2"]),
    "AGG-004": ([], [], []),
}

_USER_TAGS = {
    "AGG-001": ["u1", "u2"],
    "AGG-002": ["u1"],
    "AGG-003": ["u2"],
}


class _FilterKwargs(TypedDict, total=False):
    actor_ids: list[int]
    director_ids: list[int]
    tag_ids: list[int]
    user_tag_ids: list[int]


async def _seed(repo: Repository) -> dict[str, dict[str, int]]:
    """构造覆盖多演员 / 多导演 / 多标签 / 多用户标签的行; 画像字段名 → id."""
    metadata_ids: dict[str, int] = {}
    for number, (actors, directors, tags) in _MOVIES.items():
        metadata = await repo.upsert_metadata(number=number, actors=actors, directors=directors, tags=tags)
        assert metadata.id is not None
        metadata_ids[number] = metadata.id

    user_tags, _ = await repo.ensure_user_tags(["u1", "u2"])
    user_tag_ids: dict[str, int] = {}
    for tag in user_tags:
        assert tag.id is not None
        user_tag_ids[tag.name] = tag.id
    for number, names in _USER_TAGS.items():
        await repo.apply_metadata_user_tags(
            [metadata_ids[number]], [user_tag_ids[name] for name in names], action="attach"
        )

    facets: dict[str, dict[str, int]] = {}
    for key, kind in (
        ("actors", FacetKind.ACTOR),
        ("directors", FacetKind.DIRECTOR),
        ("tags", FacetKind.TAG),
    ):
        items, _ = await repo.list_facets(kind, limit=100)
        facets[key] = {item.name: item.id for item in items}
    facets["user_tags"] = user_tag_ids
    return facets


def _resolve(filters: Mapping[str, Sequence[str | int]], facets: Mapping[str, Mapping[str, int]]) -> _FilterKwargs:
    """把用例里的名称换成 id; 整数按未知 id 原样传入."""
    keys = {
        "actors": "actor_ids",
        "directors": "director_ids",
        "tags": "tag_ids",
        "user_tags": "user_tag_ids",
    }
    resolved = _FilterKwargs()
    for source, target in keys.items():
        names = filters.get(source)
        if names is None:
            continue
        resolved[target] = [name if isinstance(name, int) else facets[source][name] for name in names]
    return resolved


async def _legacy_filter_ids(
    repo: Repository,
    *,
    actor_ids: Sequence[int] = (),
    director_ids: Sequence[int] = (),
    tag_ids: Sequence[int] = (),
    user_tag_ids: Sequence[int] = (),
) -> set[int]:
    """旧实现: 每个 id 各一条 IN 子查询并按 AND 连接."""
    async with repo._session() as session:
        stmt = select(col(Metadata.id))
        for link_metadata_id, facet_column, ids in (
            (MetadataActor.metadata_id, MetadataActor.actor_id, actor_ids),
            (MetadataDirector.metadata_id, MetadataDirector.director_id, director_ids),
            (MetadataTag.metadata_id, MetadataTag.tag_id, tag_ids),
            (MetadataUserTag.metadata_id, MetadataUserTag.user_tag_id, user_tag_ids),
        ):
            for facet_id in unique_ids(ids):
                stmt = stmt.where(col(Metadata.id).in_(select(link_metadata_id).where(facet_column == facet_id)))
        return {row for row in (await session.exec(stmt)).all() if row is not None}


CASES = [
    ("single-actor", {"actors": ["Alice"]}, {"AGG-001", "AGG-002"}),
    ("multi-actor-and", {"actors": ["Alice", "Bob"]}, {"AGG-001"}),
    ("multi-actor-empty-intersection", {"actors": ["Alice", "Carol"]}, set()),
    ("duplicate-id", {"actors": ["Alice", "Alice"]}, {"AGG-001", "AGG-002"}),
    ("unknown-id", {"actors": ["Alice", 999999]}, set()),
    ("director-and", {"directors": ["DirA", "DirB"]}, {"AGG-001"}),
    ("tag-and", {"tags": ["t1", "t2"]}, {"AGG-001"}),
    ("user-tag-and", {"user_tags": ["u1", "u2"]}, {"AGG-001"}),
    ("cross-kind-and", {"actors": ["Alice"], "tags": ["t1", "t2"]}, {"AGG-001"}),
    ("cross-kind-conflict", {"actors": ["Carol"], "directors": ["DirA"]}, set()),
]


@pytest.mark.asyncio(loop_scope="function")
@pytest.mark.parametrize(
    ("filters", "expected_numbers"),
    [(filters, expected) for _id, filters, expected in CASES],
    ids=[case_id for case_id, _filters, _expected in CASES],
)
async def test_aggregated_filters_match_legacy(
    repo: Repository, filters: dict[str, list[str | int]], expected_numbers: set[str]
) -> None:
    facets = await _seed(repo)
    resolved = _resolve(filters, facets)

    items, total = await repo.list_metadata(limit=100, **resolved)

    assert total == len(items)
    assert {item.number for item in items} == expected_numbers
    assert {item.id for item in items} == await _legacy_filter_ids(repo, **resolved)
