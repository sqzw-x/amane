"""上游返回非标准值时的容错.

Chat Completions 的流式路径不做归一化: 规范里工具调用的 `id` 只在首个增量出现, 后续增量没有该键,
逐 chunk 补 id 会覆盖服务端已给出的 id. 这里同时锁住这条边界与"补齐出来的 id 必须唯一".
"""

from __future__ import annotations

import json
from typing import Any

import httpx2
import pytest
from pydantic_ai.messages import ModelRequest, ToolCallPart, UserPromptPart
from pydantic_ai.models import ModelRequestParameters

from amane.enums import ApiType
from amane.llm import build_model

_ENDPOINT = "https://api.example/v1"

_CHAT_COMPLETION: dict[str, Any] = {
    "id": "chatcmpl-test",
    "object": "chat.completion",
    "created": 0,
    "model": "test-model",
    "choices": [{"index": 0, "message": {"role": "assistant", "content": "OK"}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
}


def _completion_with_tool_calls(tool_calls: list[dict[str, Any]]) -> dict[str, Any]:
    """把给定工具调用写入一份合法的 Chat Completions 响应."""
    return {
        **_CHAT_COMPLETION,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": None, "tool_calls": tool_calls},
                "finish_reason": "tool_calls",
            }
        ],
    }


def _args_of(part: ToolCallPart) -> dict[str, Any]:
    """取出工具调用的参数对象; `args` 可能是 JSON 串、已解析的 dict 或空."""
    if isinstance(part.args, dict):
        return part.args
    return json.loads(part.args) if isinstance(part.args, str) and part.args else {}


def _sse(payload: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(payload)}\n\n".encode()


def _chunk(delta: dict[str, Any], finish: str | None = None) -> dict[str, Any]:
    return {
        "id": "c",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "m",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }


async def _stream_tool_calls(chunks: list[dict[str, Any]]) -> list[ToolCallPart]:
    """把给定的 chunk 序列喂进流式解析, 返回解析出的工具调用."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            content=b"".join(_sse(c) for c in chunks) + b"data: [DONE]\n\n",
            headers={"content-type": "text/event-stream"},
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        model = build_model(ApiType.CHAT, base_url=_ENDPOINT, api_key="k", model="m", http_client=client)
        async with model.request_stream(
            [ModelRequest(parts=[UserPromptPart(content="hi")])], None, ModelRequestParameters()
        ) as stream:
            async for _ in stream:
                pass
            response = stream.get()

    return [part for part in response.parts if isinstance(part, ToolCallPart)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("label", "tool_call", "expected_args"),
    [
        (
            "全字段合规",
            {"id": "call_1", "type": "function", "function": {"name": "f", "arguments": '{"a":1}'}},
            {"a": 1},
        ),
        ("缺 id", {"type": "function", "function": {"name": "f", "arguments": '{"a":1}'}}, {"a": 1}),
        ("id 为 null", {"id": None, "type": "function", "function": {"name": "f", "arguments": '{"a":1}'}}, {"a": 1}),
        ("type 为 null", {"id": "call_1", "type": None, "function": {"name": "f", "arguments": '{"a":1}'}}, {"a": 1}),
        ("arguments 为 null", {"id": "call_1", "type": "function", "function": {"name": "f", "arguments": None}}, {}),
        ("arguments 为空串", {"id": "call_1", "type": "function", "function": {"name": "f", "arguments": ""}}, {}),
        ("三字段全 null", {"id": None, "type": None, "function": {"name": "f", "arguments": None}}, {}),
    ],
)
async def test_chat_completion_tolerates_missing_tool_fields(
    label: str, tool_call: dict[str, Any], expected_args: dict[str, Any]
) -> None:
    """缺字段按规范补齐后正常解析; 已有值不被改写."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=_completion_with_tool_calls([tool_call]))

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        model = build_model(ApiType.CHAT, base_url=_ENDPOINT, api_key="k", model="m", http_client=client)
        response = await model.request(
            [ModelRequest(parts=[UserPromptPart(content="hi")])], None, ModelRequestParameters()
        )

    parts = [p for p in response.parts if isinstance(p, ToolCallPart)]
    assert len(parts) == 1, label
    assert _args_of(parts[0]) == expected_args, label
    assert parts[0].tool_call_id, f"{label}: 补齐后应有非空 id"


