"""AG-UI 协议端点: 回合在后台执行, 事件落盘后分发给订阅者.

回合不随连接存活: 断连只结束订阅, 回合继续运行至结束; 页面切走再回来靠 ``GET .../agui/events`` 续上订阅.

落盘两类行 (契约见 `...agent.rows`):
- `AguiEventRow`: 分发给订阅端的 AG-UI 事件
- 其余: 页面重建对话用的回放行, 由 `_ReplayRows` 从同一份事件展开; 逐请求用量在回合末尾成批补, 用
  ``after_tool_call`` 标出该次请求的位置

``RUN_FINISHED.usage`` 由本端点补: 协议有这个字段, 官方适配器不填.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any

from ag_ui.core import (
    BaseEvent,
    InputContent,
    Interrupt,
    Message,
    ReasoningMessageContentEvent,
    RunErrorEvent,
    RunFinishedEvent,
    RunFinishedInterruptOutcome,
    RunFinishedOutcome,
    TextInputContent,
    TextMessageContentEvent,
    TokenUsage,
    ToolCallArgsEvent,
    ToolCallEndEvent,
    ToolCallResultEvent,
    ToolCallStartEvent,
    UserMessage,
)
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import JsonValue
from pydantic_ai import AgentRunResult, DeferredToolRequests
from pydantic_ai.messages import ModelMessage, ModelRequest, UserPromptPart
from pydantic_ai.ui.ag_ui import AGUIAdapter
from pydantic_ai.usage import RunUsage

from ...agent.rows import (
    AguiEventRow,
    ApprovalsRow,
    CancelledRow,
    ErrorRow,
    ReasoningDeltaRow,
    RequestUsageRow,
    TextDeltaRow,
    ToolCallRow,
    ToolResultRow,
    TurnUsageRow,
    UiRow,
    UserMessageRow,
    no_approvals,
)
from ...agent.runtime import UNLIMITED_USAGE, resolve_model_settings
from ...agent.tools import AgentDeps
from ...agent.trace import SessionStore
from ...agent.usage import RequestTokenUsage, request_usages_from_run, turn_usage_from_run
from ...db.models import AgentSessionStatus
from ..deps import AgentDep, RepoDep, RuntimeDep
from ..models.agent import AgentCancelResponse

router = APIRouter(tags=["agent"])

SSE_CONTENT_TYPE = "text/event-stream"


def _content_text(content: str | list[InputContent]) -> str:
    """指纹只取文本部分; 媒体部分不参与比对."""
    if isinstance(content, str):
        return content
    return "".join(part.text for part in content if isinstance(part, TextInputContent))


def _history_user_texts(history: Sequence[ModelMessage]) -> list[str]:
    """服务端历史里的用户输入文本."""
    texts: list[str] = []
    for message in history:
        if not isinstance(message, ModelRequest):
            continue
        for part in message.parts:
            if isinstance(part, UserPromptPart) and isinstance(part.content, str):
                texts.append(part.content)
                break
    return texts


def _new_messages(client: Sequence[Message], history: Sequence[ModelMessage]) -> list[Message]:
    """客户端本次要送的新内容.

    AG-UI 输入契约要求客户端回放整段会话, 而服务端历史 (`SessionStore`) 才是权威, 故按**用户输入**切分:
    开头若干条用户文本与服务端历史一一对应, 落在它们之后的消息才算本轮新内容.

    不能逐条比对全部消息: 一个回合里的工具调用在服务端是「一次模型请求一条消息」, 在客户端回放里是
    合并后的一个助手气泡, 两侧分组不同, 逐条比对必然错位并把旧内容重新送给模型.
    """
    replayed = _history_user_texts(history)
    matched = 0
    for index, message in enumerate(client):
        if not isinstance(message, UserMessage):
            continue
        text = _content_text(message.content)
        if not text:
            continue
        if matched < len(replayed) and replayed[matched] == text:
            matched += 1
            continue
        return list(client[index:])
    return []


def _new_user_texts(messages: Sequence[Message]) -> list[str]:
    """本轮客户端新发的用户文本 (重放部分已裁掉)."""
    return [
        text for message in messages if isinstance(message, UserMessage) and (text := _content_text(message.content))
    ]


def _maybe_json(value: str) -> JsonValue:
    """工具参数与回执在协议里是字符串; 回放行按对象存, 页面才能取字段 (如 saved_query_id)."""
    try:
        parsed: JsonValue = json.loads(value)
    except ValueError:
        return value
    return parsed


def _interrupts_of(outcome: RunFinishedOutcome | None) -> list[Interrupt]:
    """本回合留下的未决中断; 成功或没有 outcome 时为空 (空列表会清掉页面上的待批态)."""
    if isinstance(outcome, RunFinishedInterruptOutcome):
        return list(outcome.interrupts)
    return []


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
    """把 AG-UI 事件展开为回放行 (页面重建对话用).

    正文与工具回执按事件粒度落盘, 因此切回会话能看到逐条进展, 不必等整个回合结束.
    工具参数在协议里是增量字符串, 这里拼装成对象再落盘, 页面无需二次解析.
    """

    tool_names: dict[str, str] = field(default_factory=dict)
    tool_args: dict[str, str] = field(default_factory=dict)
    usage: RunUsage | None = None
    request_usages: list[RequestTokenUsage] = field(default_factory=list)

    def feed(self, event: BaseEvent) -> Iterator[UiRow]:
        match event:
            case ReasoningMessageContentEvent(message_id=block_id, delta=delta):
                yield ReasoningDeltaRow(type="reasoning_delta", block_id=block_id, text=delta)
            case TextMessageContentEvent(message_id=block_id, delta=delta):
                yield TextDeltaRow(type="text_delta", block_id=block_id, text=delta)
            case ToolCallStartEvent(tool_call_id=tool_call_id, tool_call_name=name):
                self.tool_names[tool_call_id] = name
                self.tool_args[tool_call_id] = ""
            case ToolCallArgsEvent(tool_call_id=tool_call_id, delta=delta):
                self.tool_args[tool_call_id] = self.tool_args.get(tool_call_id, "") + delta
            case ToolCallEndEvent(tool_call_id=tool_call_id):
                yield ToolCallRow(
                    type="tool_call",
                    tool_call_id=tool_call_id,
                    name=self.tool_names.get(tool_call_id, "tool"),
                    args=_maybe_json(self.tool_args.get(tool_call_id, "")),
                )
            case ToolCallResultEvent(tool_call_id=tool_call_id, content=content):
                yield ToolResultRow(type="tool_result", tool_call_id=tool_call_id, result=_maybe_json(content))
            case RunFinishedEvent():
                for item in self.request_usages:
                    yield RequestUsageRow(type="request_usage", usage=item)
                if self.usage is not None:
                    yield TurnUsageRow(type="turn_usage", usage=turn_usage_from_run(self.usage))
                yield ApprovalsRow(type="approvals", interrupts=_interrupts_of(event.outcome))
            case RunErrorEvent():
                # 出错时适配器不补 RUN_FINISHED (after_stream 见到 _error 即返回): 失败原因与终态快照都在这里补
                yield ErrorRow(type="error", message=event.message)
                yield no_approvals()
            case _:
                return


def _sse(payload: dict[str, Any]) -> str:
    """SSE 分帧: ``data: {json}\\n\\n``."""
    return f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"


async def _follow_agui(store: SessionStore, after: int) -> AsyncIterator[str]:
    """回放 ``after`` 之后的 AG-UI 事件并跟随新事件; 回合结束且追平后结束."""
    async for row in store.follow(after):
        if isinstance(row, AguiEventRow):
            yield _sse(row.event)


async def _follow_rows(store: SessionStore) -> AsyncIterator[str]:
    """同上, 但发页面契约的行 (含整段回放): 页面据此重建气泡与工具卡片, 不认 AG-UI 协议事件."""
    async for row in store.follow(0):
        if isinstance(row, AguiEventRow):
            continue
        yield _sse(row.model_dump(mode="json", by_alias=True))


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
    incoming = _new_messages(list(adapter.run_input.messages), history)
    adapter.run_input.messages = incoming
    for text in _new_user_texts(incoming):
        await store.append_row(UserMessageRow(type="user_message", text=text))

    start_seq = store.last_seq
    rows = _ReplayRows()
    deps = service._make_deps(session_id)
    store.set_turn_running(True)

    async def on_complete(result: AgentRunResult[Any]) -> AsyncIterator[BaseEvent]:
        store.save_messages(list(result.all_messages()))
        rows.usage = result.usage
        rows.request_usages = request_usages_from_run(result)
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
                    AguiEventRow(type="agui", event=event.model_dump(mode="json", by_alias=True, exclude_none=True))
                )
                for row in rows.feed(event):
                    await store.append_row(row)
        except asyncio.CancelledError:
            await store.append_row(CancelledRow(type="cancelled"))
            await store.append_row(no_approvals())
            await runtime.repo.update_agent_session(session_id, status=AgentSessionStatus.ACTIVE)
            raise
        except Exception as exc:
            await store.append_row(AguiEventRow(type="agui", event={"type": "RUN_ERROR", "message": str(exc)}))
            await store.append_row(ErrorRow(type="error", message=str(exc)))
            await store.append_row(no_approvals())
            await runtime.repo.update_agent_session(session_id, status=AgentSessionStatus.ACTIVE)
        finally:
            store.set_turn_running(False)

    task = asyncio.create_task(consume(), name=f"agui-turn-{session_id}")
    service.track_turn(session_id, task)
    return StreamingResponse(_follow_agui(store, start_seq), media_type=SSE_CONTENT_TYPE)


@router.get(
    "/agent/sessions/{session_id}/agui/events",
    response_class=StreamingResponse,
    responses={200: {"content": {SSE_CONTENT_TYPE: {}}}},
)
async def follow_agent_events(session_id: int, service: AgentDep, repo: RepoDep) -> StreamingResponse:
    """跟随已落盘的回放行 (先整段回放, 再跟随新行), 供页面重挂载后续上进度.

    只订阅, **不**启动回合, 故进行中的回合也不会 409; 回合结束且追平后关闭.
    """
    if await repo.get_agent_session(session_id) is None:
        raise HTTPException(404, detail="会话不存在")
    return StreamingResponse(_follow_rows(service.store_for(session_id)), media_type=SSE_CONTENT_TYPE)


@router.post("/agent/sessions/{session_id}/agui/cancel")
async def cancel_agui_turn(session_id: int, service: AgentDep) -> AgentCancelResponse:
    """显式终止后台回合: 客户端 abort 只是断开订阅, 回合会继续运行."""
    try:
        cancelled = await service.cancel_turn(session_id)
    except KeyError:
        raise HTTPException(404, detail="会话不存在") from None
    return AgentCancelResponse(cancelled=cancelled)
