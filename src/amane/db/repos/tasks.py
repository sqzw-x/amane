from collections.abc import Iterable, Sequence

from pydantic import BaseModel
from sqlalchemy import asc, update
from sqlalchemy import delete as sqla_delete
from sqlalchemy.sql.functions import coalesce, count
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel.sql.expression import SelectOfScalar

from ..models import SortOrder, Task, TaskLink, TaskSortField, TaskStatus, TaskType
from ..repo_types import _TASK_SORT_COLUMNS, _order_clause, _utcnow
from .base import RepositoryMixinBase

_ACTIVE_STATUSES = (TaskStatus.QUEUED, TaskStatus.RUNNING)
# 互斥键在 payload 里; 同键已有 queued/running 则复用, 不另建行.
# ORGANIZE / ARCHIVE 每次新建; 同库串行由 handler 内的锁保证.
_EXCLUSIVE_FIELDS: dict[TaskType, str] = {
    TaskType.ACTOR_SCRAPE: "actor_id",
}


def _scope_tasks[T](
    stmt: SelectOfScalar[T],
    *,
    task_ids: Iterable[int] | None = None,
    statuses: Iterable[TaskStatus] | None = None,
    task_types: Iterable[TaskType] | None = None,
) -> SelectOfScalar[T]:
    if task_ids is not None:
        stmt = stmt.where(col(Task.id).in_(list(task_ids)))
    if statuses:
        stmt = stmt.where(col(Task.status).in_(list(statuses)))
    if task_types:
        stmt = stmt.where(col(Task.type).in_(list(task_types)))
    return stmt


def _payload_dict(payload: dict[str, object] | BaseModel) -> dict[str, object]:
    return payload.model_dump(mode="json") if isinstance(payload, BaseModel) else payload


def _exclusive_value(payload: dict[str, object], field: str) -> int | None:
    raw = payload.get(field)
    if isinstance(raw, bool) or not isinstance(raw, int):
        return None
    return raw


async def _find_active_exclusive(session: AsyncSession, task_type: TaskType, field: str, value: int) -> Task | None:
    stmt = select(Task).where(col(Task.type) == task_type, col(Task.status).in_(_ACTIVE_STATUSES))
    result = await session.exec(stmt)
    for task in result.all():
        if _exclusive_value(task.payload or {}, field) == value:
            return task
    return None


async def _insert_or_reuse(
    session: AsyncSession,
    task_type: TaskType,
    payload: dict[str, object],
    priority: int,
) -> Task:
    field = _EXCLUSIVE_FIELDS.get(task_type)
    if field is not None:
        value = _exclusive_value(payload, field)
        if value is not None:
            existing = await _find_active_exclusive(session, task_type, field, value)
            if existing is not None:
                return existing
    task = Task(type=task_type, payload=payload, priority=priority)
    session.add(task)
    await session.flush()
    return task


_IS_CHAIN_ROOT = (col(Task.root_task_id).is_(None)) | (col(Task.root_task_id) == col(Task.id))


def _matching_root_ids(
    statuses: Iterable[TaskStatus] | None, task_types: Iterable[TaskType] | None
) -> SelectOfScalar[int]:
    """有 root 用 root, 裸任务用自身 id."""
    stmt = select(coalesce(col(Task.root_task_id), col(Task.id)))
    return _scope_tasks(stmt, statuses=statuses, task_types=task_types).distinct()


async def _ids_with_external_descendants(session: AsyncSession, ids: Sequence[int]) -> set[int]:
    """待删集合里存在集合外后裔的那些 id (含菱形多父).
    有未纳入本次删除的后裔时祖先须跳过, 否则剩余子任务失去链根后无法在列表中显示.
    """
    id_set = set(ids)
    if not id_set:
        return set()
    children_of: dict[int, list[int]] = {}
    frontier = list(id_set)
    visited = set(id_set)
    while frontier:
        rows = await session.exec(
            select(TaskLink.parent_task_id, TaskLink.child_task_id).where(col(TaskLink.parent_task_id).in_(frontier))
        )
        next_frontier: list[int] = []
        for parent, child in rows.all():
            if parent is None or child is None:
                continue
            children_of.setdefault(parent, []).append(child)
            if child not in visited:
                visited.add(child)
                next_frontier.append(child)
        frontier = next_frontier
    external = visited - id_set
    if not external:
        return set()
    parents_of: dict[int, list[int]] = {}
    for parent, kids in children_of.items():
        for child in kids:
            parents_of.setdefault(child, []).append(parent)
    protected: set[int] = set()
    stack = list(external)
    seen = set(external)
    while stack:
        node = stack.pop()
        for parent in parents_of.get(node, []):
            if parent in id_set:
                protected.add(parent)
            if parent not in seen:
                seen.add(parent)
                stack.append(parent)
    return protected


