"""Agent 会话编排 - 挂在 AppRuntime 上的门面."""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections import OrderedDict
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic_ai import Agent, DeferredToolRequests, DeferredToolResults, ToolDenied
from pydantic_ai.messages import (
    DeferredToolRequestsEvent,
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    PartDeltaEvent,
    TextPart,
    TextPartDelta,
    ToolReturnPart,
    UserPromptPart,
)

from amane.config import AgentConfig, AgentThinkingMode
from amane.db.models import AgentSessionStatus, SavedQueryEntity

from .bridge import AgentRuntimeBridge
from .cache import ResultCache
from .events import (
    AgentStreamEvent,
    StreamCancelled,
    StreamDone,
    StreamError,
    StreamNeedsApproval,
    StreamTextDelta,
    StreamToolCall,
    StreamToolResult,
    TurnTokenUsage,
    truncate_json,
    turn_usage_from_run,
)
from .executor import QueryExecutor
from .runtime import UNLIMITED_USAGE, build_agent, parse_session_thinking, resolve_model_settings
from .sql import ReadonlySqlSandbox
from .tools import AgentDeps, NeedsApprovalPayload, PendingApproval
from .trace import SessionStore, TraceEvent, delete_session_dir, session_dir

if TYPE_CHECKING:
    from amane.db.repository import Repository


@dataclass
class AgentTurnResult:
    reply: str
    saved_query_ids: list[int] = field(default_factory=list)
    needs_approval: NeedsApprovalPayload | None = None
    status: AgentSessionStatus = AgentSessionStatus.ACTIVE
    usage: TurnTokenUsage = field(default_factory=TurnTokenUsage)


@dataclass
class _HistoryEntry:
    messages: list[ModelMessage]
    touched_at: float = field(default_factory=time.monotonic)


@dataclass
class _TurnStreamState:
    """后台回合可取消时共享的部分输出."""

    user_text: str | None
    history: list[ModelMessage]
    reply_parts: list[str] = field(default_factory=list)
    show_user_message: bool = True
    deferred_tool_results: DeferredToolResults | None = None


