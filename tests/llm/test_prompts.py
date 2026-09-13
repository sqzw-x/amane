"""自定义翻译提示词的组装与配置校验.

覆盖: 拼接顺序与回退、目标语言占位符、逐字段覆盖的作用范围, 以及非法输入的拒绝.
内置文案在用例里复制一份: 文案改动应当在这里被看见.
"""

import pytest
from pydantic import ValidationError

from amane.config import LLMConfig
from amane.config.manager import PROMPT_MAX_LENGTH
from amane.enums import Language, MetadataField
from amane.llm import TARGET_LANG_PLACEHOLDER, build_system_prompt

_TITLE_HINT = "这是一部影片的标题, 翻译应简洁自然, 保留专有名词与番号."
_PLOT_HINT = "这是一部影片的简介, 完整通顺地翻译全部内容."
_OUTPUT_CONSTRAINT = "只输出译文本身, 不要解释、不要引号、不要附加任何内容."


def _instruction(lang_name: str) -> str:
    return f"你是专业的影视元数据翻译. 将用户提供的文本翻译为{lang_name}."


_ZH = _instruction("简体中文")
_ZH_TW = _instruction("繁體中文")


@pytest.mark.parametrize(
    ("target", "field", "system_prompt", "field_prompts", "expected"),
    [
        # 未配置: 指令 + 内置字段说明 + 输出约束
        (Language.ZH_CN, MetadataField.TITLE, None, None, f"{_ZH} {_TITLE_HINT} {_OUTPUT_CONSTRAINT}"),
        # 无内置说明的字段: 不追加空说明, 也不留下多余空格
        (Language.ZH_CN, MetadataField.SCORE, None, None, f"{_ZH} {_OUTPUT_CONSTRAINT}"),
        # 自定义指令: 占位符替换为目标语言名
        (
            Language.ZH_TW,
            MetadataField.TITLE,
            f"将输入翻译为{TARGET_LANG_PLACEHOLDER}, 只使用中性词汇.",
            None,
            f"将输入翻译为繁體中文, 只使用中性词汇. {_TITLE_HINT} {_OUTPUT_CONSTRAINT}",
        ),
        # 不写占位符时按原文使用, 不自动补充目标语言
        (
            Language.ZH_CN,
            MetadataField.TITLE,
            "只输出中文译文.",
            None,
            f"只输出中文译文. {_TITLE_HINT} {_OUTPUT_CONSTRAINT}",
        ),
        # 空白指令回退内置
        (Language.ZH_CN, MetadataField.TITLE, "   ", None, f"{_ZH} {_TITLE_HINT} {_OUTPUT_CONSTRAINT}"),
        # 字段说明覆盖该字段的内置说明, 且同样支持占位符
        (
            Language.ZH_TW,
            MetadataField.PLOT,
            None,
            {MetadataField.PLOT: f"用{TARGET_LANG_PLACEHOLDER}書面語."},
            f"{_ZH_TW} 用繁體中文書面語. {_OUTPUT_CONSTRAINT}",
        ),
        # 覆盖只作用于列出的字段
        (
            Language.ZH_CN,
            MetadataField.PLOT,
            None,
            {MetadataField.TITLE: "标题不超过 30 字."},
            f"{_ZH} {_PLOT_HINT} {_OUTPUT_CONSTRAINT}",
        ),
        # 花括号按字面保留 (允许 JSON 示例), 不触发格式化
        (
            Language.ZH_CN,
            MetadataField.TITLE,
            '按 {"title": "值"} 结构理解输入.',
            {MetadataField.TITLE: "保留 {番号} 原样."},
            f'按 {{"title": "值"}} 结构理解输入. 保留 {{番号}} 原样. {_OUTPUT_CONSTRAINT}',
        ),
    ],
)
def test_build_system_prompt(
    target: Language,
    field: MetadataField,
    system_prompt: str | None,
    field_prompts: dict[MetadataField, str] | None,
    expected: str,
) -> None:
    assert build_system_prompt(target, field, system_prompt=system_prompt, field_prompts=field_prompts) == expected


@pytest.mark.parametrize(
    ("payload", "expected_system", "expected_fields"),
    [
        ({}, None, {}),
        # 首尾空白去除
        ({"system_prompt": "  只用中性词汇.  "}, "只用中性词汇.", {}),
        # 纯空白等价于未配置
        ({"system_prompt": "   "}, None, {}),
        # 空白条目与缺席等价, 只保留有内容的字段
        (
            {"field_prompts": {"title": " 标题简洁 ", "plot": "   "}},
            None,
            {MetadataField.TITLE: "标题简洁"},
        ),
    ],
)
def test_llm_config_prompt_normalization(
    payload: dict, expected_system: str | None, expected_fields: dict[MetadataField, str]
) -> None:
    cfg = LLMConfig.model_validate(payload)
    assert cfg.system_prompt == expected_system
    assert cfg.field_prompts == expected_fields


@pytest.mark.parametrize(
    ("payload", "match"),
    [
        # 字段名受 MetadataField 约束
        ({"field_prompts": {"nope": "标题简洁"}}, "field_prompts.nope"),
        # 单条长度上限
        ({"system_prompt": "x" * (PROMPT_MAX_LENGTH + 1)}, "at most 2000 characters"),
        ({"field_prompts": {"plot": "x" * (PROMPT_MAX_LENGTH + 1)}}, "at most 2000 characters"),
    ],
)
def test_llm_config_rejects_invalid_prompts(payload: dict, match: str) -> None:
    with pytest.raises(ValidationError, match=match):
        LLMConfig.model_validate(payload)


def test_llm_config_accepts_max_length_prompt() -> None:
    """长度上限本身合法, 界外才拒绝."""
    at_limit = "x" * PROMPT_MAX_LENGTH
    cfg = LLMConfig(system_prompt=at_limit, field_prompts={MetadataField.TITLE: at_limit})
    assert cfg.system_prompt == at_limit
    assert cfg.field_prompts[MetadataField.TITLE] == at_limit