@pytest.mark.asyncio
async def test_chat_completion_generates_distinct_ids_for_parallel_calls() -> None:
    """同批多个调用都缺 id 时各自补出唯一 id.

    pydantic-ai 按 `tool_call_id` 绑定调用与结果, 同批重复即拒绝执行整批.
    """
    tool_calls = [
        {"type": "function", "function": {"name": "f", "arguments": "{}"}},
        {"type": "function", "function": {"name": "g", "arguments": "{}"}},
    ]

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=_completion_with_tool_calls(tool_calls))

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        model = build_model(ApiType.CHAT, base_url=_ENDPOINT, api_key="k", model="m", http_client=client)
        response = await model.request(
            [ModelRequest(parts=[UserPromptPart(content="hi")])], None, ModelRequestParameters()
        )

    ids = [p.tool_call_id for p in response.parts if isinstance(p, ToolCallPart)]
    assert len(ids) == 2
    assert len(set(ids)) == 2, f"并行调用的 id 必须互不相同: {ids}"


@pytest.mark.asyncio
async def test_chat_streaming_keeps_server_tool_call_id() -> None:
    """后续增量不带 `id` 是规范行为, 不能补占位值把首个增量给出的真实 id 覆盖掉."""
    tool_calls = await _stream_tool_calls(
        [
            _chunk(
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call_abc",
                            "type": "function",
                            "function": {"name": "f", "arguments": ""},
                        }
                    ],
                }
            ),
            _chunk({"tool_calls": [{"index": 0, "function": {"arguments": '{"a":'}}]}),
            _chunk({"tool_calls": [{"index": 0, "function": {"arguments": "1}"}}]}),
            _chunk({}, "tool_calls"),
        ]
    )

    assert len(tool_calls) == 1
    assert tool_calls[0].tool_call_id == "call_abc"
    assert tool_calls[0].args == '{"a":1}'


@pytest.mark.asyncio
async def test_chat_streaming_generates_distinct_ids_for_parallel_calls() -> None:
    """上游不给 id 时由 pydantic-ai 生成; 多个并行调用不能撞成同一个."""
    tool_calls = await _stream_tool_calls(
        [
            _chunk(
                {
                    "role": "assistant",
                    "tool_calls": [{"index": 0, "type": "function", "function": {"name": "f", "arguments": ""}}],
                }
            ),
            _chunk({"tool_calls": [{"index": 1, "type": "function", "function": {"name": "g", "arguments": ""}}]}),
            _chunk({"tool_calls": [{"index": 0, "function": {"arguments": '{"a":1}'}}]}),
            _chunk({"tool_calls": [{"index": 1, "function": {"arguments": '{"b":2}'}}]}),
            _chunk({}, "tool_calls"),
        ]
    )

    assert len(tool_calls) == 2
    ids = [p.tool_call_id for p in tool_calls]
    assert all(ids), "流式缺失的 id 应由 pydantic-ai 补齐"
    assert len(set(ids)) == 2, f"并行调用的 id 必须互不相同: {ids}"
    assert [p.tool_name for p in tool_calls] == ["f", "g"]
    assert [_args_of(p) for p in tool_calls] == [{"a": 1}, {"b": 2}]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("label", "chunk_delta", "expected_args"),
    [
        (
            "arguments 为 null",
            {"index": 0, "id": "call_1", "type": "function", "function": {"name": "f", "arguments": None}},
            {},
        ),
        (
            "id 为 null",
            {"index": 0, "id": None, "type": "function", "function": {"name": "f", "arguments": '{"a":1}'}},
            {"a": 1},
        ),
        (
            "type 为 null",
            {"index": 0, "id": "call_1", "type": None, "function": {"name": "f", "arguments": '{"a":1}'}},
            {"a": 1},
        ),
    ],
)
async def test_chat_streaming_tolerates_missing_tool_fields(
    label: str, chunk_delta: dict[str, Any], expected_args: dict[str, Any]
) -> None:
    """流式缺字段由 SDK 与 pydantic-ai 自行承接, 不因缺字段中断."""
    tool_calls = await _stream_tool_calls(
        [_chunk({"role": "assistant", "tool_calls": [chunk_delta]}), _chunk({}, "tool_calls")]
    )

    assert len(tool_calls) == 1, label
    assert _args_of(tool_calls[0]) == expected_args, label
    assert tool_calls[0].tool_call_id, f"{label}: 流式失败后应有非空 id"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("label", "body"),
    [
        ("output 为 null", {"output": None}),
        ("缺 output 键", {}),
    ],
)
async def test_responses_tolerates_missing_output(label: str, body: dict[str, Any]) -> None:
    """`output` 缺失或为 `null` 时父类会遍历 `None` 崩溃; 按空结果处理而不是中断整个 run."""
    base = {
        "id": "resp_1",
        "object": "response",
        "created_at": 0,
        "model": "test-model",
        "status": "completed",
        "parallel_tool_calls": False,
        "tool_choice": "auto",
        "tools": [],
        **body,
    }

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=base)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        model = build_model(ApiType.RESPONSE, base_url=_ENDPOINT, api_key="k", model="m", http_client=client)
        response = await model.request(
            [ModelRequest(parts=[UserPromptPart(content="hi")])], None, ModelRequestParameters()
        )

    assert response.parts == [], label


