"""调用方注入的番号解析规则.

值对象由配置层构造 (`config/manager.py::build_number_rules`), `parsing/` 只读取它, 不依赖 `config`.
字段为空表示与内置规则一致.
"""

from collections.abc import Mapping
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from .types import ContentType

__all__ = ["EMPTY_NUMBER_RULES", "NumberRules", "match_prefix_rule", "search_aliases_for"]


class NumberRules(BaseModel):
    """用户对番号解析与检索的约定; 冻结, 可安全共享.

    归一化 (去空白 / 大写 / 去重) 与短前缀预计算由构造方负责, 本对象不再校验.
    """

    model_config = ConfigDict(frozen=True)

    escape_strings: tuple[str, ...] = ()
    """解析前整段剔除的串; 文件名与目录名都适用."""

    prefix_types: Mapping[str, ContentType] = Field(default_factory=dict)
    """番号前缀 → 内容类型; 键为大写, 覆盖内置兜底判定."""

    search_aliases: Mapping[str, tuple[str, ...]] = Field(default_factory=dict)
    """番号前缀 → 检索别名番号; 非空即覆盖该前缀的自动别名."""

    prefix_short_forms: Annotated[Mapping[str, str], Field(exclude=True)] = Field(default_factory=dict)
    """派生量: 长前缀 → 短前缀 (`300MIUM` → `MIUM`), 由构造方预计算, 不由用户填写."""


EMPTY_NUMBER_RULES = NumberRules()


PREFIX_SEPARATORS = "-_. "


def match_prefix_rule[T](prefix: str, table: Mapping[str, T]) -> T | None:
    """按 `_prefix` 的输出查规则表, 兼容两种书写: 带与不带尾部分隔符.

    规则表在构造期已归一 (大写 / 无尾部分隔符); `_prefix` 的输出可能带尾部分隔符 (`300MIUM-`).
    """
    stripped = prefix.rstrip(PREFIX_SEPARATORS)
    return table.get(stripped) or table.get(prefix)


def search_aliases_for(number: str, rules: NumberRules) -> tuple[str, ...]:
    """该番号在别处的写法; 别名只在出站检索时使用, 不改本地番号.

    匹配键与 `prefix_types` 同口径: 先按 `_prefix` 的输出, 未命中再按长前缀的短形态.
    """
    if not rules.search_aliases:
        return ()
    prefix = _prefix(number)
    if not prefix:
        return ()
    found = match_prefix_rule(prefix, rules.search_aliases)
    if found is None:
        short = rules.prefix_short_forms.get(prefix.rstrip(PREFIX_SEPARATORS))
        if short is not None:
            found = rules.search_aliases.get(short)
    return found or ()


def _prefix(number: str) -> str:
    from .file_info import _prefix as impl

    return impl(number)
