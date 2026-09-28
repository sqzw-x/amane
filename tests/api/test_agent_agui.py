"""AG-UI 端点: 事件映射、后台回合与审批续执行, 由 FunctionModel 的 stream_function 驱动, 不触网."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable, Sequence
from typing import TYPE_CHECKING, Any

import pytest
from pydantic_ai import Agent, DeferredToolRequests, RunContext
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, TextPart, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import (
    AgentInfo,
    DeltaThinkingCalls,
    DeltaThinkingPart,
    DeltaToolCall,
    DeltaToolCalls,
    FunctionModel,
)
from pydantic_ai.toolsets import AbstractToolset, FunctionToolset

from amane.agent.rows import (
    AguiEventRow,
    ApprovalsRow,
    ReasoningDeltaRow,
    RequestUsageRow,
    TextDeltaRow,
    ToolCallRow,
    ToolResultRow,
    TurnUsageRow,
    UserMessageRow,
)
from amane.agent.service import AgentService
from amane.agent.tools import AgentDeps, build_explore_toolset, require_approval
from amane.agent.trace import SessionStore
from amane.api.routes import agent_agui
from amane.db.models import AgentSessionStatus
from amane.db.repository import Repository

if TYPE_CHECKING:
    from fastapi import FastAPI
    from httpx2 import AsyncClient

StreamItem = str | DeltaToolCalls | DeltaThinkingCalls
StreamRespond = Callable[[list[ModelMessage], AgentInfo], AsyncIterator[StreamItem]]


def _sse_events(raw: str) -> list[dict[str, Any]]:
    """AG-UI 事件按 ``data: {json}`` 分帧."""
    return [
        json.loads(line[5:].strip())
        for chunk in raw.split("\n\n")
        for line in chunk.splitlines()
        if line.startswith("data:")
    ]


def _types(events: list[dict[str, Any]]) -> list[str]:
    return [str(e["type"]) for e in events]


def _of(events: list[dict[str, Any]], *types: str) -> dict[str, Any]:
    return next(e for e in events if e["type"] in types)


def _tool_returns(messages: Sequence[ModelMessage]) -> list[ToolReturnPart]:
    return [p for m in messages if isinstance(m, ModelRequest) for p in m.parts if isinstance(p, ToolReturnPart)]


def _user_prompts(messages: Sequence[ModelMessage]) -> list[str]:
    return [
        p.content
        for m in messages
        if isinstance(m, ModelRequest)
        for p in m.parts
        if isinstance(p, UserPromptPart) and isinstance(p.content, str)
    ]


def _text_stream(*chunks: str) -> StreamRespond:
    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[StreamItem]:
        for chunk in chunks:
            yield chunk

    return stream


def _call(*, name: str, args: dict[str, Any], call_id: str) -> DeltaToolCalls:
    return {0: DeltaToolCall(name=name, json_args=json.dumps(args), tool_call_id=call_id)}


def _call_then_text(*, name: str, args: dict[str, Any], call_id: str, text: str) -> StreamRespond:
    """先流式发出一次工具调用; 拿到工具回执后再出正文."""

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[StreamItem]:
        if _tool_returns(messages):
            yield text
        else:
            yield _call(name=name, args=args, call_id=call_id)

    return stream


def _thinking_stream(content: str) -> StreamRespond:
    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[StreamItem]:
        yield {0: DeltaThinkingPart(content=content)}

    return stream


def _install_agent(service: AgentService, stream: StreamRespond, *toolsets: AbstractToolset[AgentDeps]) -> None:
    """把脚本化模型装进运行中的 AgentService (绕开需要 api_key 的 build_agent)."""
    service.agent = Agent[AgentDeps, str | DeferredToolRequests](
        FunctionModel(stream_function=stream),
        deps_type=AgentDeps,
        output_type=[str, DeferredToolRequests],
        toolsets=list(toolsets),
    )


async def _run(
    client: AsyncClient,
    session_id: int,
    messages: list[dict[str, Any]],
    *,
    resume: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    body: dict[str, Any] = {
        "threadId": f"session-{session_id}",
        "runId": "run-1",
        "state": None,
        "messages": messages,
        "tools": [],
        "context": [],
        "forwardedProps": {},
    }
    if resume is not None:
        body["resume"] = resume
    resp = await client.post(f"/agent/sessions/{session_id}/agui", json=body, headers={"Accept": "text/event-stream"})
    assert resp.status_code == 200, resp.text
    assert "text/event-stream" in resp.headers["content-type"]
    return _sse_events(resp.text)


def _user(text: str, mid: str = "u1") -> dict[str, Any]:
    return {"id": mid, "role": "user", "content": text}


def _call_message(call_id: str, name: str, args: dict[str, Any]) -> dict[str, Any]:
    """客户端重放的助手消息 (带工具调用)."""
    return {
        "id": "a1",
        "role": "assistant",
        "content": None,
        "toolCalls": [{"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}],
    }


async def service_of(app: FastAPI, client: AsyncClient) -> AgentService:
    """``client`` 仅用于确保 lifespan 已进入."""
    service = app.state.runtime.agent_service
    assert isinstance(service, AgentService)
    return service


@pytest.mark.asyncio
async def test_agui_requires_configured_agent(client: AsyncClient, repo: Repository) -> None:
    """未配置 API key (service.agent 为 None) 时 503."""
    session = await repo.create_agent_session()
    assert session.id is not None
    resp = await client.post(f"/agent/sessions/{session.id}/agui", json={})
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_agui_missing_session_404(app: FastAPI, client: AsyncClient) -> None:
    service = await service_of(app, client)
    _install_agent(service, _text_stream("x"))
    resp = await client.post("/agent/sessions/999999/agui", json={})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_text_stream_and_history_persisted(app: FastAPI, client: AsyncClient, repo: Repository) -> None:
    service = await service_of(app, client)
    session = await service.create_session(title="agui")
    assert session.id is not None
    _install_agent(service, _text_stream("你好，", "世界"))

    events = await _run(client, session.id, [_user("在吗")])

    assert _types(events) == [
        "RUN_STARTED",
        "TEXT_MESSAGE_START",
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_END",
        "RUN_FINISHED",
    ], "事件序列不符"
    assert events[0]["threadId"] == f"session-{session.id}"
    assert events[0]["runId"] == "run-1"
    assert "".join(str(e["delta"]) for e in events if e["type"] == "TEXT_MESSAGE_CONTENT") == "你好，世界"
    assert events[-1]["outcome"] == {"type": "success"}

    # 回合结束回存服务端历史, 并清掉 turn_running
    history = service.store_for(session.id).load_messages()
    assert history is not None
    assert any(isinstance(p, TextPart) and p.content == "你好，世界" for m in history for p in m.parts)
    assert not service.is_turn_running(session.id)
    stored = await repo.get_agent_session(session.id)
    assert stored is not None and stored.status is AgentSessionStatus.ACTIVE

    # 回放行同步落盘: 客户端 thread 切走即丢, 只能靠它回填
    rows = service.store_for(session.id).read_events()
    assert [row.text for row in rows if isinstance(row, UserMessageRow)] == ["在吗"]
    assert "".join(row.text for row in rows if isinstance(row, TextDeltaRow)) == "你好，世界"
    assert [row.text for row in rows if isinstance(row, TextDeltaRow)][:1] == ["你好，"]  # 正文按增量落, 切回即可见进展
    assert len([row for row in rows if isinstance(row, TurnUsageRow)]) == 1


@pytest.mark.asyncio
async def test_tool_call_lifecycle_events(app: FastAPI, client: AsyncClient) -> None:
    """真实工具集 (sql_explore) 经 AG-UI 工具事件全生命周期往返."""
    service = await service_of(app, client)
    session = await service.create_session(title="tool")
    assert session.id is not None
    _install_agent(
        service,
        _call_then_text(name="sql_explore", args={"sql": "SELECT 1 AS n"}, call_id="call-1", text="统计完成"),
        build_explore_toolset(),
    )

    events = await _run(client, session.id, [_user("数一下")])

    start = _of(events, "TOOL_CALL_START")
    assert start["toolCallName"] == "sql_explore"
    assert start["toolCallId"] == "call-1"
    assert _of(events, "TOOL_CALL_ARGS")["delta"] == '{"sql": "SELECT 1 AS n"}'
    assert _of(events, "TOOL_CALL_END")["toolCallId"] == "call-1"
    result = _of(events, "TOOL_CALL_RESULT")
    assert result["toolCallId"] == "call-1"
    assert json.loads(result["content"])["columns"] == ["n"]
    assert "".join(str(e["delta"]) for e in events if e["type"] == "TEXT_MESSAGE_CONTENT") == "统计完成"
    assert events[-1]["outcome"] == {"type": "success"}

    # 工具事件同样落盘, 切回会话时 UI 才能重建工具卡片 (参数已解析成对象)
    rows = service.store_for(session.id).read_events()
    calls = [row for row in rows if isinstance(row, (ToolCallRow, ToolResultRow))]
    assert [row.type for row in calls] == ["tool_call", "tool_result"]
    assert isinstance(calls[0], ToolCallRow)
    assert (calls[0].tool_call_id, calls[0].name, calls[0].args) == ("call-1", "sql_explore", {"sql": "SELECT 1 AS n"})

    # 逐请求用量随回合末尾补: 出工具那次挂在工具之后, 收尾正文那次落在末尾
    usage_rows = [row.usage for row in rows if isinstance(row, RequestUsageRow)]
    assert [u.after_tool_call for u in usage_rows] == ["call-1", None]
    assert all(isinstance(u.duration_ms, int) for u in usage_rows)
    assert usage_rows[0].output > 0


@pytest.mark.asyncio
async def test_reasoning_events(app: FastAPI, client: AsyncClient) -> None:
    service = await service_of(app, client)
    session = await service.create_session(title="thinking")
    assert session.id is not None
    _install_agent(service, _thinking_stream("先看库结构"))

    events = await _run(client, session.id, [_user("有多少条")])
    types = _types(events)

    assert "REASONING_START" in types
    assert "REASONING_MESSAGE_CONTENT" in types
    assert "REASONING_MESSAGE_END" in types
    assert "REASONING_END" in types
    assert _of(events, "REASONING_MESSAGE_CONTENT")["delta"] == "先看库结构"

    # 思考同样落回放行, 折叠的思考块在重放 (切会话 / 刷新) 后仍能还原
    # 次数不作断言: 模型只回思考不回正文时框架会重试, 每次都重发一遍思考
    rows = service.store_for(session.id).read_events()
    deltas = [row for row in rows if isinstance(row, ReasoningDeltaRow)]
    assert {row.text for row in deltas} == {"先看库结构"}
    assert all(row.block_id for row in deltas), "归块要用协议给的 id, 前端不靠相邻关系拼接"


@pytest.mark.asyncio
async def test_run_finished_usage_backfilled(app: FastAPI, client: AsyncClient) -> None:
    """RUN_FINISHED 的 usage 由端点填充 (官方适配器留空)."""
    service = await service_of(app, client)
    session = await service.create_session(title="usage")
    assert session.id is not None
    _install_agent(service, _text_stream("你好"))

    events = await _run(client, session.id, [_user("在吗")])

    assert _types(events) == [
        "RUN_STARTED",
        "TEXT_MESSAGE_START",
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_END",
        "RUN_FINISHED",
    ]
    assert events[1]["messageId"]  # camelCase: 与官方编码器的线上形状一致
    usage = events[-1]["usage"]
    assert len(usage) == 1
    assert usage[0]["outputTokens"] > 0
    assert usage[0]["totalTokens"] == usage[0]["inputTokens"] + usage[0]["outputTokens"]


@pytest.mark.asyncio
async def test_disconnect_does_not_cancel_turn(app: FastAPI, client: AsyncClient) -> None:
    """客户端中途断开只结束订阅, 回合继续跑完并落盘."""
    service = await service_of(app, client)
    session = await service.create_session(title="disconnect")
    assert session.id is not None
    _install_agent(service, _text_stream("跑完了"))

    body: dict[str, Any] = {
        "threadId": str(session.id),
        "runId": "run-1",
        "state": None,
        "messages": [_user("在吗")],
        "tools": [],
        "context": [],
        "forwardedProps": {},
    }
    async with client.stream(
        "POST", f"/agent/sessions/{session.id}/agui", json=body, headers={"Accept": "text/event-stream"}
    ) as resp:
        assert resp.status_code == 200
        async for line in resp.aiter_lines():
            if line.startswith("data:"):
                break  # 收到首个事件即断开

    for _ in range(200):
        if not service.is_turn_running(session.id):
            break
        await asyncio.sleep(0.01)

    rows = service.store_for(session.id).read_events()
    assert "".join(row.text for row in rows if isinstance(row, TextDeltaRow)) == "跑完了"
    assert any(isinstance(row, TurnUsageRow) for row in rows)


@pytest.mark.asyncio
async def test_replayed_transcript_reaches_model_once(app: FastAPI, client: AsyncClient) -> None:
    """客户端重放整段会话时, 服务端历史去重后模型只收到一份."""
    seen: list[list[ModelMessage]] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[StreamItem]:
        seen.append(list(messages))
        yield f"第{len(seen)}答"

    service = await service_of(app, client)
    session = await service.create_session(title="replay")
    assert session.id is not None
    _install_agent(service, stream)

    await _run(client, session.id, [_user("第一轮")])
    events = await _run(
        client,
        session.id,
        [
            _user("第一轮"),
            {"id": "a1", "role": "assistant", "content": "第1答"},
            _user("第二轮"),
        ],
    )

    assert events[-1]["outcome"] == {"type": "success"}
    assert _user_prompts(seen[-1]) == ["第一轮", "第二轮"]


@pytest.mark.asyncio
async def test_tool_loop_replay_is_trimmed_by_user_message(app: FastAPI, client: AsyncClient) -> None:
    """带工具调用的回合: 客户端重放整段会话时, 只送出新的一条用户消息.

    服务端把工具回合拆成「一次模型请求一条消息」, 客户端回放却是合并后的一个助手气泡; 若逐条比对
    全部消息, 会在第 2 条错位, 把上一条提问与回答重新当作新消息送给模型.
    """
    seen: list[list[ModelMessage]] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[StreamItem]:
        seen.append(list(messages))
        if _tool_returns(messages):
            yield "统计完成"
        else:
            yield _call(name="sql_explore", args={"sql": "SELECT 1 AS n"}, call_id="call-1")

    service = await service_of(app, client)
    session = await service.create_session(title="loop-replay")
    assert session.id is not None
    _install_agent(service, stream, build_explore_toolset())

    await _run(client, session.id, [_user("数一下")])
    events = await _run(
        client,
        session.id,
        [
            _user("数一下"),
            {
                "id": "a1",
                "role": "assistant",
                "content": "统计完成",
                "toolCalls": [
                    {"id": "call-1", "type": "function", "function": {"name": "sql_explore", "arguments": "{}"}}
                ],
            },
            {"id": "r1", "role": "tool", "toolCallId": "call-1", "content": "{}"},
            _user("再来一次", "u2"),
        ],
    )

    assert events[-1]["outcome"] == {"type": "success"}
    final = seen[-1]
    assert _user_prompts(final) == ["数一下", "再来一次"]
    texts = [
        p.content
        for m in final
        if isinstance(m, (ModelRequest, ModelResponse))
        for p in m.parts
        if isinstance(p, TextPart) and isinstance(p.content, str)
    ]
    assert texts.count("统计完成") == 1


@pytest.mark.asyncio
async def test_approval_interrupt_and_resume(app: FastAPI, client: AsyncClient, repo: Repository) -> None:
    """工具内 ``require_approval`` → RUN_FINISHED 中断; ``resume[]`` 批准后续执行."""
    ran: list[str] = []
    toolset: FunctionToolset[AgentDeps] = FunctionToolset()

    @toolset.tool
    async def delete_metadata(ctx: RunContext[AgentDeps], target: str) -> str:
        """删除一条元数据 (需审批)."""
        require_approval(ctx, sql=f"DELETE FROM metadata WHERE id = {target}", tool="delete_metadata", name="删除")
        ran.append(target)
        return "OK"

    service = await service_of(app, client)
    session = await service.create_session(title="approval")
    assert session.id is not None
    _install_agent(
        service,
        _call_then_text(name="delete_metadata", args={"target": "1"}, call_id="call-9", text="删除完成"),
        toolset,
    )

    events = await _run(client, session.id, [_user("删掉 1")])
    finish = events[-1]
    assert finish["type"] == "RUN_FINISHED"
    outcome = finish["outcome"]
    assert outcome["type"] == "interrupt"
    interrupt = outcome["interrupts"][0]
    assert interrupt["toolCallId"] == "call-9"
    assert interrupt["metadata"]["sql"] == "DELETE FROM metadata WHERE id = 1"
    assert interrupt["metadata"]["tool"] == "delete_metadata"
    assert interrupt["metadata"]["reason"] == "allow_slow"
    assert ran == []  # 未批准不执行
    stored = await repo.get_agent_session(session.id)
    assert stored is not None and stored.status is AgentSessionStatus.AWAITING_APPROVAL

    # 待批态落成一条快照行; 刷新后页面据此恢复审批入口, 故中断字段必须是库读得懂的 camelCase
    trace = (await client.get(f"/agent/sessions/{session.id}/trace")).json()["events"]
    pending = next(row for row in reversed(trace) if row["type"] == "approvals")["interrupts"]
    assert [item["toolCallId"] for item in pending] == ["call-9"]
    assert pending[0]["metadata"]["sql"] == "DELETE FROM metadata WHERE id = 1"

    # 批准: 客户端重放被打断的回合 + resume[]
    approved = await _run(
        client,
        session.id,
        [_user("删掉 1"), _call_message("call-9", "delete_metadata", {"target": "1"})],
        resume=[{"interruptId": interrupt["id"], "status": "resolved", "payload": {"approved": True}}],
    )
    assert ran == ["1"]
    assert approved[-1]["outcome"] == {"type": "success"}
    assert _of(approved, "TOOL_CALL_RESULT")["content"] == "OK"
    stored = await repo.get_agent_session(session.id)
    assert stored is not None and stored.status is AgentSessionStatus.ACTIVE

    # 续跑成功的那次回合发出空快照: 待批态由后一条覆盖, 页面重放不会停在旧的审批上
    snapshots = [row for row in service.store_for(session.id).read_events() if isinstance(row, ApprovalsRow)]
    assert [len(row.interrupts) for row in snapshots] == [1, 0]


@pytest.mark.asyncio
async def test_approval_denied_via_resume(app: FastAPI, client: AsyncClient) -> None:
    """resume 的 payload 不是 ``approved=true`` 时按拒绝处理 (deny-by-default)."""
    ran: list[str] = []
    toolset: FunctionToolset[AgentDeps] = FunctionToolset()

    @toolset.tool
    async def delete_metadata(ctx: RunContext[AgentDeps], target: str) -> str:
        """删除一条元数据 (需审批)."""
        require_approval(ctx, sql=f"DELETE FROM metadata WHERE id = {target}", tool="delete_metadata")
        ran.append(target)
        return "OK"

    service = await service_of(app, client)
    session = await service.create_session(title="deny")
    assert session.id is not None
    _install_agent(
        service,
        _call_then_text(name="delete_metadata", args={"target": "2"}, call_id="call-8", text="已取消"),
        toolset,
    )

    events = await _run(client, session.id, [_user("删掉 2")])
    interrupt = events[-1]["outcome"]["interrupts"][0]

    denied = await _run(
        client,
        session.id,
        [_user("删掉 2"), _call_message("call-8", "delete_metadata", {"target": "2"})],
        resume=[
            {"interruptId": interrupt["id"], "status": "resolved", "payload": {"approved": False, "reason": "不删"}}
        ],
    )
    assert ran == []
    assert denied[-1]["outcome"] == {"type": "success"}
    assert "不删" in _of(denied, "TOOL_CALL_RESULT")["content"]


@pytest.mark.asyncio
async def test_follow_replays_rows_from_scratch(app: FastAPI, client: AsyncClient) -> None:
    """跟随端点只订阅: 回合未跑时回放全部回放行就关闭, 不启动回合."""
    service = await service_of(app, client)
    session = await service.create_session(title="follow")
    assert session.id is not None
    store = service.store_for(session.id)
    await store.append_row(TextDeltaRow(type="text_delta", block_id="m1", text="上半"))
    await store.append_row(AguiEventRow(type="agui", event={"type": "RUN_FINISHED"}))

    resp = await client.get(f"/agent/sessions/{session.id}/agui/events")
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]
    # 发的是回放行本身 (agui 行连载荷原样), 与 POST 通道的 AG-UI 投影不同
    assert [(r["type"], r["seq"]) for r in _sse_events(resp.text)] == [("text_delta", 1), ("agui", 2)]

    assert (await client.get("/agent/sessions/999999/agui/events")).status_code == 404


@pytest.mark.asyncio
async def test_follow_attaches_to_running_turn_without_starting_one(
    app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """回合进行中也能订阅: 跟随不启动回合 (照 POST 的做法会 409), 且回合收尾的行照样发得出来."""
    release = asyncio.Event()
    entered = asyncio.Event()
    real_follow = agent_agui._follow_rows

    async def spy(store: SessionStore) -> AsyncIterator[str]:
        entered.set()
        async for chunk in real_follow(store):
            yield chunk

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[StreamItem]:
        yield "上半"
        await release.wait()
        yield "下半"

    service = await service_of(app, client)
    session = await service.create_session(title="follow-live")
    assert session.id is not None
    _install_agent(service, stream)
    monkeypatch.setattr(agent_agui, "_follow_rows", spy)

    run_task = asyncio.create_task(_run(client, session.id, [_user("在吗")]))
    for _ in range(200):
        if service.is_turn_running(session.id):
            break
        await asyncio.sleep(0.01)
    assert service.is_turn_running(session.id), "回合未起来"

    follow_task = asyncio.create_task(client.get(f"/agent/sessions/{session.id}/agui/events"))
    await asyncio.wait_for(entered.wait(), timeout=5)  # 订阅已建立, 而回合仍在跑
    assert service.is_turn_running(session.id), "订阅不应影响进行中的回合"

    release.set()
    resp = await asyncio.wait_for(follow_task, timeout=5)
    await asyncio.wait_for(run_task, timeout=5)

    assert resp.status_code == 200
    rows = _sse_events(resp.text)
    assert [r["type"] for r in rows if r["type"] == "user_message"] == ["user_message"]
    assert "".join(str(r["text"]) for r in rows if r["type"] == "text_delta") == "上半下半"