def _responses_stream(arguments_delta: dict[str, Any]) -> bytes:
    """拼一份 Responses 流式响应; `arguments_delta` 决定参数增量的形状."""
    base: dict[str, Any] = {
        "id": "resp_1",
        "object": "response",
        "created_at": 0,
        "model": "test-model",
        "status": "completed",
        "parallel_tool_calls": False,
        "tool_choice": "auto",
        "tools": [],
        "output": [{"type": "function_call", "id": "fc_1", "call_id": "c1", "name": "f", "arguments": '{"a":1}'}],
    }
    events = [
        {"type": "response.created", "response": {**base, "status": "in_progress", "output": []}},
        {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {"type": "function_call", "id": "fc_1", "call_id": "c1", "name": "f", "arguments": ""},
        },
        {"type": "response.function_call_arguments.delta", "item_id": "fc_1", "output_index": 0, **arguments_delta},
        {"type": "response.function_call_arguments.done", "item_id": "fc_1", "output_index": 0},
        {"type": "response.completed", "response": base},
    ]
    return b"".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode() for event in events)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("label", "arguments_delta", "expected_args"),
    [
        ("正常增量", {"delta": '{"a":1}'}, {"a": 1}),
        ("增量为 null", {"delta": None}, {}),
        ("缺增量键", {}, {}),
    ],
)
async def test_responses_streaming_tolerates_null_arguments(
    label: str, arguments_delta: dict[str, Any], expected_args: dict[str, Any]
) -> None:
    """参数增量为 null 时父类直接 `+=` 累加并抛 TypeError; 按空串处理后仍产出工具调用."""
    body = _responses_stream(arguments_delta)

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, content=body, headers={"content-type": "text/event-stream"})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        model = build_model(ApiType.RESPONSE, base_url=_ENDPOINT, api_key="k", model="m", http_client=client)
        async with model.request_stream(
            [ModelRequest(parts=[UserPromptPart(content="hi")])], None, ModelRequestParameters()
        ) as stream:
            async for _ in stream:
                pass
            response = stream.get()

    tool_calls = [part for part in response.parts if isinstance(part, ToolCallPart)]
    assert len(tool_calls) == 1, label
    assert _args_of(tool_calls[0]) == expected_args, label


@pytest.mark.asyncio
async def test_agent_executes_parallel_tool_calls_without_ids() -> None:
    """上游一次给出的多个工具调用缺 id 时, 两个调用都要真正执行.

    真实端点会把同一个调用的声明与参数拆成同 index 的两条增量, 其中只有一条带 id.
    pydantic-ai 在执行前按 `tool_call_id` 绑定调用与结果, id 撞车会让整个 run 失败.
    """
    from pydantic_ai import Agent

    executed: list[tuple[str, dict[str, Any]]] = []
    turns = 0

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal turns
        turns += 1
        if turns > 1:
            return httpx2.Response(
                200,
                content=_sse(_chunk({"role": "assistant", "content": "done"}, "stop")) + b"data: [DONE]\n\n",
                headers={"content-type": "text/event-stream"},
            )
        chunks = [
            _chunk(
                {
                    "role": "assistant",
                    "tool_calls": [
                        {"index": 0, "type": "function", "function": {"name": "alpha", "arguments": None}},
                        {"index": 0, "type": "function", "function": {"name": None, "arguments": '{"x":1}'}},
                    ],
                }
            ),
            _chunk(
                {
                    "tool_calls": [
                        {"index": 1, "type": "function", "function": {"name": "beta", "arguments": None}},
                        {"index": 1, "type": "function", "function": {"name": None, "arguments": '{"y":2}'}},
                    ]
                }
            ),
            _chunk({}, "tool_calls"),
        ]
        return httpx2.Response(
            200,
            content=b"".join(_sse(c) for c in chunks) + b"data: [DONE]\n\n",
            headers={"content-type": "text/event-stream"},
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        model = build_model(ApiType.CHAT, base_url=_ENDPOINT, api_key="k", model="m", http_client=client)
        agent = Agent(model)

        @agent.tool_plain
        def alpha(x: int) -> str:
            """返回 alpha 结果."""
            executed.append(("alpha", {"x": x}))
            return "a-ok"

        @agent.tool_plain
        def beta(y: int) -> str:
            """返回 beta 结果."""
            executed.append(("beta", {"y": y}))
            return "b-ok"

        async with agent.run_stream("hi") as stream:
            async for _ in stream.stream_text(delta=True):
                pass

    assert executed == [("alpha", {"x": 1}), ("beta", {"y": 2})]
