"""清理清单的只读接口: 面板读取清单, 提交扫描与删除仍走任务接口."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, HTTPException, Query

from ...db.models import TaskStatus, TaskType
from ...library import (
    TRASH_DIRNAME,
    LibraryPlan,
    PlanEntryKind,
    PlanNode,
    PlanSource,
    PlanStore,
    build_selection_plan,
    find_plan_node,
    plan_tree,
    scan_trash,
)
from ..deps import RepoDep, RuntimeDep
from ..models.cleanup import (
    PlanNodePage,
    PlanNodeResponse,
    PlanSummaryResponse,
    SelectionRequest,
    SelectionSummaryResponse,
    TrashSummaryResponse,
)

if TYPE_CHECKING:
    from ...db.repository import Repository

router = APIRouter(prefix="/libraries", tags=["cleanup"])

_SCAN_STATUSES = (TaskStatus.QUEUED, TaskStatus.RUNNING)
# 单页子节点数. 一次下钻最多这么多条, 面板滚到底再取下一页.
NODE_PAGE_SIZE = 200
MAX_NODE_PAGE_SIZE = 1000


def _relative(plan: LibraryPlan, path: Path) -> str:
    """库内给相对路径 (库根为空串), 库外给绝对路径.

    一律用 ``as_posix``: 面板按 `/` 做分量匹配, 而 Windows 的原生分隔符是反斜杠.
    """
    if path == plan.root:
        return ""
    if not path.is_relative_to(plan.root):
        return path.as_posix()
    return path.relative_to(plan.root).as_posix()


def _to_response(plan: LibraryPlan, node: PlanNode, *, children: bool) -> PlanNodeResponse:
    return PlanNodeResponse(
        path=_relative(plan, node.path),
        name=node.name,
        kind=PlanEntryKind.DIR if node.is_dir else (PlanEntryKind.SYMLINK if node.is_symlink else PlanEntryKind.FILE),
        reason=node.reason,
        size=node.size,
        hardlink=node.hardlink,
        entry_count=node.entry_count,
        entry_bytes=node.entry_bytes,
        will_be_empty=node.will_be_empty,
        has_children=bool(node.children),
        children=[_to_response(plan, child, children=False) for child in node.children] if children else None,
    )


def _plan_by_id(store: PlanStore, library_id: int, plan_id: str | None) -> LibraryPlan:
    """按标识取清单. 已执行的清单在面板侧等同于不存在 — 它按设计无法再执行."""
    plan = store.get(plan_id) if plan_id else store.latest(library_id, PlanSource.RULES)
    if plan is None or plan.executed or plan.library_id != library_id:
        raise HTTPException(status_code=404, detail="No cleanup plan")
    return plan


def _page(plan: LibraryPlan, node: PlanNode, *, offset: int, limit: int) -> PlanNodePage:
    return PlanNodePage(
        path=_relative(plan, node.path),
        items=[_to_response(plan, child, children=False) for child in node.children[offset : offset + limit]],
        total=len(node.children),
        offset=offset,
        limit=limit,
        entry_count=node.entry_count,
        entry_bytes=node.entry_bytes,
    )


async def _scan_state(repo: Repository, library_id: int) -> tuple[bool, str | None]:
    """该库是否有扫描在跑, 以及最近一次扫描的失败原因 (面板据此给出可见的错误)."""
    running = await repo.list_tasks(statuses=_SCAN_STATUSES, task_types=(TaskType.SCAN_INVALID,), limit=50)
    is_running = any(task.payload.get("library_id") == library_id for task in running)
    recent = await repo.list_tasks(task_types=(TaskType.SCAN_INVALID,), limit=50)
    for task in recent:
        if task.payload.get("library_id") != library_id:
            continue
        if task.status is TaskStatus.FAILED:
            return is_running, task.error or "扫描失败"
        return is_running, None
    return is_running, None


@router.get("/{library_id}/cleanup/plan")
async def get_cleanup_plan(library_id: int, repo: RepoDep, runtime: RuntimeDep) -> PlanSummaryResponse:
    """面板的入口: 有清单给状态与范围, 无清单只给 ``exists=False``; 节点经 ``/plan/nodes`` 另取.

    遍历设置与当前库配置不一致的清单按不存在处理: 面板只渲染代表整库的规则来源清单,
    否则一次不递归或带自定义匹配模式的扫描会被当成整库可以清理.
    """
    running, last_error = await _scan_state(repo, library_id)
    library = await repo.get_library(library_id)
    if library is None:
        raise HTTPException(status_code=404, detail="Library not found")
    plan = runtime.plan_store.latest(library_id, PlanSource.RULES)
    if plan is None or plan.recursive != library.recursive or plan.patterns != tuple(library.patterns):
        return PlanSummaryResponse(exists=False, scan_running=running, last_scan_error=last_error)

    return PlanSummaryResponse(
        exists=True,
        plan_id=plan.plan_id,
        created_at=plan.created_at,
        scope_path=_relative(plan, plan.scope_path) if plan.scope_path is not None else None,
        truncated=plan.truncated,
        dropped=plan.dropped,
        skipped_dirs=plan.skipped_dirs,
        skipped_files=plan.skipped_files,
        scan_running=running,
        last_scan_error=last_error,
    )


@router.get("/{library_id}/cleanup/plan/nodes")
async def get_cleanup_plan_nodes(
    library_id: int,
    runtime: RuntimeDep,
    path: Annotated[str, Query(description="节点路径: 库内相对库根, 库外为绝对路径; 空串取根")] = "",
    plan_id: Annotated[str | None, Query(description="指定清单; 缺省用规则来源的最新一份")] = None,
    offset: Annotated[int, Query(ge=0, description="从第几个子节点开始")] = 0,
    limit: Annotated[int, Query(ge=1, le=MAX_NODE_PAGE_SIZE, description="本页最多返回多少个子节点")] = NODE_PAGE_SIZE,
) -> PlanNodePage:
    """展开某个节点的一页子节点. 库可能有上万条候选, 因此不整份下发."""
    plan = _plan_by_id(runtime.plan_store, library_id, plan_id)
    node = find_plan_node(plan_tree(plan), _resolve(plan, path))
    if node is None:
        raise HTTPException(status_code=404, detail=f"Plan node not found: {path}")
    return _page(plan, node, offset=offset, limit=limit)


def _resolve(plan: LibraryPlan, raw: str) -> Path:
    if not raw:
        return plan.root
    candidate = Path(raw)
    return candidate if candidate.is_absolute() else plan.root / candidate


@router.get("/{library_id}/cleanup/trash")
async def get_cleanup_trash(library_id: int, repo: RepoDep, runtime: RuntimeDep) -> TrashSummaryResponse:
    """展开回收站历史内容: 同步产出显式来源清单, 前端拿到要展开的目录再按页读."""
    library = await repo.get_library(library_id)
    if library is None:
        raise HTTPException(status_code=404, detail="Library not found")
    library_root = Path(library.path)
    trash_dir = library_root / TRASH_DIRNAME
    if not trash_dir.is_dir():
        return TrashSummaryResponse(exists=False)

    plan = await scan_trash(trash_dir, library_id=library_id, library_root=library_root)
    if not plan.entries:
        return TrashSummaryResponse(exists=False)
    runtime.plan_store.put(plan)

    return TrashSummaryResponse(
        exists=True,
        plan_id=plan.plan_id,
        path=_relative(plan, trash_dir),
        truncated=plan.truncated,
        dropped=plan.dropped,
    )


@router.post("/{library_id}/cleanup/selection")
async def expand_cleanup_selection(
    library_id: int,
    req: SelectionRequest,
    repo: RepoDep,
    runtime: RuntimeDep,
) -> SelectionSummaryResponse:
    """由选中项展开显式来源清单: 文件表与详情页的删除入口据此预览. 只读, 不改磁盘与索引."""
    library = await repo.get_library(library_id)
    if library is None:
        raise HTTPException(status_code=404, detail="Library not found")
    items = await repo.list_media_files(ids=req.media_file_ids, limit=None)
    if any(item.library_id != library_id for item in items):
        raise HTTPException(status_code=422, detail="media_file_ids 含其它库的文件")
    if len(items) != len(set(req.media_file_ids)):
        raise HTTPException(status_code=422, detail="media_file_ids 含不存在的文件")

    metas = {}
    for item in items:
        if item.metadata_id is not None and item.metadata_id not in metas:
            meta = await repo.get_metadata(item.metadata_id)
            if meta is not None:
                metas[item.metadata_id] = meta
    outcome = build_selection_plan(
        library=library,
        items=items,
        indexed=await repo.list_media_files(library_id=library_id, limit=None),
        metas=metas,
        include_work_dir=req.include_work_dir,
    )
    if not outcome.plan.entries:
        return SelectionSummaryResponse(exists=False, notices=outcome.notices)
    runtime.plan_store.put(outcome.plan)
    return SelectionSummaryResponse(
        exists=True,
        plan_id=outcome.plan.plan_id,
        notices=outcome.notices,
        truncated=outcome.plan.truncated,
        dropped=outcome.plan.dropped,
    )
