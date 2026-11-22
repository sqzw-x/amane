"""上游端点返回非标准值时的容错.

pydantic-ai 假设端点遵守其声明协议, 但中转站与国内厂商常在必需字段返回 `null`: 空参工具调用的
`arguments`、缺 id 的 `tool_calls`. 默认校验在构造响应时抛 `ValidationError`, 整个 run 失败.

只处理 Chat Completions 与 Responses, 二者的接入位置不同:

- Chat Completions: `_validate_completion` 与流式 `_validate_response` 在校验前接收原始数据.
- Responses: 没有校验前置钩子, 覆写 `_process_response` 补 `output` (缺该键或为 `null` 时父类
  遍历 `None`, 抛出 `TypeError`); 流式的参数增量`delta` 为 `null` 时父类直接累加, 在 `_process_streamed_response`
  入口归一化事件流.

Anthropic 无需处理: SDK 接受 `tool_use` 的 `id` / `input` 为 `null`, 且重放时自行生成 id.

归一化只填写该字段的规范默认值, 不改写已有值; 发生改写时记录 warning, 便于定位到具体端点.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterable, AsyncIterator
from typing import TYPE_CHECKING, Any, cast

from pydantic_ai.models.openai import (
    OpenAIChatModel,
    OpenAIResponsesModel,
    OpenAIStreamedResponse,
    _ChatCompletion,  # type: ignore[attr-defined]
)

if TYPE_CHECKING:
    from openai import AsyncStream

logger = logging.getLogger(__name__)

_PLACEHOLDER_TOOL_CALL_ID = "call_0"
"""占位 id. 上游返回 `null` 时补齐, 避免校验失败; 重放时 pydantic-ai 另行生成 id, 该值不外泄."""


def _normalize_tool_call(tool_call: dict[str, Any], index: int) -> bool:
    """补齐一个工具调用的必需字段. 返回是否改动过."""
    changed = False
    if not tool_call.get("id"):
        tool_call["id"] = f"{_PLACEHOLDER_TOOL_CALL_ID}{index}"
        changed = True
    if not tool_call.get("type"):
        tool_call["type"] = "function"
        changed = True
    function = tool_call.get("function")
    if isinstance(function, dict) and function.get("arguments") is None:
        function["arguments"] = "{}"
        changed = True
    return changed


def _normalize_chat_payload(raw: dict[str, Any]) -> bool:
    """就地归一化 Chat Completions 响应体的工具调用. 返回是否改动过."""
    changed = False
    for choice in raw.get("choices") or []:
        message = choice.get("message") or choice.get("delta") or {}
        for index, tool_call in enumerate(message.get("tool_calls") or []):
            if _normalize_tool_call(tool_call, index):
                changed = True
    return changed


class TolerantChatModel(OpenAIChatModel):
    """Chat Completions: 校验前归一化非流式与流式响应."""

    def _validate_completion(self, response: Any) -> _ChatCompletion:
        data = response.model_dump(warnings=False)
        if _normalize_chat_payload(data):
            logger.warning("上游 %s 的工具调用缺少必需字段, 已按协议补齐", self.system)
        return _ChatCompletion.model_validate(data)

    @property
    def _streamed_response_cls(self) -> type[OpenAIStreamedResponse]:
        return _TolerantChatStreamedResponse


class _TolerantChatStreamedResponse(OpenAIStreamedResponse):
    """在流式 chunk 进入默认映射之前逐个归一化."""

    def _validate_response(self) -> AsyncIterable[Any]:
        return _normalize_chunks(self._response)


async def _normalize_chunks(source: AsyncIterable[Any]) -> AsyncIterator[Any]:
    async for chunk in source:
        data = chunk.model_dump(warnings=False)
        if _normalize_chat_payload(data):
            yield type(chunk).model_validate(data)
        else:
            yield chunk


class TolerantResponsesModel(OpenAIResponsesModel):
    """Responses: 无校验前置钩子, 在解析入口补齐."""

    def _process_response(self, response: Any, model_settings: Any, model_request_parameters: Any) -> Any:
        """补 `output`: 缺该键或为 `null` 时父类遍历 `None`, 抛出 `TypeError`."""
        if response.output is None:
            response.output = []
            logger.warning("上游 %s 的响应缺少 output, 已按空结果处理", self.system)
        return super()._process_response(response, model_settings, model_request_parameters)

    async def _process_streamed_response(self, response: Any, *args: Any, **kwargs: Any) -> Any:
        events = cast("AsyncStream[Any]", _normalize_responses_events(response, self.system))
        return await super()._process_streamed_response(events, *args, **kwargs)


async def _normalize_responses_events(source: AsyncIterable[Any], system: str) -> AsyncIterator[Any]:
    """把指向函数调用参数的 `null` 归一化为空串.

    父类对 `function_call_arguments.delta` 的 `delta` 直接 `+=` 累加, 收到 `null` 时抛出 `TypeError`;
    `output_item.added` 与 `arguments.done` 的 `arguments` 同样参与拼接.
    """
    normalized = False
    async for event in source:
        for holder in (_event_item(event), event):
            if holder is None:
                continue
            for field in ("arguments", "delta"):
                if getattr(holder, field, "") is None:
                    setattr(holder, field, "")
                    normalized = True
        yield event
    if normalized:
        logger.warning("上游 %s 的流式函数调用参数为 null, 已按空串处理", system)


def _event_item(event: Any) -> Any:
    """取事件携带的 item; 事件类型各异, 缺该属性时返回 `None`."""
    return getattr(event, "item", None)
