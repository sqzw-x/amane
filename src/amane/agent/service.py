from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from ..config import AgentConfig, AgentThinkingMode
from ..db.models import DEFAULT_SESSION_TITLE, AgentSession, AgentSessionStatus
from ..parsing import EMPTY_NUMBER_RULES, NumberRules
from .bridge import AgentRuntimeBridge
from .cache import ResultCache
from .executor import QueryExecutor
from .naming import generate_title
from .rows import CancelledRow, no_approvals
from .runtime import build_agent, build_model, parse_session_thinking
from .sql import ReadonlySqlSandbox
from .tools import AgentDeps
from .trace import SessionStore, delete_session_dir, session_dir

if TYPE_CHECKING:
    from pydantic_ai import Agent, DeferredToolRequests
    from pydantic_ai.models import Model

    from ..db.repository import Repository


@dataclass
class AgentService:
    db_path: Path
    data_dir: Path
    repo: Repository
    cache: ResultCache
    config: AgentConfig
    sandbox: ReadonlySqlSandbox = field(init=False)
    executor: QueryExecutor = field(init=False)
    agent: Agent[AgentDeps, str | DeferredToolRequests] | None = field(init=False, default=None)
    naming_model: Model | None = field(init=False, default=None)
    bridge: AgentRuntimeBridge = field(default_factory=AgentRuntimeBridge)
    number_rules: NumberRules = EMPTY_NUMBER_RULES
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
        self.naming_model = build_model(config) if config.api_key else None

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

    def track_turn(self, session_id: int, task: asyncio.Task[None]) -> None:
        """登记后台回合任务; 取消与删除会话据此终止它."""
        self._turn_tasks[session_id] = task

        def _clear(finished: asyncio.Task[None]) -> None:
            if self._turn_tasks.get(session_id) is finished:
                self._turn_tasks.pop(session_id, None)

        task.add_done_callback(_clear)

    async def create_session(self, title: str = DEFAULT_SESSION_TITLE) -> AgentSession:
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
        return session

    async def name_session(self, session_id: int, prompt: str) -> str:
        """按首条用户输入生成标题并写回. 调用方与主回合并行执行, 不等待回合结束."""
        title = await generate_title(self.naming_model, prompt)
        if await self.repo.update_agent_session(session_id, title=title) is None:
            return title
        store = self.store_for(session_id)
        meta = store.read_meta()
        meta["title"] = title
        store.write_meta(meta)
        return title

    def session_thinking(self, session_id: int) -> AgentThinkingMode | None:
        return parse_session_thinking(self.store_for(session_id).read_meta().get("thinking"))

    def set_session_thinking(self, session_id: int, thinking: AgentThinkingMode | None) -> None:
        """thinking=None 表示继承全局默认."""
        store = self.store_for(session_id)
        meta = store.read_meta()
        meta["thinking"] = thinking if thinking is not None else None
        store.write_meta(meta)

    async def delete_session(self, session_id: int) -> bool:
        """回合进行中则先取消其后台任务."""
        task = self._turn_tasks.pop(session_id, None)
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        # 回合已取消, 不会再新增未保留预设; 这些行随会话删除且删除路径不回传 id, 故先快照供缓存失效
        ephemeral_ids = [
            q.id
            for q in await self.repo.list_saved_queries(session_id=session_id)
            if q.id is not None and not q.persisted
        ]
        ok = await self.repo.delete_agent_session(session_id)
        if ok:
            for query_id in ephemeral_ids:
                self.cache.invalidate(query_id)
            self._stores.pop(session_id, None)
            delete_session_dir(self.data_dir, session_id)
        return ok

    def _make_deps(self, session_id: int) -> AgentDeps:
        return AgentDeps(
            repo=self.repo,
            executor=self.executor,
            session_id=session_id,
            sql_timeout_ms=self.config.sql_timeout_ms,
            bridge=self.bridge,
            number_rules=self.number_rules,
        )

    async def cancel_turn(self, session_id: int) -> bool:
        """显式终止后台回合; 断连不会进入此路径."""
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
        else:
            # 无任务 (进程异常态): 补写终态并清除 turn_running
            await self._write_cancelled(session_id)
        return True

    async def _write_cancelled(self, session_id: int) -> None:
        store = self.store_for(session_id)
        await store.append_row(CancelledRow(type="cancelled"))
        await store.append_row(no_approvals())
        await self.repo.update_agent_session(session_id, status=AgentSessionStatus.ACTIVE)
        store.set_turn_running(False)
