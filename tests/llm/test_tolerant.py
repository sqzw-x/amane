"""上游返回非标准值时的容错."""

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


def _completion_with_tool_call(tool_call: dict[str, Any]) -> dict[str, Any]:
    """把给定工具调用写入一份合法的 Chat Completions 响应."""
    return {
        **_CHAT_COMPLETION,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": None, "tool_calls": [tool_call]},
                "finish_reason": "tool_calls",
            }
        ],
    }


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
        (
            "三字段全 null",
            {"id": None, "type": None, "function": {"name": "f", "arguments": None}},
            {},
        ),
    ],
)
async def test_chat_completion_tolerates_missing_tool_fields(
    label: str, tool_call: dict[str, Any], expected_args: dict[str, Any]
) -> None:
    """缺字段按规范补齐后正常解析; 已有值不被改写."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=_completion_with_tool_call(tool_call))

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        model = build_model(ApiType.CHAT, base_url=_ENDPOINT, api_key="k", model="m", http_client=client)
        response = await model.request(
            [ModelRequest(parts=[UserPromptPart(content="hi")])], None, ModelRequestParameters()
        )

    parts = [p for p in response.parts if p.part_kind == "tool-call"]
    assert len(parts) == 1, label
    args = parts[0].args
    assert (json.loads(args) if isinstance(args, str) and args else {}) == expected_args, label
    assert parts[0].tool_call_id, f"{label}: 补齐后应有非空 id"


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
    """流式 chunk 与非流式同样补齐, 不因缺字段中断."""

    def sse(payload: dict[str, Any]) -> bytes:
        return f"data: {json.dumps(payload)}\n\n".encode()

    chunks = [
        {
            "id": "c",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "m",
            "choices": [
                {"index": 0, "delta": {"role": "assistant", "tool_calls": [chunk_delta]}, "finish_reason": None}
            ],
        },
        {
            "id": "c",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "m",
            "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
        },
    ]

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            content=b"".join(sse(c) for c in chunks) + b"data: [DONE]\n\n",
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

    tool_calls = [part for part in response.parts if isinstance(part, ToolCallPart)]
    assert len(tool_calls) == 1, label
    args = tool_calls[0].args
    assert (json.loads(args) if isinstance(args, str) and args else {}) == expected_args, label
    assert tool_calls[0].tool_call_id, f"{label}: 补齐后应有非空 id"


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
    args = tool_calls[0].args
    assert (json.loads(args) if isinstance(args, str) and args else {}) == expected_args, label
