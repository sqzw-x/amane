"""AG-UI 协议端点: 回合在后台执行, 事件落盘后分发给订阅者.

回合不随连接存活: 断连只结束订阅, 回合继续运行至结束; 页面切走再回来靠 ``GET .../agui/events`` 续上订阅.

落盘两类行 (契约见 `...agent.rows`):
- `AguiEventRow`: 分发给订阅端的 AG-UI 事件
- 其余: 页面重建对话用的回放行. 正文与工具由 `_ReplayRows` 从同一份事件展开; 逐请求用量由
  `_RequestUsageRows` 在每次响应完成时落行

``RUN_FINISHED.usage`` 由本端点补: 协议有这个字段, 官方适配器不填.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import datetime
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
from pydantic_ai import AgentRunResult, DeferredToolRequests, RunContext, capture_run_messages
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.exceptions import RunCancelled
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, UserPromptPart
from pydantic_ai.models import ModelRequestContext
from pydantic_ai.ui.ag_ui import AGUIAdapter
from pydantic_ai.usage import RunUsage

from ...agent.reasoning import EmbeddedThinkingRows
from ...agent.rows import (
    AguiEventRow,
    ApprovalsRow,
    CancelledRow,
    ErrorRow,
    ReasoningDeltaRow,
    RequestUsageRow,
    RetriedRow,
    TextDeltaRow,
    ToolCallRow,
    ToolResultRow,
    TraceRow,
    TurnUsageRow,
    UiRow,
    UserMessageRow,
    no_approvals,
)
from ...agent.runtime import UNLIMITED_USAGE, resolve_model_settings
from ...agent.tools import AgentDeps
from ...agent.trace import SessionStore
from ...agent.usage import request_usage, turn_usage_from_run
from ...db.models import AgentSessionStatus
from ...llm.retry import retry_sink
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


def _consumed_ids(rows: Sequence[TraceRow]) -> set[str] | None:
    """已入库的用户输入 id; 只要有一条是早于该字段写入的, 就返回 None, 整段退回按文本比对.

    混用两种口径会漏判: 旧行在页面里的 id 由 `seq` 现编, 永远不在已入库集合里, 按 id 切会把整段历史
    当成新输入重跑.
    """
    ids: set[str] = set()
    for row in rows:
        if isinstance(row, UserMessageRow):
            if row.message_id is None:
                return None
            ids.add(row.message_id)
    return ids


def _first_unconsumed(client: Sequence[Message], consumed: set[str]) -> int | None:
    """客户端回放里第一条尚未入库的用户输入的位置; 全都已入库 (或没有用户输入) 时报 None."""
    for index, message in enumerate(client):
        if isinstance(message, UserMessage) and _content_text(message.content) and message.id not in consumed:
            return index
    return None


def _new_messages(
    client: Sequence[Message], history: Sequence[ModelMessage], consumed: set[str] | None
) -> list[Message]:
    """客户端本次要送的新内容.

    AG-UI 输入契约要求客户端回放整段会话, 而服务端历史 (`SessionStore`) 才是权威, 故按**用户输入**切分:
    开头若干条已入库的输入与服务端一一对应, 落在第一条未入库输入之前的都算重放.

    切分按消息 id 而不是条数: 取消与失败的回合只在 `events.jsonl` 留用户输入、不进 `messages.json`,
    两侧的条数本就不相等, 按位置比对会把重放里的旧输入当成新输入再跑一轮 (重复回答与日志膨胀的由来).
    `consumed` 为 None 时没有 id 可依, 退回按文本顺序比对.

    不能逐条比对全部消息: 一个回合里的工具调用在服务端是「一次模型请求一条消息」, 在客户端回放里是
    合并后的一个助手气泡, 两侧分组不同, 逐条比对必然错位并把旧内容重新送给模型.
    """
    if consumed:
        index = _first_unconsumed(client, consumed)
        return [] if index is None else list(client[index:])
    return _new_messages_by_text(client, history)


def _new_messages_by_text(client: Sequence[Message], history: Sequence[ModelMessage]) -> list[Message]:
    """按用户文本顺序切分重放; 仅用于没有 id 的旧日志."""
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


def _new_user_inputs(messages: Sequence[Message]) -> list[tuple[str, str]]:
    """本轮客户端新发的用户输入 (重放部分已裁掉), 连同客户端的消息 id."""
    return [
        (_content_text(message.content), message.id)
        for message in messages
        if isinstance(message, UserMessage) and _content_text(message.content)
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


def _request_sent_at(messages: Sequence[ModelMessage]) -> datetime | None:
    """本次请求发出的时刻: 送出的历史以这条 `ModelRequest` 收尾, 据此计算耗时; 末尾不是请求时耗时留空."""
    last = messages[-1]
    return last.timestamp if isinstance(last, ModelRequest) else None


@dataclass
class _RequestUsageRows(AbstractCapability[AgentDeps]):
    """逐请求用量: 每次模型响应完成即写一条回放行.

    该钩子在这次响应的正文与工具调用事件之后、工具执行之前触发, 因此到达顺序就是用量在该回合里的位置;
    等到回合收尾再成批补, 会让整轮开销都堆到最后 (长回合里工具运行了好几轮仍看不到花费).
    """

    store: SessionStore

    async def after_model_request(
        self, ctx: RunContext[AgentDeps], *, request_context: ModelRequestContext, response: ModelResponse
    ) -> ModelResponse:
        usage = request_usage(response, _request_sent_at(request_context.messages))
        await self.store.append_row(RequestUsageRow(type="request_usage", usage=usage))
        return response


@dataclass
class _ReplayRows:
    """把 AG-UI 事件展开为回放行 (页面重建对话用).

    正文与工具回执按事件粒度落盘, 因此切回会话能看到逐条进展, 不必等整个回合结束.
    工具参数在协议里是增量字符串, 这里拼装成对象再落盘, 页面无需二次解析.
    """

    tool_names: dict[str, str] = field(default_factory=dict)
    tool_args: dict[str, str] = field(default_factory=dict)
    usage: RunUsage | None = None

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


async def _follow_rows(store: SessionStore, after: int) -> AsyncIterator[str]:
    """同上, 但发页面契约的行: 页面据此重建气泡与工具卡片, 不认 AG-UI 协议事件.

    与 `/trace` 过同一遍切分, 直播与回放因此同形.
    """
    async for row in _ui_rows(store.follow(after)):
        yield _sse(row.model_dump(mode="json", by_alias=True))


async def _ui_rows(rows: AsyncIterator[TraceRow]) -> AsyncIterator[UiRow]:
    """滤掉协议透传行 (只在 POST 通道分发) 并做正文切分, 与 `/trace` 同一套规则."""
    rewriter = EmbeddedThinkingRows()
    async for row in rows:
        if not isinstance(row, AguiEventRow):
            for out in rewriter.feed(row):
                yield out
    for out in rewriter.close():
        yield out


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
    if not service.try_begin_turn(session_id):
        raise HTTPException(409, detail="会话已有进行中的回合")

    try:
        store = service.store_for(session_id)
        history: list[ModelMessage] = list(store.load_messages() or [])
        adapter = await AGUIAdapter[AgentDeps, str | DeferredToolRequests].from_request(request, agent=agent)
        incoming = _new_messages(list(adapter.run_input.messages), history, _consumed_ids(store.ui_events()))
        adapter.run_input.messages = incoming
        for text, message_id in _new_user_inputs(incoming):
            await store.append_row(UserMessageRow(type="user_message", text=text, message_id=message_id))

        start_seq = store.last_seq
        rows = _ReplayRows()
        deps = service._make_deps(session_id)

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

        async def _on_retry(attempt: int, delay_s: float, reason: str) -> None:
            await store.append_row(
                RetriedRow(type="retried", attempt=attempt, delay_ms=round(delay_s * 1000), reason=reason)
            )

        async def consume() -> None:
            # 失败与取消都要把已产生的往返落盘: 页面已经把这些展示给用户, 历史缺一截就会让下一轮的
            # 上下文与所见内容对不上. 捕获到的是异常路径上已有的部分 (用户提示与被打断的响应,
            # 后者标 state='interrupted'), 框架会在下一轮据此补出中断回执.
            with capture_run_messages() as captured, retry_sink(_on_retry):
                try:
                    async for event in adapter.run_stream(
                        message_history=history,
                        deps=deps,
                        capabilities=[_RequestUsageRows(store=store)],
                        model_settings=resolve_model_settings(
                            service.config, session_thinking=service.session_thinking(session_id)
                        ),
                        usage_limits=UNLIMITED_USAGE,
                        on_complete=on_complete,
                    ):
                        if isinstance(event, RunErrorEvent) and captured:
                            # 适配器把模型失败收成 RUN_ERROR 事件而不是抛出, 捕获列表此时已带着
                            # 这一轮的往返; 先入库, 下一轮才不会少掉用户那句提问
                            store.save_messages(list(captured))
                        if isinstance(event, RunFinishedEvent) and rows.usage is not None:
                            event.usage = [_token_usage(rows.usage)]
                        await store.append_row(
                            AguiEventRow(
                                type="agui",
                                event=event.model_dump(mode="json", by_alias=True, exclude_none=True),
                            )
                        )
                        for row in rows.feed(event):
                            await store.append_row(row)
                except asyncio.CancelledError as exc:
                    cancelled = RunCancelled.from_cancellation(exc)
                    if cancelled is not None:
                        store.save_messages(list(cancelled.all_messages()))
                    await store.append_row(CancelledRow(type="cancelled"))
                    await store.append_row(no_approvals())
                    await runtime.repo.update_agent_session(session_id, status=AgentSessionStatus.ACTIVE)
                    raise
                except Exception as exc:
                    if captured:
                        store.save_messages(list(captured))
                    await store.append_row(AguiEventRow(type="agui", event={"type": "RUN_ERROR", "message": str(exc)}))
                    await store.append_row(ErrorRow(type="error", message=str(exc)))
                    await store.append_row(no_approvals())
                    await runtime.repo.update_agent_session(session_id, status=AgentSessionStatus.ACTIVE)
                finally:
                    store.set_turn_running(False)

        store.set_turn_running(True)
        task = asyncio.create_task(consume(), name=f"agui-turn-{session_id}")
        service.track_turn(session_id, task)
    except BaseException:
        service.abort_turn(session_id)
        raise
    return StreamingResponse(_follow_agui(store, start_seq), media_type=SSE_CONTENT_TYPE)


@router.get(
    "/agent/sessions/{session_id}/agui/events",
    response_class=StreamingResponse,
    responses={200: {"content": {SSE_CONTENT_TYPE: {}}}},
)
async def follow_agent_events(
    session_id: int, service: AgentDep, repo: RepoDep, after_seq: int = 0
) -> StreamingResponse:
    """跟随 ``after_seq`` 之后的回放行, 供页面接上进度: 整段历史经 ``/trace``, 这里只接新行.

    页面发起的回合也经由这条通道 (展示只认回放行), 因此必须能给出起始位置: 否则每接一次都要重发整段历史.

    只订阅, **不**启动回合, 故进行中的回合也不会 409; 回合结束且追平后关闭.
    """
    if await repo.get_agent_session(session_id) is None:
        raise HTTPException(404, detail="会话不存在")
    return StreamingResponse(_follow_rows(service.store_for(session_id), after_seq), media_type=SSE_CONTENT_TYPE)


@router.post("/agent/sessions/{session_id}/agui/cancel")
async def cancel_agui_turn(session_id: int, service: AgentDep) -> AgentCancelResponse:
    """显式终止后台回合: 客户端 abort 只是断开订阅, 回合会继续运行."""
    try:
        cancelled = await service.cancel_turn(session_id)
    except KeyError:
        raise HTTPException(404, detail="会话不存在") from None
    return AgentCancelResponse(cancelled=cancelled)
