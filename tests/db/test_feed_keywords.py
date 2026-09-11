import pytest

from amane.db.feed_keywords import item_matches_ignore_keywords, normalize_ignore_keywords


def test_normalize_ignore_keywords_table() -> None:
    cases: list[tuple[list[str] | None, list[str] | type[ValueError]]] = [
        (None, []),
        ([], []),
        (["  ", "\n"], []),
        (["合集", " 合集 ", "総集編"], ["合集", "総集編"]),
        (["Best", "best", "BEST"], ["Best"]),
        (["a" * 64], ["a" * 64]),
    ]
    for raw, expected in cases:
        assert normalize_ignore_keywords(raw) == expected

    with pytest.raises(ValueError, match="最长"):
        normalize_ignore_keywords(["a" * 65])
    with pytest.raises(ValueError, match="最多"):
        normalize_ignore_keywords([str(i) for i in range(51)])


def test_item_matches_ignore_keywords_table() -> None:
    cases: list[tuple[list[str], dict[str, str | None], bool]] = [
        ([], {"title": "合集 123"}, False),
        (["合集"], {"title": "超豪华合集 VOL.1"}, True),
        (["合集"], {"title": "Regular title"}, False),
        (["best"], {"title": "The BEST of 2024"}, True),
        (["midv"], {"number": "MIDV-123"}, True),
        (["cover"], {"description": "<p>Cover <b>shot</b></p>"}, True),
        (["g-key"], {"item_key": "g-key"}, True),
        (["合集"], {"title": None, "number": None, "description": None, "item_key": None}, False),
    ]
    for keywords, fields, expected in cases:
        assert (
            item_matches_ignore_keywords(
                keywords,
                title=fields.get("title"),
                number=fields.get("number"),
                description=fields.get("description"),
                item_key=fields.get("item_key"),
            )
            is expected
        )
