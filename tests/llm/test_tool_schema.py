"""发给上游的工具定义收敛: 不声明 strict, 去掉取值为 false 的 additionalProperties."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, cast

import httpx2
import pytest
from pydantic_ai import Agent, RunContext
from pydantic_ai.tools import ToolDefinition

from amane.agent.tool_schema import ToolSchemaCapability
from amane.agent.tools import AgentDeps
from amane.enums import ApiType
from amane.llm import build_model

_ENDPOINT = "https://api.example/v1"

_CTX = cast(RunContext[AgentDeps], SimpleNamespace(deps=None))
"""`prepare_tools` 不读取 ctx; 构造最小替代实现, 免去为纯 schema 变换准备 deps."""

_CHAT_COMPLETION: dict[str, Any] = {
    "id": "chatcmpl-test",
    "object": "chat.completion",
    "created": 0,
    "model": "test-model",
    "choices": [{"index": 0, "message": {"role": "assistant", "content": "OK"}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("label", "strict", "schema", "expected_strict", "expected_schema"),
    [
        ("混合 strict 归零", True, {"type": "object"}, False, {"type": "object"}),
        ("未声明 strict 归零", None, {"type": "object"}, False, {"type": "object"}),
        (
            "顶层 false 删除",
            None,
            {"type": "object", "additionalProperties": False},
            False,
            {"type": "object"},
        ),
        (
            "true 保留",
            None,
            {"type": "object", "additionalProperties": True},
            False,
            {"type": "object", "additionalProperties": True},
        ),
        (
            "子 schema 保留",
            None,
            {"type": "object", "additionalProperties": {"type": "string"}},
            False,
            {"type": "object", "additionalProperties": {"type": "string"}},
        ),
        (
            "嵌套均删除",
            None,
            {"properties": {"a": {"type": "object", "additionalProperties": False}}},
            False,
            {"properties": {"a": {"type": "object"}}},
        ),
        (
            "数组内删除",
            None,
            {"anyOf": [{"type": "object", "additionalProperties": False}]},
            False,
            {"anyOf": [{"type": "object"}]},
        ),
    ],
)
async def test_tool_schema_capability_normalizes(
    label: str,
    strict: bool | None,
    schema: dict[str, Any],
    expected_strict: bool,
    expected_schema: dict[str, Any],
) -> None:
    """strict 一律收敛为 False, 两项变换互不影响.

    additionalProperties 只删除取值为 false 的: 它仅显式禁止 schema 外字段, 本地校验已覆盖;
    true 与子 schema 表达开放结构, 删除会改变语义.
    """
    defs = [ToolDefinition(name="t", parameters_json_schema=schema, strict=strict)]
    result = await ToolSchemaCapability().prepare_tools(_CTX, defs)

    assert result[0].strict == expected_strict, label
    assert result[0].parameters_json_schema == expected_schema, label


@pytest.mark.asyncio
async def test_agent_request_has_no_strict_key() -> None:
    """装配后的真实工具面发给上游时不含 strict 键, 否则混用会让端点 400."""
    captured: list[dict[str, Any]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        captured.append(json.loads(request.content))
        return httpx2.Response(200, json=_CHAT_COMPLETION)

    def alpha(a: int, b: int) -> str:
        """两个必填参数, 推断为 strict 兼容."""
        return f"{a}{b}"

    def beta(a: int, b: int = 3) -> str:
        """带默认值, 推断为 strict 不兼容."""
        return f"{a}{b}"

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        model = build_model(ApiType.CHAT, base_url=_ENDPOINT, api_key="k", model="m", http_client=client)
        agent = Agent(model, tools=[alpha, beta], capabilities=[ToolSchemaCapability()])
        await agent.run("hi")

    strict_values = [tool["function"].get("strict") for tool in captured[0]["tools"]]
    assert strict_values == [None, None]

    for tool in captured[0]["tools"]:
        assert "additionalProperties" not in json.dumps(tool["function"]["parameters"])
