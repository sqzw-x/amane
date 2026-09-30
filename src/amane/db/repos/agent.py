from __future__ import annotations

from typing import Unpack

from sqlmodel import col, select

from ..models import DEFAULT_SESSION_TITLE, AgentSession, AgentSessionStatus, SavedQuery, SavedQueryEntity
from ..repo_types import SavedQueryUpdates, _utcnow
from .base import RepositoryMixinBase


class AgentRepoMixin(RepositoryMixinBase):
    async def create_agent_session(self, title: str = DEFAULT_SESSION_TITLE) -> AgentSession:
        async with self._session() as session:
            row = AgentSession(title=title, status=AgentSessionStatus.ACTIVE)
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row

    async def list_agent_sessions(self) -> list[AgentSession]:
        async with self._session() as session:
            stmt = select(AgentSession).order_by(col(AgentSession.updated_at).desc())
            result = await session.exec(stmt)
            return list(result.all())

    async def get_agent_session(self, session_id: int) -> AgentSession | None:
        async with self._session() as session:
            return await session.get(AgentSession, session_id)

    async def update_agent_session(
        self,
        session_id: int,
        *,
        title: str | None = None,
        status: AgentSessionStatus | None = None,
    ) -> AgentSession | None:
        async with self._session() as session:
            row = await session.get(AgentSession, session_id)
            if row is None:
                return None
            if title is not None:
                row.title = title
            if status is not None:
                row.status = status
            row.updated_at = _utcnow()
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row

    async def delete_agent_session(self, session_id: int) -> bool:
        """未 persist 的 saved_query 一并删除; 已 persist 的保留并将 session_id 置空.
        无 ORM relationship, 须先 flush 子表再删会话, 否则 FK 会 IntegrityError.
        """
        async with self._session() as session:
            row = await session.get(AgentSession, session_id)
            if row is None:
                return False
            sq_stmt = select(SavedQuery).where(col(SavedQuery.session_id) == session_id)
            for sq in (await session.exec(sq_stmt)).all():
                if sq.persisted:
                    sq.session_id = None
                    sq.updated_at = _utcnow()
                    session.add(sq)
                else:
                    await session.delete(sq)
            await session.flush()
            await session.delete(row)
            await session.commit()
            return True

    async def create_saved_query(
        self,
        *,
        name: str,
        sql: str,
        entity: SavedQueryEntity,
        description: str = "",
        session_id: int | None = None,
        persisted: bool = False,
    ) -> SavedQuery:
        async with self._session() as session:
            row = SavedQuery(
                name=name,
                sql=sql,
                entity=entity,
                description=description,
                session_id=session_id,
                persisted=persisted,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row

    async def get_saved_query(self, query_id: int) -> SavedQuery | None:
        async with self._session() as session:
            return await session.get(SavedQuery, query_id)

    async def list_saved_queries(
        self, *, session_id: int | None = None, persisted_only: bool = False
    ) -> list[SavedQuery]:
        async with self._session() as session:
            stmt = select(SavedQuery)
            if session_id is not None:
                stmt = stmt.where(col(SavedQuery.session_id) == session_id)
            if persisted_only:
                stmt = stmt.where(col(SavedQuery.persisted).is_(True))
            stmt = stmt.order_by(col(SavedQuery.updated_at).desc())
            result = await session.exec(stmt)
            return list(result.all())

    async def update_saved_query(
        self,
        query_id: int,
        **updates: Unpack[SavedQueryUpdates],
    ) -> SavedQuery | None:
        async with self._session() as session:
            row = await session.get(SavedQuery, query_id)
            if row is None:
                return None
            if "name" in updates:
                row.name = updates["name"]
            if "description" in updates:
                row.description = updates["description"]
            if "sql" in updates:
                row.sql = updates["sql"]
            row.updated_at = _utcnow()
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row

    async def delete_saved_queries(self, ids: list[int]) -> tuple[int, int]:
        """重复 id 只处理一次; 不存在的 id 计入 missing."""
        async with self._session() as session:
            deleted = 0
            missing = 0
            for query_id in dict.fromkeys(ids):
                row = await session.get(SavedQuery, query_id)
                if row is None:
                    missing += 1
                    continue
                await session.delete(row)
                deleted += 1
            await session.commit()
            return deleted, missing

    async def persist_saved_queries(self, ids: list[int]) -> tuple[int, int]:
        """置为已保留并解绑会话; 重复 id 只处理一次; 幂等: 已保留的行计入 persisted 且不重写."""
        async with self._session() as session:
            persisted = 0
            missing = 0
            for query_id in dict.fromkeys(ids):
                row = await session.get(SavedQuery, query_id)
                if row is None:
                    missing += 1
                    continue
                if not row.persisted or row.session_id is not None:
                    row.persisted = True
                    row.session_id = None
                    row.updated_at = _utcnow()
                    session.add(row)
                persisted += 1
            await session.commit()
            return persisted, missing