@dataclass
class AgentService:
    """会话级 Agent 门面: sandbox + cache + agent 工厂 + pending 批准."""

    db_path: Path
    data_dir: Path
    repo: Repository
    cache: ResultCache
    config: AgentConfig
    sandbox: ReadonlySqlSandbox = field(init=False)
    executor: QueryExecutor = field(init=False)
    agent: Agent[AgentDeps, str | DeferredToolRequests] | None = field(init=False, default=None)
    bridge: AgentRuntimeBridge = field(default_factory=AgentRuntimeBridge)
    _pending: dict[str, PendingApproval] = field(default_factory=dict)
    _history: OrderedDict[int, _HistoryEntry] = field(default_factory=OrderedDict)
    _stores: dict[int, SessionStore] = field(default_factory=dict)
    _turn_tasks: dict[int, asyncio.Task[None]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.sandbox = ReadonlySqlSandbox(self.db_path)
        self.executor = QueryExecutor(self.sandbox, self.cache)
        self.rebuild(self.config)

    def rebuild(self, config: AgentConfig) -> None:
        self.config = config
        self.cache.configure(ttl_s=config.result_cache_ttl_s, max_entries=config.result_cache_max_entries)
        self.agent = build_agent(config)
        self._evict_history()

    def store_for(self, session_id: int) -> SessionStore:
        store = self._stores.get(session_id)
        if store is None:
            store = SessionStore(session_dir(self.data_dir, session_id))
            self._stores[session_id] = store
        return store

    def is_turn_running(self, session_id: int) -> bool:
        task = self._turn_tasks.get(session_id)
        if task is not None and not task.done():
            return True
        return self.store_for(session_id).turn_running

    async def create_session(self, title: str = "新会话") -> Any:
        session = await self.repo.create_agent_session(title=title)
        assert session.id is not None
        store = self.store_for(session.id)
        store.write_meta(
            {
                "session_id": session.id,
                "title": session.title,
                "turn_running": False,
                "thinking": None,
            }
        )
        await store.append_row({"type": "session_created", "title": title})
        return session

    def session_thinking(self, session_id: int) -> AgentThinkingMode | None:
        return parse_session_thinking(self.store_for(session_id).read_meta().get("thinking"))

    def set_session_thinking(self, session_id: int, thinking: AgentThinkingMode | None) -> None:
        """写入会话思考覆盖; thinking=None 表示继承全局默认."""
        store = self.store_for(session_id)
        meta = store.read_meta()
        meta["thinking"] = thinking if thinking is not None else None
        store.write_meta(meta)

    async def delete_session(self, session_id: int) -> bool:
        """删除会话; 若回合进行中, 先取消其后台任务"""
        task = self._turn_tasks.pop(session_id, None)
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        ok = await self.repo.delete_agent_session(session_id)
        if ok:
            self._stores.pop(session_id, None)
            self._history.pop(session_id, None)
            self._pending = {k: v for k, v in self._pending.items() if v.session_id != session_id}
            delete_session_dir(self.data_dir, session_id)
        return ok

    def _make_deps(self, session_id: int, store: SessionStore) -> AgentDeps:
        return AgentDeps(
            repo=self.repo,
            executor=self.executor,
            session_id=session_id,
            trace=store,
            sql_timeout_ms=self.config.sql_timeout_ms,
            pending=self._pending,
            persist_tool_trace=False,
            bridge=self.bridge,
        )

    def _evict_history(self) -> None:
        now = time.monotonic()
        ttl = self.config.history_ttl_s
        max_n = self.config.history_max_sessions
        expired = [k for k, v in self._history.items() if now - v.touched_at > ttl]
        for k in expired:
            del self._history[k]
        while len(self._history) > max_n:
            self._history.popitem(last=False)

    def _touch_history(self, session_id: int, messages: list[ModelMessage]) -> None:
        self._history[session_id] = _HistoryEntry(messages=messages)
        self._history.move_to_end(session_id)
        self._evict_history()

    def _load_history(self, session_id: int) -> list[ModelMessage]:
        entry = self._history.get(session_id)
        if entry is not None:
            if time.monotonic() - entry.touched_at > self.config.history_ttl_s:
                del self._history[session_id]
            else:
                entry.touched_at = time.monotonic()
                self._history.move_to_end(session_id)
                return list(entry.messages)
        loaded = self.store_for(session_id).load_messages()
        if loaded is None:
            return []
        self._touch_history(session_id, loaded)
        return list(loaded)

    def _save_history(self, session_id: int, messages: list[ModelMessage]) -> None:
        self.store_for(session_id).save_messages(messages)
        self._touch_history(session_id, messages)

    async def start_turn(
        self,
        session_id: int,
        user_text: str | None,
        *,
        show_user_message: bool = True,
        deferred_tool_results: DeferredToolResults | None = None,
    ) -> int:
        """启动后台回合; 返回启动前的 last_seq (订阅用 after=).

        deferred_tool_results: 批准/拒绝后续跑; 此时 user_text 可为 None.
        """
        if self.agent is None:
            raise RuntimeError("助理 Agent 未配置")
        session = await self.repo.get_agent_session(session_id)
        if session is None:
            raise KeyError(f"session {session_id} 不存在")
        if self.is_turn_running(session_id):
            raise RuntimeError("会话已有进行中的回合")

        store = self.store_for(session_id)
        after = store.last_seq
        store.set_turn_running(True)
        task = asyncio.create_task(
            self._run_turn_background(
                session_id,
                user_text,
                show_user_message=show_user_message,
                deferred_tool_results=deferred_tool_results,
            ),
            name=f"agent-turn-{session_id}",
        )
        self._turn_tasks[session_id] = task

        def _clear(t: asyncio.Task[None]) -> None:
            if self._turn_tasks.get(session_id) is t:
                self._turn_tasks.pop(session_id, None)

        task.add_done_callback(_clear)
        return after

    async def start_approve(self, session_id: int, approval_ids: list[str], *, slow_timeout_ms: int = 60_000) -> int:
        """批准一批 tool_call_id, 以 DeferredToolResults 续跑 (模型无感知批准)."""
        _ = slow_timeout_ms  # 批准后续跑走工具内 approved 路径; 保留参数兼容 API
        if self.is_turn_running(session_id):
            raise RuntimeError("会话已有进行中的回合")
        ids = list(dict.fromkeys(approval_ids))
        valid = [aid for aid in ids if (p := self._pending.get(aid)) is not None and p.session_id == session_id]
        if not valid:
            raise KeyError("批准请求不存在或已过期")

        store = self.store_for(session_id)
        results = DeferredToolResults()
        for aid in valid:
            pending = self._pending.pop(aid)
            assert pending is not None
            store.append(
                TraceEvent(
                    type="approval_granted", payload={"approval_id": aid, "sql": pending.sql, "tool": pending.tool}
                )
            )
            results.approvals[aid] = True

        if self.agent is None:
            after = store.last_seq
            store.set_turn_running(True)
            status = await self._status_after_pending(session_id)
            done = StreamDone(status=status, usage=TurnTokenUsage())
            await store.append_row(done.model_dump(mode="json"))
            store.set_turn_running(False)
            return after
        return await self.start_turn(
            session_id,
            None,
            show_user_message=False,
            deferred_tool_results=results,
        )

    async def start_reject(self, session_id: int, approval_id: str) -> int:
        """拒绝一项待批并以 ToolDenied 续跑."""
        if self.is_turn_running(session_id):
            raise RuntimeError("会话已有进行中的回合")
        pending = self._pending.pop(approval_id, None)
        if pending is None or pending.session_id != session_id:
            raise KeyError("批准请求不存在或已过期")
        store = self.store_for(session_id)
        store.append(
            TraceEvent(
                type="approval_rejected", payload={"approval_id": approval_id, "tool": pending.tool, "sql": pending.sql}
            )
        )
        results = DeferredToolResults()
        results.approvals[approval_id] = ToolDenied(f"操作已取消: {pending.tool}")

        if self.agent is None:
            after = store.last_seq
            store.set_turn_running(True)
            status = await self._status_after_pending(session_id)
            done = StreamDone(status=status)
            await store.append_row(done.model_dump(mode="json"))
            store.set_turn_running(False)
            return after
        return await self.start_turn(
            session_id,
            None,
            show_user_message=False,
            deferred_tool_results=results,
        )

    async def cancel_turn(self, session_id: int) -> bool:
        """显式终止进行中的后台回合; 断连不会走到这里."""
        session = await self.repo.get_agent_session(session_id)
        if session is None:
            raise KeyError(f"session {session_id} 不存在")
        if not self.is_turn_running(session_id):
            return False
        task = self._turn_tasks.get(session_id)
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        elif self.store_for(session_id).turn_running:
            # 无 task (进程异常态): 补写终态并清旗
            await self._write_cancelled(session_id, reply="")
            self.store_for(session_id).set_turn_running(False)
        self._clear_session_pending(session_id)
        return True

    def _clear_session_pending(self, session_id: int) -> None:
        self._pending = {k: v for k, v in self._pending.items() if v.session_id != session_id}

    async def subscribe_events(self, session_id: int, after: int) -> AsyncIterator[dict[str, Any]]:
        store = self.store_for(session_id)
        async for ev in store.follow(after):
            yield ev

    async def _write_cancelled(
        self,
        session_id: int,
        *,
        reply: str,
        history: list[ModelMessage] | None = None,
        user_text: str | None = None,
    ) -> None:
        store = self.store_for(session_id)
        if reply:
            await store.append_row({"type": "assistant_message", "text": reply, "usage": TurnTokenUsage().model_dump()})
        if history is not None and user_text is not None:
            self._save_history(session_id, _messages_for_cancelled(history, user_text, reply))
        await store.append_row(StreamCancelled().model_dump(mode="json"))
        await self.repo.update_agent_session(session_id, status=AgentSessionStatus.ACTIVE)

    async def _run_turn_background(
        self,
        session_id: int,
        user_text: str | None,
        *,
        show_user_message: bool = True,
        deferred_tool_results: DeferredToolResults | None = None,
    ) -> None:
        store = self.store_for(session_id)
        history = self._load_history(session_id)
        state = _TurnStreamState(
            user_text=user_text,
            history=history,
            show_user_message=show_user_message,
            deferred_tool_results=deferred_tool_results,
        )
        try:
            async for event in self._iter_turn_events(session_id, state):
                await store.append_row(event.model_dump(mode="json"))
        except asyncio.CancelledError:
            await self._write_cancelled(
                session_id,
                reply="".join(state.reply_parts),
                history=state.history,
                user_text=state.user_text,
            )
            self._clear_session_pending(session_id)
        except Exception as exc:
            await store.append_row(StreamError(message=str(exc)).model_dump(mode="json"))
        finally:
            store.set_turn_running(False)

    async def _iter_turn_events(self, session_id: int, state: _TurnStreamState) -> AsyncIterator[AgentStreamEvent]:
        if self.agent is None:
            yield StreamError(message="请先在设置中配置 Amane")
            return

        store = self.store_for(session_id)
        if state.user_text is not None and state.show_user_message:
            await store.append_row({"type": "user_message", "text": state.user_text})
        elif state.user_text is not None and not state.show_user_message:
            await store.append_row({"type": "user_message", "text": state.user_text, "hidden": True})

        deps = self._make_deps(session_id, store)
        usage = TurnTokenUsage()

        emitted_deferred = False
        try:
            async with self.agent.run_stream_events(
                state.user_text,
                deps=deps,
                message_history=state.history,
                deferred_tool_results=state.deferred_tool_results,
                model_settings=resolve_model_settings(self.config, session_thinking=self.session_thinking(session_id)),
                usage_limits=UNLIMITED_USAGE,
            ) as stream:
                async for ev in stream:
                    if isinstance(ev, DeferredToolRequestsEvent):
                        async for item in self._emit_deferred_approvals(session_id, deps, ev.requests):
                            yield item
                        emitted_deferred = True
                        continue
                    mapped = _map_pai_event(ev)
                    if mapped is not None:
                        if isinstance(mapped, StreamTextDelta):
                            state.reply_parts.append(mapped.text)
                        yield mapped
                if stream.result is not None:
                    messages = list(stream.result.all_messages())
                    self._save_history(session_id, messages)
                    out = stream.result.output
                    if isinstance(out, DeferredToolRequests) and not emitted_deferred:
                        async for item in self._emit_deferred_approvals(session_id, deps, out):
                            yield item
                    elif isinstance(out, str) and out and not state.reply_parts:
                        state.reply_parts.append(out)
                        yield StreamTextDelta(text=out)
                    usage = turn_usage_from_run(stream.result.usage)
                else:
                    usage = turn_usage_from_run(stream.usage)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            yield StreamError(message=str(exc))
            return

        reply = "".join(state.reply_parts)
        if reply or usage.input or usage.output or usage.cache_read or usage.cache_write:
            await store.append_row({"type": "assistant_message", "text": reply, "usage": usage.model_dump()})
        status = await self._finalize_status(session_id, deps)
        yield StreamDone(saved_query_ids=list(deps.last_saved_query_ids), status=status, usage=usage)

    async def _emit_deferred_approvals(
        self, session_id: int, deps: AgentDeps, requests: DeferredToolRequests
    ) -> AsyncIterator[AgentStreamEvent]:
        """把 DeferredToolRequests 登记为 pending 并推送 needs_approval SSE."""
        last: NeedsApprovalPayload | None = None
        for call in requests.approvals:
            tid = call.tool_call_id
            meta = requests.metadata.get(tid) or {}
            if isinstance(meta.get("sql"), str) and meta["sql"]:
                sql = meta["sql"]
            elif isinstance(call.args, str):
                sql = call.args
            else:
                sql = call.tool_name
            tool = str(meta.get("tool") or call.tool_name)
            entity_raw = meta.get("entity")
            entity: SavedQueryEntity | None = None
            if isinstance(entity_raw, str):
                try:
                    entity = SavedQueryEntity(entity_raw)
                except ValueError:
                    entity = None
            name = meta.get("name") if isinstance(meta.get("name"), str) else None
            create_view = bool(meta.get("create_view", False))
            raw_extra = meta.get("extra")
            extra: dict[str, Any] = {str(k): v for k, v in raw_extra.items()} if isinstance(raw_extra, dict) else {}
            self._pending[tid] = PendingApproval(
                approval_id=tid,
                session_id=session_id,
                sql=sql,
                tool=tool,
                entity=entity,
                name=name,
                create_view=create_view,
                extra=extra,
            )
            payload = NeedsApprovalPayload(
                approval_id=tid,
                sql=sql,
                tool=tool,
                entity=entity,
                name=name,
                create_view=create_view,
            )
            last = payload
            yield StreamNeedsApproval.model_validate({**payload.model_dump(mode="json"), "type": "needs_approval"})
        deps.awaiting_approval = last

    async def _status_after_pending(self, session_id: int) -> AgentSessionStatus:
        still = any(p.session_id == session_id for p in self._pending.values())
        status = AgentSessionStatus.AWAITING_APPROVAL if still else AgentSessionStatus.ACTIVE
        await self.repo.update_agent_session(session_id, status=status)
        return status

    async def _finalize_status(self, session_id: int, deps: AgentDeps) -> AgentSessionStatus:
        still = deps.awaiting_approval is not None or any(p.session_id == session_id for p in self._pending.values())
        status = AgentSessionStatus.AWAITING_APPROVAL if still else AgentSessionStatus.ACTIVE
        await self.repo.update_agent_session(session_id, status=status)
        return status


def _messages_for_cancelled(history: list[ModelMessage], user_text: str, reply: str) -> list[ModelMessage]:
    """取消后把本轮用户消息与已生成片段写入权威历史, 避免下一轮丢上下文."""
    messages = list(history)
    messages.append(ModelRequest(parts=[UserPromptPart(content=user_text)]))
    messages.append(ModelResponse(parts=[TextPart(content=reply or "（已终止）")]))
    return messages


def _map_pai_event(ev: Any) -> AgentStreamEvent | None:
    if isinstance(ev, PartDeltaEvent) and isinstance(ev.delta, TextPartDelta):
        text = ev.delta.content_delta
        if text:
            return StreamTextDelta(text=text)
        return None
    if isinstance(ev, FunctionToolCallEvent):
        return StreamToolCall(tool_call_id=ev.tool_call_id, name=ev.part.tool_name, args=truncate_json(ev.part.args))
    if isinstance(ev, FunctionToolResultEvent):
        part = ev.part
        name = part.tool_name if isinstance(part, ToolReturnPart) else (part.tool_name or "unknown")
        return StreamToolResult(tool_call_id=ev.tool_call_id, name=name, result=truncate_json(part.content))
    return None
