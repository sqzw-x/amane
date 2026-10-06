"""清理清单的只读接口: 面板读取清单, 提交扫描与删除仍走任务接口."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, HTTPException, Query

from ...db.models import TaskStatus, TaskType
from ...library import (
    LibraryPlan,
    PlanEntryKind,
    PlanNode,
    PlanSource,
    PlanStore,
    build_plan_tree,
    find_plan_node,
)
from ..deps import RepoDep, RuntimeDep
from ..models.cleanup import PlanNodeResponse, PlanNodesResponse, PlanSummaryResponse

if TYPE_CHECKING:
    from ...db.repository import Repository

router = APIRouter(prefix="/libraries", tags=["cleanup"])

_SCAN_STATUSES = (TaskStatus.QUEUED, TaskStatus.RUNNING)


def _relative(plan: LibraryPlan, path: Path) -> str:
    if not path.is_relative_to(plan.root):
        return str(path)
    return str(path.relative_to(plan.root))


def _to_response(plan: LibraryPlan, node: PlanNode, *, children: bool) -> PlanNodeResponse:
    return PlanNodeResponse(
        path=_relative(plan, node.path),
        name=node.name,
        kind=PlanEntryKind.DIR if node.is_dir else (PlanEntryKind.SYMLINK if node.is_symlink else PlanEntryKind.FILE),
        reason=node.reason,
        size=node.size,
        outside=node.outside,
        hardlink=node.hardlink,
        entry_count=node.entry_count,
        entry_bytes=node.entry_bytes,
        will_be_empty=node.will_be_empty,
        has_children=bool(node.children),
        children=[_to_response(plan, child, children=False) for child in node.children] if children else None,
    )


def _plan_or_404(store: PlanStore, library_id: int) -> LibraryPlan:
    plan = store.latest(library_id, PlanSource.RULES)
    if plan is None:
        raise HTTPException(status_code=404, detail="No cleanup plan")
    return plan


async def _scan_running(repo: Repository, library_id: int) -> bool:
    tasks = await repo.list_tasks(statuses=_SCAN_STATUSES, task_types=(TaskType.SCAN_INVALID,), limit=50)
    return any(task.payload.get("library_id") == library_id for task in tasks)


@router.get("/{library_id}/cleanup/plan")
async def get_cleanup_plan(library_id: int, repo: RepoDep, runtime: RuntimeDep) -> PlanSummaryResponse:
    """面板的入口: 有清单给状态与顶层节点, 无清单只给 ``exists=False``."""
    running = await _scan_running(repo, library_id)
    plan = runtime.plan_store.latest(library_id, PlanSource.RULES)
    if plan is None:
        return PlanSummaryResponse(exists=False, scan_running=running)

    root = build_plan_tree(plan)
    return PlanSummaryResponse(
        exists=True,
        plan_id=plan.plan_id,
        created_at=plan.created_at,
        scope_path=_relative(plan, plan.scope_path) if plan.scope_path is not None else None,
        truncated=plan.truncated,
        skipped_dirs=plan.skipped_dirs,
        skipped_files=plan.skipped_files,
        entry_count=root.entry_count,
        entry_bytes=root.entry_bytes,
        dir_count=len(plan.dirs),
        scan_running=running,
        nodes=[_to_response(plan, child, children=False) for child in root.children],
    )


@router.get("/{library_id}/cleanup/plan/nodes")
async def get_cleanup_plan_nodes(
    library_id: int,
    runtime: RuntimeDep,
    path: Annotated[str, Query(description="节点路径: 库内相对库根, 库外为绝对路径; 空串取根")] = "",
) -> PlanNodesResponse:
    """展开某个节点: 只返回该目录的直接子节点."""
    plan = _plan_or_404(runtime.plan_store, library_id)
    root = build_plan_tree(plan)
    node = find_plan_node(root, _resolve(plan, path))
    if node is None:
        raise HTTPException(status_code=404, detail=f"Plan node not found: {path}")
    return PlanNodesResponse(nodes=[_to_response(plan, child, children=False) for child in node.children])


def _resolve(plan: LibraryPlan, raw: str) -> Path:
    if not raw:
        return plan.root
    candidate = Path(raw)
    return candidate if candidate.is_absolute() else plan.root / candidate
