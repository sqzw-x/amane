"""wikipedia 演员爬虫的名字校验: 实体标签 / 别名与查找名相等才算命中."""

from typing import Any

import pytest

from amane.crawlers.actor.sites.wikipedia import _name_matches


def _entity(labels: dict[str, str], aliases: dict[str, list[str]] | None = None) -> dict[str, Any]:
    return {
        "labels": {lang: {"value": value} for lang, value in labels.items()},
        "aliases": {lang: [{"value": v} for v in values] for lang, values in (aliases or {}).items()},
    }


@pytest.mark.parametrize(
    ("entity", "name", "expected"),
    [
        # 任一语言的标签相等即命中, 大小写无关
        (_entity({"ja": "あいだゆあ", "en": "Yua Aida"}), "Yua Aida", True),
        (_entity({"ja": "あいだゆあ"}), "あいだゆあ", True),
        (_entity({"en": "Aida Yua"}), "aida yua", True),
        # 别名相等同样命中 (别名按语言分组, 与标签形状不同)
        (_entity({"en": "Aida Yua"}, {"zh": ["爱田友"]}), "爱田友", True),
        # 只在描述 / 职业上命中 AV 关键词的实体不算命中
        (_entity({"ja": "あいだゆあ", "zh": "愛田由"}, {"zh": ["あいだゆあ"]}), "あい", False),
        # 空查找名与缺结构不命中
        (_entity({"ja": "あいだゆあ"}), "  ", False),
        ({}, "あいだゆあ", False),
    ],
)
def test_name_matches(entity: dict[str, Any], name: str, expected: bool) -> None:
    assert _name_matches(entity, name) is expected
