"""工具定义发给上游前的 schema 收敛.

pydantic-ai 逐工具推断 strict: schema 严格兼容的置 `True`, 其余留 `None` 且不发 `strict` 键.
一个请求里因此可能既有 `strict=True` 又有不带该键的工具, 部分中转站与国产端点会把这种混用
判为非法请求并返回 400.

这里做两件事, 都只精简发给上游的定义, 不改变工具语义:

- 所有工具统一 `strict=False`, 请求里不再出现 `strict` 键. 不统一置 `True`: 带默认值的参数在
  strict 模式下必须写入 `required`, 会把可选参数变成必填.
- 删除取值为 `False` 的 `additionalProperties`. 它只是显式禁止模型传 schema 外的字段; 模型本就
  照 schema 生成, 而多余字段由 pydantic 在本地校验时拒绝 (`extra_behavior=Forbid`), 不依赖该键.
  其余取值 (`true` / 子 schema) 保留.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.tools import ToolDefinition


def _strip_false_additional_properties(node: Any) -> Any:
    """递归删除取值为 `False` 的 `additionalProperties`, 其余取值原样保留."""
    if isinstance(node, dict):
        cleaned: dict[str, Any] = {}
        for key, value in node.items():
            if key == "additionalProperties" and value is False:
                continue
            cleaned[key] = _strip_false_additional_properties(value)
        return cleaned
    if isinstance(node, list):
        return [_strip_false_additional_properties(item) for item in node]
    return node


class ToolSchemaCapability(AbstractCapability[Any]):
    """把工具定义收敛为上游普遍接受的形态. 不读取 deps, 因此可装配到任意 AgentDeps."""

    async def prepare_tools(self, ctx: RunContext[Any], tool_defs: list[ToolDefinition]) -> list[ToolDefinition]:
        return [
            replace(
                tool_def,
                strict=False,
                parameters_json_schema=_strip_false_additional_properties(tool_def.parameters_json_schema),
            )
            for tool_def in tool_defs
        ]


def build_tool_schema_capability() -> AbstractCapability[Any]:
    return ToolSchemaCapability()
