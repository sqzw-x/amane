"""正文里内嵌思维链的切分."""

from __future__ import annotations

import pytest

from amane.agent.reasoning import EmbeddedThinking


def _run(chunks: list[str]) -> list[tuple[bool, str, int]]:
    """推进所有增量并收尾. 同一段在真流程里按 `block_id` 归并, 这里逐次比对."""
    thinking = EmbeddedThinking()
    out: list[tuple[bool, str, int]] = []
    for chunk in chunks:
        out.extend(thinking.feed(chunk))
    out.extend(thinking.close())
    return out


@pytest.mark.parametrize(
    ("chunks", "expected"),
    [
        # 没有标签: 增量原样透传, 归在同一段
        (["a", "b"], [(False, "a", 0), (False, "b", 0)]),
        # 标签被增量切开: 尾巴扣住不发出, 拼回后整段改道
        (["前 <thi", "nk>想法</th", "ink> 后"], [(False, "前 ", 0), (True, "想法", 1), (False, " 后", 2)]),
        # 思维链没写收尾 (被截断) 时, 余下内容仍算思考, 不能漏进正文
        (["正文<think>未收尾"], [(False, "正文", 0), (True, "未收尾", 1)]),
        # 只有一对空标签: 标签两侧的正文分属两段, 空段不产生行
        (["<think></think>正文"], [(False, "正文", 2)]),
        # 连续两段思维链: 中间的空正文不产生空行
        (["<think>甲</think><think>乙</think>尾巴"], [(True, "甲", 1), (True, "乙", 3), (False, "尾巴", 4)]),
    ],
)
def test_split(chunks: list[str], expected: list[tuple[bool, str, int]]) -> None:
    assert _run(chunks) == expected


def test_untagged_text_stays_one_segment() -> None:
    """没有标签时整段正文仍归第 0 段: 页面上的气泡与用量条位置都不因本模块改变."""
    assert _run(["α", "β", "γ"]) == [(False, "α", 0), (False, "β", 0), (False, "γ", 0)]