class TasksRepoMixin(RepositoryMixinBase):
    async def create_task(self, task_type: TaskType, payload: dict[str, object] | BaseModel, priority: int = 0) -> Task:
        tasks = await self.create_tasks(task_type, [payload], priority=priority)
        return tasks[0]

    async def create_tasks(
        self,
        task_type: TaskType,
        payloads: Sequence[dict[str, object] | BaseModel],
        priority: int = 0,
    ) -> list[Task]:
        if not payloads:
            return []
        async with self._task_insert_lock, self._session() as session:
            tasks = [
                await _insert_or_reuse(session, task_type, _payload_dict(payload), priority) for payload in payloads
            ]
            await session.commit()
            for task in tasks:
                await session.refresh(task)
            return tasks

    async def get_task(self, task_id: int) -> Task | None:
        async with self._session() as session:
            return await session.get(Task, task_id)

    async def claim_next_task(self) -> Task | None:
        """认领优先级最高的 QUEUED 行, 标为 RUNNING."""
        async with self._session() as session:
            stmt = (
                select(Task)
                .where(Task.status == TaskStatus.QUEUED)
                .order_by(col(Task.priority).desc(), asc(col(Task.created_at)))
                .limit(1)
            )
            result = await session.exec(stmt)
            task = result.first()
            if task is None:
                return None
            task.status = TaskStatus.RUNNING
            task.started_at = _utcnow()
            session.add(task)
            await session.commit()
            await session.refresh(task)
            return task

    async def complete_task(self, task_id: int, result: dict[str, object] | None = None) -> None:
        async with self._session() as session:
            task = await session.get(Task, task_id)
            if task is None:
                return
            task.status = TaskStatus.DONE
            task.result = result
            task.finished_at = _utcnow()
            session.add(task)
            await session.commit()

    async def complete_task_with_followups(
        self,
        task_id: int,
        result: dict[str, object] | None,
        followups: Sequence[tuple[str, TaskType, dict[str, object], int]],
    ) -> list[Task]:
        """父完成与子创建须在同一事务. 同父同 key 只留第一条, UNIQUE(parent, key) 拒绝重复."""
        created: list[Task] = []
        async with self._task_insert_lock, self._session() as session:
            task = await session.get(Task, task_id)
            if task is None:
                return created
            if task.status != TaskStatus.RUNNING:
                return created
            task.status = TaskStatus.DONE
            task.result = result
            task.finished_at = _utcnow()
            if task.root_task_id is None:
                task.root_task_id = task.id  # 裸任务成为链根: 指向自己
            session.add(task)
            root_id = task.root_task_id or task.id
            seen_keys: set[str] = set()
            for key, task_type, payload, priority in followups:
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                child = await _insert_or_reuse(session, task_type, payload, priority)
                if child.root_task_id is None:
                    child.root_task_id = root_id
                    session.add(child)
                assert child.id is not None
                session.add(TaskLink(parent_task_id=task_id, child_task_id=child.id, key=key))
                created.append(child)
            await session.commit()
            for child in created:
                await session.refresh(child)
        return created

    async def fail_task(self, task_id: int, error: str) -> None:
        async with self._session() as session:
            task = await session.get(Task, task_id)
            if task is None:
                return
            task.status = TaskStatus.FAILED
            task.error = error
            task.retries += 1
            task.finished_at = _utcnow()
            session.add(task)
            await session.commit()

    async def fail_all_running_tasks(self) -> int:
        """将全部 RUNNING 标为 FAILED (进程重启后的僵尸任务)."""
        async with self._session() as session:
            stmt = (
                update(Task)
                .where(col(Task.status) == TaskStatus.RUNNING)
                .values(
                    {
                        col(Task.status): TaskStatus.FAILED,
                        col(Task.error): "Task marked as failed due to application restart",
                        col(Task.finished_at): _utcnow(),
                    }
                )
            )
            result = await session.exec(stmt)
            await session.commit()
            return result.rowcount

    async def list_tasks(
        self,
        statuses: Iterable[TaskStatus] | None = None,
        task_types: Iterable[TaskType] | None = None,
        limit: int = 50,
        offset: int = 0,
        sort_by: TaskSortField = TaskSortField.CREATED_AT,
        order: SortOrder = SortOrder.DESC,
        *,
        roots_only: bool = False,
    ) -> list[Task]:
        async with self._session() as session:
            if roots_only and (statuses or task_types):
                # roots_only 只约束列表显示哪一层: 筛选仍匹配全部任务, 再还原链根.
                stmt = select(Task).where(col(Task.id).in_(_matching_root_ids(statuses, task_types)), _IS_CHAIN_ROOT)
            else:
                stmt = _scope_tasks(select(Task), statuses=statuses, task_types=task_types)
                if roots_only:
                    stmt = stmt.where(_IS_CHAIN_ROOT)
            stmt = (
                stmt.order_by(_order_clause(_TASK_SORT_COLUMNS[sort_by], order), col(Task.id).asc())
                .offset(offset)
                .limit(limit)
            )
            result = await session.exec(stmt)
            return list(result.all())

    async def count_tasks(
        self,
        statuses: Iterable[TaskStatus] | None = None,
        task_types: Iterable[TaskType] | None = None,
        *,
        roots_only: bool = False,
    ) -> int:
        async with self._session() as session:
            if roots_only and (statuses or task_types):
                stmt = select(count()).where(col(Task.id).in_(_matching_root_ids(statuses, task_types)), _IS_CHAIN_ROOT)
            else:
                base = _scope_tasks(select(Task), statuses=statuses, task_types=task_types)
                if roots_only:
                    base = base.where(_IS_CHAIN_ROOT)
                stmt = select(count()).select_from(base.subquery())
            result = await session.exec(stmt)
            return result.one() or 0

    async def list_task_links(
        self, *, parent_task_id: int | None = None, child_task_id: int | None = None
    ) -> list[TaskLink]:
        """可按父或子过滤."""
        async with self._session() as session:
            stmt = select(TaskLink)
            if parent_task_id is not None:
                stmt = stmt.where(col(TaskLink.parent_task_id) == parent_task_id)
            if child_task_id is not None:
                stmt = stmt.where(col(TaskLink.child_task_id) == child_task_id)
            stmt = stmt.order_by(col(TaskLink.id))
            result = await session.exec(stmt)
            return list(result.all())

    async def list_tasks_by_root(self, root_task_id: int) -> list[Task]:
        """同一链全部任务 (含根). root_task_id 在完成事务内写入."""
        async with self._session() as session:
            result = await session.exec(
                select(Task).where(col(Task.root_task_id) == root_task_id).order_by(col(Task.created_at), col(Task.id))
            )
            return list(result.all())

    async def list_children(
        self, parent_task_id: int, *, limit: int | None = None, offset: int = 0
    ) -> list[tuple[Task, str]]:
        """直接子任务, 按边创建顺序, 每项带出边 key."""
        async with self._session() as session:
            stmt = (
                select(Task, col(TaskLink.key))
                .join(TaskLink, col(TaskLink.child_task_id) == col(Task.id))
                .where(col(TaskLink.parent_task_id) == parent_task_id)
                .order_by(col(TaskLink.id))
            )
            if offset:
                stmt = stmt.offset(offset)
            if limit is not None:
                stmt = stmt.limit(limit)
            rows = (await session.exec(stmt)).all()
            return [(task, key) for task, key in rows]

    async def child_status_counts(self, parent_task_ids: Iterable[int]) -> dict[int, dict[TaskStatus, int]]:
        """返回 {parent_id: {status: n}}."""
        ids = list(parent_task_ids)
        if not ids:
            return {}
        async with self._session() as session:
            stmt = (
                select(col(TaskLink.parent_task_id), col(Task.status), count())
                .join(Task, col(Task.id) == col(TaskLink.child_task_id))
                .where(col(TaskLink.parent_task_id).in_(ids))
                .group_by(col(TaskLink.parent_task_id), col(Task.status))
            )
            out: dict[int, dict[TaskStatus, int]] = {}
            for parent_id, status, n in (await session.exec(stmt)).all():
                bucket = out.setdefault(parent_id, {})
                st = status if isinstance(status, TaskStatus) else TaskStatus(status)
                bucket[st] = int(n)
            return out

    async def find_tasks(
        self,
        *,
        task_ids: Iterable[int] | None = None,
        statuses: Iterable[TaskStatus] | None = None,
        task_types: Iterable[TaskType] | None = None,
    ) -> list[Task]:
        """不分页. task_ids 为空可迭代视为无匹配."""
        if task_ids is not None:
            ids = list(task_ids)
            if not ids:
                return []
        else:
            ids = None
        async with self._session() as session:
            stmt = _scope_tasks(select(Task), task_ids=ids, statuses=statuses, task_types=task_types)
            result = await session.exec(stmt)
            return list(result.all())

    async def fail_queued_tasks(
        self,
        *,
        error: str,
        task_ids: Iterable[int] | None = None,
        task_types: Iterable[TaskType] | None = None,
    ) -> int:
        """只标 QUEUED. 返回更新行数."""
        if task_ids is not None:
            ids = list(task_ids)
            if not ids:
                return 0
        else:
            ids = None
        async with self._session() as session:
            stmt = update(Task).where(col(Task.status) == TaskStatus.QUEUED)
            if ids is not None:
                stmt = stmt.where(col(Task.id).in_(ids))
            if task_types:
                stmt = stmt.where(col(Task.type).in_(list(task_types)))
            stmt = stmt.values(
                {
                    col(Task.status): TaskStatus.FAILED,
                    col(Task.error): error,
                    col(Task.finished_at): _utcnow(),
                    col(Task.retries): col(Task.retries) + 1,
                }
            )
            result = await session.exec(stmt)
            await session.commit()
            return result.rowcount

    async def retry_tasks(self, tasks: Sequence[Task]) -> list[Task]:
        """克隆为无根裸任务, 一次提交. 不保留原链归属, 否则会与原有后继冲突."""
        if not tasks:
            return []
        async with self._task_insert_lock, self._session() as session:
            created: list[Task] = []
            seen: set[int] = set()
            for task in tasks:
                row = await _insert_or_reuse(session, task.type, task.payload or {}, task.priority)
                assert row.id is not None
                if row.id in seen:
                    continue
                seen.add(row.id)
                created.append(row)
            await session.commit()
            for clone in created:
                await session.refresh(clone)
            return created

    async def delete_task(self, task_id: int) -> bool:
        """有未纳入本次删除的后裔时拒绝, 否则剩余子任务失去链根后无法在列表中显示."""
        async with self._session() as session:
            task = await session.get(Task, task_id)
            if task is None:
                return False
            if await _ids_with_external_descendants(session, [task_id]):
                return False
            await session.exec(
                sqla_delete(TaskLink).where(
                    (col(TaskLink.parent_task_id) == task_id) | (col(TaskLink.child_task_id) == task_id)
                )
            )
            await session.delete(task)
            await session.commit()
            return True

    async def delete_tasks(self, task_ids: Iterable[int | None]) -> int:
        """含相关后继边. 存在集合外后裔的行跳过."""
        ids = [i for i in task_ids if i is not None]
        if not ids:
            return 0
        async with self._session() as session:
            protected = await _ids_with_external_descendants(session, ids)
            deletable = [i for i in ids if i not in protected]
            if not deletable:
                return 0
            await session.exec(
                sqla_delete(TaskLink).where(
                    (col(TaskLink.parent_task_id).in_(deletable)) | (col(TaskLink.child_task_id).in_(deletable))
                )
            )
            stmt = sqla_delete(Task).where(col(Task.id).in_(deletable))
            result = await session.exec(stmt)
            await session.commit()
            return result.rowcount

    async def update_task_log_file(self, task_id: int, log_file: str) -> None:
        async with self._session() as session:
            task = await session.get(Task, task_id)
            if task is None:
                return
            task.log_file = log_file
            session.add(task)
            await session.commit()
