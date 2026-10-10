"""strip_actor_names 表测试."""

from __future__ import annotations

import pytest

from amane.utils.titles import strip_actor_names

CASES: list[tuple[str | None, list[str], str | None]] = [
    # 无标题 / 无候选名: 原样返回
    (None, ["似鳥"], None),
    ("", ["似鳥"], ""),
    ("作品名", [], "作品名"),
    ("作品名", ["似鳥"], "作品名"),
    # 全名子串命中
    ("似鳥[似鳥] ナースの告白", ["似鳥"], "ナースの告白"),
    ("MIDV-123 似鳥 the best", ["似鳥"], "MIDV-123 the best"),
    ("似鳥 - 作品", ["似鳥"], "作品"),
    ("作品 - 似鳥", ["似鳥"], "作品"),
    ("似鳥   作品", ["似鳥"], "作品"),
    # 括号注记: 剔名后残留的空壳整对删除
    ("【似鳥】作品", ["似鳥"], "作品"),
    ("（似鳥）作品", ["似鳥"], "作品"),
    ("作品（似鳥）", ["似鳥"], "作品"),
    ("[ 似鳥 ] 作品", ["似鳥"], "作品"),
    ("(  ) 似鳥 作品", ["似鳥"], "作品"),
    # 多名字: 长名优先, 短名是其前缀时不产生残留
    ("似鳥花 作品", ["似鳥", "似鳥花"], "作品"),
    ("似鳥 花井 作品", ["似鳥", "花井"], "作品"),
    # 短名与非法候选不参与
    ("愛の作品", ["愛"], "愛の作品"),
    ("作品", [" "], "作品"),
    ("作品", [""], "作品"),
    # 子串语义: CJK 无词边界, 更长词里的同名片段会被命中
    ("似鳥子の日常", ["似鳥"], "子の日常"),
    # ASCII 名字加字母数字边界, 大小写不敏感
    ("AIKA の作品", ["aika"], "の作品"),
    ("AIKA-123", ["aika"], "123"),
    ("AIKATSU-001", ["AIKA"], "AIKATSU-001"),
    # 结果为空时保留原标题
    ("似鳥", ["似鳥"], "似鳥"),
    ("（似鳥）", ["似鳥"], "（似鳥）"),
    # 孤立括号不是成对注记, 只清理首尾分隔符
    ("作品 [似鳥", ["似鳥"], "作品 ["),
    # 首尾的分隔符一并剥除 (含与名字无关的装饰性连字符)
    ("-作品- 似鳥", ["似鳥"], "作品"),
    # 未命中时不做任何清理
    ("(  ) 作品", ["似鳥"], "(  ) 作品"),
]


@pytest.mark.parametrize(("title", "names", "expected"), CASES)
def test_strip_actor_names(title: str | None, names: list[str], expected: str | None) -> None:
    assert strip_actor_names(title, names) == expected


@pytest.mark.parametrize(("title", "names", "_expected"), CASES)
def test_strip_actor_names_is_idempotent(title: str | None, names: list[str], _expected: str | None) -> None:
    """重刮时同一标题会再次经过本步骤; 二次调用必须不再变化."""
    once = strip_actor_names(title, names)
    assert strip_actor_names(once, names) == once
