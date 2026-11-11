"""关联筛选的聚合子查询: 同 kind 的多个 id 取 AND, 覆盖未知 / 重复 / 跨 kind."""

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, TypedDict

import pytest

from amane.db.models import FacetKind

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
    """构造覆盖多演员 / 多导演 / 多标签 / 多用户标签的行."""
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
async def test_facet_filters(repo: Repository, filters: dict[str, list[str | int]], expected_numbers: set[str]) -> None:
    """同 kind 的多个 id 必须全部命中 (AND); 未知 id 使结果为空, 重复 id 不影响结果."""
    facets = await _seed(repo)
    resolved = _resolve(filters, facets)

    items, total = await repo.list_metadata(limit=100, **resolved)

    assert total == len(items)
    assert {item.number for item in items} == expected_numbers
