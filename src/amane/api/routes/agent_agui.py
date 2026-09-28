"""AG-UI 协议端点: 回合在后台执行, 事件落盘后分发给订阅者.

回合不随连接存活: 断连只结束订阅, 回合继续跑完.

落盘两类行:
- ``{"type": "agui", "event": ...}``: 分发给订阅端的 AG-UI 事件
- ``user_message`` / ``text_delta`` / ``tool_call`` / ``tool_result`` / ``assistant_message``:
  页面重建对话用的回放行

``RUN_FINISHED.usage`` 由本端点补: 协议有这个字段, 官方适配器不填.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any

from ag_ui.core import (
    AssistantMessage,
    BaseEvent,
    InputContent,
    Message,
    RunFinishedEvent,
    TextInputContent,
    TextMessageContentEvent,
    TokenUsage,
    ToolCallArgsEvent,
    ToolCallEndEvent,
    ToolCallResultEvent,
    ToolCallStartEvent,
    ToolMessage,
    UserMessage,
)
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic_ai import AgentRunResult, DeferredToolRequests
from pydantic_ai.messages import ModelMessage
from pydantic_ai.ui.ag_ui import AGUIAdapter
from pydantic_ai.usage import RunUsage

from ...agent.events import turn_usage_from_run
from ...agent.runtime import UNLIMITED_USAGE, resolve_model_settings
from ...agent.tools import AgentDeps
from ...agent.trace import SessionStore
from ...db.models import AgentSessionStatus
from ..deps import AgentDep, RuntimeDep
from ..models.agent import AgentCancelResponse

router = APIRouter(tags=["agent"])

SSE_CONTENT_TYPE = "text/event-stream"


def _content_text(content: str | list[InputContent]) -> str:
    """指纹只取文本部分; 媒体部分不参与比对."""
    if isinstance(content, str):
        return content
    return "".join(part.text for part in content if isinstance(part, TextInputContent))


def _message_key(message: Message) -> tuple[str, str] | None:
    """(角色, 文本) 指纹; ``None`` 表示该类型不参与匹配.

    工具回执按 ``tool_call_id`` 比对: 服务端 dump 的 content 是 JSON 字符串, 客户端回放的是结构化
    结果, 文本形式必然不同, 但 id 两侧一致.
    """
    match message:
        case UserMessage(content=content):
            return ("user", _content_text(content))
        case AssistantMessage(content=content):
            return ("assistant", content or "")
        case ToolMessage(tool_call_id=tool_call_id):
            return ("tool", tool_call_id)
        case _:
            return None


def _replayed_len(client: list[Message], server: list[Message]) -> int:
    """客户端重放的服务端历史条数.

    AG-UI 输入契约要求 ``messages`` 完整, 客户端会把整段会话发回来; 服务端历史 (SessionStore)
    才是权威, 故只保留客户端多出的那一段. 逐条比对指纹, 首个不同即停.
    """
    shared = min(len(client), len(server))
    for index in range(shared):
        left, right = _message_key(client[index]), _message_key(server[index])
        if left is None or right is None or left != right:
            return index
    return shared


def _new_user_texts(messages: Sequence[Message]) -> list[str]:
    """本轮客户端新发的用户文本 (重放部分已裁掉)."""
    return [
        text for message in messages if isinstance(message, UserMessage) and (text := _content_text(message.content))
    ]


def _maybe_json(value: str) -> Any:
    """工具回执在协议里是字符串; 回放行按对象存, 页面才能取字段 (如 saved_query_id)."""
    try:
        return json.loads(value)
    except ValueError:
        return value


def _token_usage(usage: RunUsage) -> TokenUsage:
    """AG-UI 标准位. 该协议版本没有 cache_write 字段, 缓存写只体现在 input 总量里."""
    return TokenUsage(
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        total_tokens=usage.input_tokens + usage.output_tokens,
        reasoning_tokens=int(usage.details.get("reasoning_tokens", 0)),
        cached_input_tokens=usage.cache_read_tokens,
    )


@dataclass
class _ReplayRows:
    """把 AG-UI 事件摊成回放行 (页面重建对话用).

    正文与工具回执按事件粒度落盘, 因此切回会话能看到逐条进展, 不必等整个回合结束.
    """

    text: list[str] = field(default_factory=list)
    tool_names: dict[str, str] = field(default_factory=dict)
    tool_args: dict[str, str] = field(default_factory=dict)
    usage: RunUsage | None = None

    def feed(self, event: BaseEvent) -> Iterator[dict[str, Any]]:
        match event:
            case TextMessageContentEvent(delta=delta):
                self.text.append(delta)
                yield {"type": "text_delta", "text": delta}
            case ToolCallStartEvent(tool_call_id=tool_call_id, tool_call_name=name):
                self.tool_names[tool_call_id] = name
                self.tool_args[tool_call_id] = ""
            case ToolCallArgsEvent(tool_call_id=tool_call_id, delta=delta):
                self.tool_args[tool_call_id] = self.tool_args.get(tool_call_id, "") + delta
            case ToolCallEndEvent(tool_call_id=tool_call_id):
                yield {
                    "type": "tool_call",
                    "tool_call_id": tool_call_id,
                    "name": self.tool_names.get(tool_call_id, "tool"),
                    "args": _maybe_json(self.tool_args.get(tool_call_id, "")),
                }
            case ToolCallResultEvent(tool_call_id=tool_call_id, content=content):
                yield {
                    "type": "tool_result",
                    "tool_call_id": tool_call_id,
                    "name": self.tool_names.get(tool_call_id, "tool"),
                    "result": _maybe_json(content),
                }
            case RunFinishedEvent():
                if self.text or self.usage is not None:
                    yield {
                        "type": "assistant_message",
                        "text": "".join(self.text),
                        "usage": turn_usage_from_run(self.usage).model_dump() if self.usage else None,
                    }
            case _:
                return


def _sse(event: dict[str, Any]) -> str:
    """AG-UI 的 SSE 分帧: ``data: {json}\\n\\n``."""
    return f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"


async def _follow(store: SessionStore, after: int) -> AsyncIterator[str]:
    """回放 ``after`` 之后的事件并跟随新事件; 回合结束且追平后结束."""
    async for row in store.follow(after):
        if row.get("type") == "agui":
            yield _sse(row["event"])


# 未设置 response_class 时, FastAPI 会按 default_response_class 追加一条 application/json 声明,
# 生成客户端据此按非流式请求处理. 运行时返回的仍是 StreamingResponse 实例.
@router.post(
    "/agent/sessions/{session_id}/agui",
    response_class=StreamingResponse,
    responses={200: {"content": {SSE_CONTENT_TYPE: {}}}},
)
async def run_agent_agui(
    session_id: int, request: Request, service: AgentDep, runtime: RuntimeDep
) -> StreamingResponse:
    """启动 AG-UI 回合 (后台执行) 并订阅其事件. ``threadId`` 由适配器映射为 ``conversation_id``."""
    agent = service.agent
    if agent is None:
        raise HTTPException(503, detail="助理 Agent 未配置")
    session = await runtime.repo.get_agent_session(session_id)
    if session is None:
        raise HTTPException(404, detail="会话不存在")
    if service.is_turn_running(session_id):
        raise HTTPException(409, detail="会话已有进行中的回合")

    store = service.store_for(session_id)
    history: list[ModelMessage] = list(store.load_messages() or [])
    adapter = await AGUIAdapter[AgentDeps, str | DeferredToolRequests].from_request(request, agent=agent)
    replayed = _replayed_len(list(adapter.run_input.messages), adapter.dump_messages(history))
    adapter.run_input.messages = list(adapter.run_input.messages[replayed:])
    for text in _new_user_texts(adapter.run_input.messages):
        await store.append_row({"type": "user_message", "text": text})

    start_seq = store.last_seq
    rows = _ReplayRows()
    deps = service._make_deps(session_id, store)
    store.set_turn_running(True)

    async def on_complete(result: AgentRunResult[Any]) -> AsyncIterator[BaseEvent]:
        store.save_messages(list(result.all_messages()))
        rows.usage = result.usage
        output = result.output
        pending = isinstance(output, DeferredToolRequests) and bool(output.approvals)
        await runtime.repo.update_agent_session(
            session_id,
            status=AgentSessionStatus.AWAITING_APPROVAL if pending else AgentSessionStatus.ACTIVE,
        )
        return
        yield

    async def consume() -> None:
        try:
            async for event in adapter.run_stream(
                message_history=history,
                deps=deps,
                model_settings=resolve_model_settings(
                    service.config, session_thinking=service.session_thinking(session_id)
                ),
                usage_limits=UNLIMITED_USAGE,
                on_complete=on_complete,
            ):
                if isinstance(event, RunFinishedEvent) and rows.usage is not None:
                    event.usage = [_token_usage(rows.usage)]
                await store.append_row(
                    {"type": "agui", "event": event.model_dump(mode="json", by_alias=True, exclude_none=True)}
                )
                for row in rows.feed(event):
                    await store.append_row(row)
        except Exception as exc:
            await store.append_row({"type": "agui", "event": {"type": "RUN_ERROR", "message": str(exc)}})
            await store.append_row({"type": "error", "message": str(exc)})
            await runtime.repo.update_agent_session(session_id, status=AgentSessionStatus.ACTIVE)
        finally:
            store.set_turn_running(False)

    task = asyncio.create_task(consume(), name=f"agui-turn-{session_id}")
    service._turn_tasks[session_id] = task

    def _clear(finished: asyncio.Task[None]) -> None:
        if service._turn_tasks.get(session_id) is finished:
            service._turn_tasks.pop(session_id, None)

    task.add_done_callback(_clear)
    return StreamingResponse(_follow(store, start_seq), media_type=SSE_CONTENT_TYPE)


@router.post("/agent/sessions/{session_id}/agui/cancel")
async def cancel_agui_turn(session_id: int, service: AgentDep) -> AgentCancelResponse:
    """显式终止后台回合: 客户端 abort 只是断开订阅, 回合会继续跑完."""
    try:
        cancelled = await service.cancel_turn(session_id)
    except KeyError:
        raise HTTPException(404, detail="会话不存在") from None
    return AgentCancelResponse(cancelled=cancelled)
