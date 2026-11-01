import pytest

from scripts import sync_zhcdict as sync


def _php(tables: dict[str, list[tuple[str, str]]]) -> str:
    blocks = "\n".join(
        f"\tpublic const {name} = [\n" + "\n".join(f"\t\t'{a}' => '{b}'," for a, b in pairs) + "\n\t];"
        for name, pairs in tables.items()
    )
    return f"<?php\nclass ZhConversion {{\n{blocks}\n}}\n"


def _tables(**extra: list[tuple[str, str]]) -> dict[str, list[tuple[str, str]]]:
    base = {name: [("甲", "乙")] for name in sync._BLOCKS}
    base.update(extra)
    return base


def test_parse_and_unescape(monkeypatch):
    monkeypatch.setattr(sync, "_MIN_ENTRIES", 1)
    php = _php(_tables(ZH_TO_HANS=[("it\\'s", "它"), ("\\\\", "\\\\")]))
    tables = sync.parse(php)
    assert set(tables) == set(sync._BLOCKS)
    assert tables["ZH_TO_HANS"] == {"it's": "它", "\\": "\\"}


def test_parse_rejects_missing_block(monkeypatch):
    monkeypatch.setattr(sync, "_MIN_ENTRIES", 1)
    php = _php({name: [("甲", "乙")] for name in list(sync._BLOCKS)[:4]})
    with pytest.raises(ValueError, match="上游常量"):
        sync.parse(php)


def test_parse_rejects_malformed_line(monkeypatch):
    monkeypatch.setattr(sync, "_MIN_ENTRIES", 1)
    php = _php(_tables(ZH_TO_HANT=[("甲", "乙")]))
    broken = php.replace("\t\t'甲' => '乙',", "\t\t'甲' => 乙,")
    with pytest.raises(ValueError, match="无法解析"):
        sync.parse(broken)


def test_parse_rejects_small_table(monkeypatch):
    monkeypatch.setattr(sync, "_MIN_ENTRIES", 2)
    php = _php(_tables())
    with pytest.raises(ValueError, match="条目过少"):
        sync.parse(php)


def _existing(**override: dict[str, str]) -> dict[str, object]:
    base: dict[str, object] = {name: {"旧": "旧"} for name in sync._BLOCKS.values()}
    base.update({"SIMPONLY": ["干"], "TRADONLY": ["幹"]})
    base.update(override)
    return base


def test_build_merge_keeps_old_and_prefers_upstream():
    existing = _existing()
    tables = {name: {"旧": "新", "上游": "值"} for name in sync._BLOCKS}
    merged = sync.build(existing, tables, policy="merge", allow_shrink=False)
    assert merged["zh2Hans"] == {"旧": "新", "上游": "值"}
    assert merged["SIMPONLY"] == ["干"]


def test_build_replace_rejects_shrink():
    existing = _existing(zh2Hant={"a": "b", "c": "d"})
    tables = {name: {"上游": "值"} for name in sync._BLOCKS}
    with pytest.raises(ValueError, match="allow-shrink"):
        sync.build(existing, tables, policy="replace", allow_shrink=False)


def test_build_replace_with_shrink_allowed():
    existing = _existing(zh2Hant={"a": "b", "c": "d"})
    tables = {name: {"上游": "值"} for name in sync._BLOCKS}
    merged = sync.build(existing, tables, policy="replace", allow_shrink=True)
    assert merged["zh2Hant"] == {"上游": "值"}
