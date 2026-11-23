"""上游端点返回非标准值时的容错.

pydantic-ai 假设端点遵守其声明协议, 但中转站与国内厂商常在必需字段返回 `null`: 空参工具调用的
`arguments`、缺 id 的 `tool_calls`. 默认校验在构造响应时抛 `ValidationError`, 整个 run 失败.

只处理 Chat Completions 与 Responses, 二者的接入位置不同:

- Chat Completions: 只处理非流式响应, 挂在 `_validate_completion`. 流式 chunk 由 SDK 直接构造,
  不经过校验, 缺字段不会中断映射; pydantic-ai 对缺 id 的调用自行生成唯一 id. 逐 chunk 补 id 反而
  会覆盖服务端已给出的 id —— 规范里 `id` 只在首个增量出现, 后续增量没有该键.
- Responses: 没有校验前置钩子, 覆写 `_process_response` 补 `output` (缺该键或为 `null` 时父类
  遍历 `None`, 抛出 `TypeError`); 流式的参数增量 `delta` 为 `null` 时父类直接累加, 在
  `_process_streamed_response` 入口归一化事件流.

Anthropic 无需处理: SDK 接受 `tool_use` 的 `id` / `input` 为 `null`, 且重放时自行生成 id.

归一化只填写该字段的规范默认值, 不改写已有值; 发生改写时记录 warning, 便于定位到具体端点.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterable, AsyncIterator
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

from pydantic_ai.models.openai import (
    OpenAIChatModel,
    OpenAIResponsesModel,
    _ChatCompletion,  # type: ignore[attr-defined]
)

if TYPE_CHECKING:
    from openai import AsyncStream

logger = logging.getLogger(__name__)


def _normalize_tool_call(tool_call: dict[str, Any]) -> bool:
    """补齐一个工具调用的必需字段. 返回是否改动过.

    缺 id 时生成随机值而不是按位置编号: pydantic-ai 按 `tool_call_id` 绑定调用与结果, 同一批
    出现重复 id 即拒绝执行整批, 位置编号会让一次返回的多个无 id 调用撞成同一个.
    """
    changed = False
    if not tool_call.get("id"):
        tool_call["id"] = f"call_{uuid4().hex}"
        changed = True
    if not tool_call.get("type"):
        tool_call["type"] = "function"
        changed = True
    function = tool_call.get("function")
    if isinstance(function, dict) and function.get("arguments") is None:
        function["arguments"] = "{}"
        changed = True
    return changed


def _normalize_completion(raw: dict[str, Any]) -> bool:
    """就地归一化非流式响应体的工具调用. 返回是否改动过."""
    changed = False
    for choice in raw.get("choices") or []:
        message = choice.get("message") or {}
        for tool_call in message.get("tool_calls") or []:
            if _normalize_tool_call(tool_call):
                changed = True
    return changed


class TolerantChatModel(OpenAIChatModel):
    """Chat Completions: 校验前归一化非流式响应."""

    def _validate_completion(self, response: Any) -> _ChatCompletion:
        data = response.model_dump(warnings=False)
        if _normalize_completion(data):
            logger.warning("上游 %s 的工具调用缺少必需字段, 已按协议补齐", self.system)
        return _ChatCompletion.model_validate(data)


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
