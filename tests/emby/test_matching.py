"""人物名归一与匹配: 归一后精确相等才算命中."""

import pytest

from amane.emby import EmbyPerson, match_persons, normalize_person_name


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Alice", "alice"),
        ("  Alice  ", "alice"),
        ("希崎 ジェシカ", "希崎ジェシカ"),
        ("希崎　ジェシカ", "希崎ジェシカ"),  # 全角空格
        ("Ａｌｉｃｅ", "alice"),  # 全角字母
        ("ｱﾘｽ", "アリス"),  # 半角片假名
        ("", ""),
    ],
)
def test_normalize_person_name(raw: str, expected: str):
    assert normalize_person_name(raw) == expected


def _person(person_id: str, name: str) -> EmbyPerson:
    return EmbyPerson(id=person_id, name=name, has_primary_image=False)


class TestMatchPersons:
    def test_hits_exact_after_normalize(self):
        persons = [_person("1", "希崎 ジェシカ"), _person("2", "别人")]
        assert [p.id for p in match_persons(["希崎ジェシカ"], persons)] == ["1"]

    def test_alias_list_matches_any_candidate(self):
        persons = [_person("1", "ありす"), _person("2", "Alice")]
        assert [p.id for p in match_persons(["Alice", "ありす"], persons)] == ["1", "2"]

    def test_similar_name_is_not_matched(self):
        """归一后不等即不命中: 少一个字、加后缀都不算."""
        persons = [_person("1", "希崎ジェシカ子"), _person("2", "希崎")]
        assert match_persons(["希崎ジェシカ"], persons) == []

    def test_deduplicates_by_id_and_keeps_order(self):
        persons = [_person("1", "Alice"), _person("1", "Alice"), _person("2", "ありす")]
        assert [p.id for p in match_persons(["Alice", "ありす"], persons)] == ["1", "2"]

    @pytest.mark.parametrize("names", [[], [""], ["   "]])
    def test_blank_names_match_nothing(self, names: list[str]):
        assert match_persons(names, [_person("1", "Alice")]) == []
