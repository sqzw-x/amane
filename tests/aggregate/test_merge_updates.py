"""compute_merge_updates: 按 selections 从 raw 拼 update_metadata 的 dict."""

import pytest

from amane.aggregate import compute_merge_updates

_IMG_RAW: dict[str, dict[str, object]] = {
    "javdb": {
        "poster_urls": ["https://j/p.jpg"],
        "thumb_urls": ["https://j/t.jpg"],
        "extrafanart": ["https://j/e1.jpg", "https://j/e2.jpg"],
    },
    "dmm": {
        "poster_urls": ["https://dmm/p.jpg"],
        "thumb_urls": ["https://dmm/t.jpg"],
        "extrafanart": ["https://d/e.jpg"],
    },
}


@pytest.mark.parametrize(
    ("raw", "field_sources", "selections", "expect"),
    [
        (
            {"javdb": {"title": "javdb title"}, "dmm": {"title": "dmm title"}},
            {"title": "javdb"},
            {"title": "dmm"},
            {"title": "dmm title", "field_sources": {"title": "dmm"}},
        ),
        (
            {"javdb": {"score": 85.0}},
            {},
            {"score": "javdb"},
            {"scores": {"javdb": 85.0}},
        ),
        (
            {"javdb": {"title": None}},
            {"title": "javdb"},
            {"title": "javdb"},
            {},
        ),
        (
            {},
            {},
            {},
            {},
        ),
        (
            {
                "javdb": {"title": "javdb title", "studio": "javdb studio"},
                "dmm": {"title": "dmm title"},
            },
            {"title": "javdb", "studio": "javdb"},
            {"title": "dmm"},
            {"title": "dmm title", "field_sources": {"title": "dmm", "studio": "javdb"}},
        ),
        (
            _IMG_RAW,
            {},
            {"poster_urls": "dmm"},
            {"poster_urls": ["https://dmm/p.jpg"]},
        ),
        (
            _IMG_RAW,
            {},
            {"extrafanart": "javdb"},
            {"extrafanart_urls": {"javdb": ["https://j/e1.jpg", "https://j/e2.jpg"]}},
        ),
        (
            _IMG_RAW,
            {},
            {"thumb_urls": "javdb", "extrafanart": "dmm"},
            {
                "thumb_urls": ["https://j/t.jpg"],
                "extrafanart_urls": {"dmm": ["https://d/e.jpg"]},
            },
        ),
        (
            {
                "javdb": {
                    "actors": [
                        {"name": "Mei", "gender": "female"},
                        {"name": "MaleA", "gender": "male"},
                    ]
                }
            },
            {},
            {"actors": "javdb"},
            {"actors": ["Mei", "MaleA"], "field_sources": {"actors": "javdb"}},
        ),
        (
            {"javdb": {"actors": ["Mei", "MaleA"]}},
            {},
            {"actors": "javdb"},
            {"actors": ["Mei", "MaleA"], "field_sources": {"actors": "javdb"}},
        ),
    ],
    ids=[
        "scalar_title",
        "score_to_scores",
        "none_skipped",
        "empty_selections",
        "preserves_other_field_sources",
        "poster_urls",
        "extrafanart",
        "thumb_and_extrafanart",
        "actors_film_actor_objects",
        "actors_string_list",
    ],
)
def test_compute_merge_updates(
    raw: dict[str, dict[str, object]],
    field_sources: dict[str, str],
    selections: dict[str, str],
    expect: dict[str, object],
) -> None:
    assert compute_merge_updates(raw, field_sources, selections) == expect


@pytest.mark.parametrize(
    ("raw", "selections", "match"),
    [
        ({}, {"title": "nonexistent"}, "source 'nonexistent'"),
        ({"javdb": {"title": "x"}}, {"plot": "javdb"}, "field 'plot'"),
    ],
    ids=["unknown_source", "unknown_field"],
)
def test_compute_merge_updates_rejects(
    raw: dict[str, dict[str, object]], selections: dict[str, str], match: str
) -> None:
    with pytest.raises(ValueError, match=match):
        compute_merge_updates(raw, {}, selections)
