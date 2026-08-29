"""library-ops Capability - 媒体库 CRUD 与扫描入队."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from pydantic_ai import RunContext
from pydantic_ai.capabilities import Capability

from amane.api.support.path_validation import check_directory_path
from amane.db.models import TaskType
from amane.db.repo_types import LibraryUpdates
from amane.enums import LibraryAutomation
from amane.handlers.models import RefreshPayload, ScanMode

from .tools import AgentDeps, require_approval, trace_tool

_LIBRARY_UPDATE_KEYS = frozenset(
    {
        "name",
        "path",
        "automation",
        "recursive",
        "patterns",
        "move_mode",
        "video_template",
        "link_template",
        "link_mode",
        "thumb_template",
        "poster_template",
        "fanart_template",
        "extrafanart_template",
        "nfo_template",
        "trailer_template",
        "subtitle_template",
        "subtitle_extensions",
        "write_nfo",
        "copy_resources",
        "trailer_pattern",
        "blacklist_patterns",
        "min_file_size",
    }
)


def _sync_watcher_add(
    deps: AgentDeps,
    *,
    path: str,
    library_id: int,
    recursive: bool,
    patterns: list[str],
    skip_patterns: list[str],
    min_file_size: int = 0,
) -> None:
    watcher = deps.bridge.watcher
    if watcher is not None:
        watcher.add_library(
            path,
            library_id,
            recursive=recursive,
            patterns=patterns,
            skip_patterns=skip_patterns,
            min_file_size=min_file_size,
        )


def _sync_watcher_remove(deps: AgentDeps, library_id: int) -> None:
    watcher = deps.bridge.watcher
    if watcher is not None:
        watcher.remove_library(library_id)


def build_library_ops_capability() -> Capability[AgentDeps]:
    """按需加载的媒体库管理; 删除须批准."""
    cap: Capability[AgentDeps] = Capability(
        id="library-ops",
        description=(
            "Use for creating/updating/deleting media libraries and enqueueing refresh/scan tasks. "
            "Paths must stay inside configured safe directories."
        ),
        instructions=(
            "Library paths must exist and lie under safe_dirs. "
            "delete_library requires user approval (removes MediaFile index rows, not disk files). "
            "Prefer enqueue_library_refresh after create when the user wants an initial scan."
        ),
        defer_loading=True,
    )

    @cap.tool
    async def create_library(
        ctx: RunContext[AgentDeps],
        path: str,
        name: str | None = None,
        automation: LibraryAutomation = LibraryAutomation.SCRAPE,
        recursive: bool = True,
        patterns: list[str] | None = None,
        scan: bool = True,
    ) -> dict[str, Any]:
        """Create a media library; optional initial REFRESH(scan=add)."""
        patterns = list(patterns or [])
        trace_tool(
            ctx,
            "tool_call",
            {
                "tool": "create_library",
                "path": path,
                "name": name,
                "automation": automation,
                "recursive": recursive,
                "patterns": patterns,
                "scan": scan,
            },
        )
        try:
            check_directory_path(path, ctx.deps.bridge.safe_dirs)
        except ValueError as exc:
            return {"error": str(exc)}
        display = name or Path(path).name
        lib = await ctx.deps.repo.create_library(
            name=display,
            path=path,
            automation=automation,
            recursive=recursive,
            patterns=patterns,
        )
        assert lib.id is not None
        if automation != LibraryAutomation.NONE:
            _sync_watcher_add(
                ctx.deps,
                path=lib.path,
                library_id=lib.id,
                recursive=lib.recursive,
                patterns=list(lib.patterns or []),
                skip_patterns=[lib.trailer_pattern, *(lib.blacklist_patterns or [])],
                min_file_size=lib.min_file_size,
            )
        task_id: int | None = None
        if scan:
            task = await ctx.deps.repo.create_task(
                TaskType.REFRESH,
                RefreshPayload(
                    library_id=lib.id,
                    recursive=lib.recursive,
                    patterns=list(lib.patterns or []),
                    path=lib.path,
                    scan={ScanMode.add},
                    scrape=set(),
                ),
            )
            assert task.id is not None
            task_id = task.id
        out = {"id": lib.id, "name": lib.name, "path": lib.path, "refresh_task_id": task_id}
        trace_tool(ctx, "tool_result", {"tool": "create_library", "result": out})
        return out

    @cap.tool
    async def update_library(ctx: RunContext[AgentDeps], library_id: int, patch: dict[str, Any]) -> dict[str, Any]:
        """Patch library config fields."""
        trace_tool(ctx, "tool_call", {"tool": "update_library", "library_id": library_id, "patch": patch})
        if not patch:
            return {"error": "patch 为空"}
        unknown = sorted(set(patch) - _LIBRARY_UPDATE_KEYS)
        if unknown:
            return {"error": f"不允许的字段: {', '.join(unknown)}"}
        if "automation" in patch:
            try:
                patch["automation"] = LibraryAutomation(patch["automation"])
            except ValueError:
                return {"error": f"无效的 automation: {patch['automation']}"}
        if "path" in patch and patch["path"] is not None:
            try:
                check_directory_path(str(patch["path"]), ctx.deps.bridge.safe_dirs)
            except ValueError as exc:
                return {"error": str(exc)}
        lib = await ctx.deps.repo.update_library(library_id, **cast(LibraryUpdates, patch))
        if lib is None:
            return {"error": f"library {library_id} 不存在"}
        watch_fields = {
            "automation",
            "path",
            "recursive",
            "patterns",
            "trailer_pattern",
            "blacklist_patterns",
            "min_file_size",
        }
        if watch_fields & set(patch):
            _sync_watcher_remove(ctx.deps, library_id)
            if lib.automation != LibraryAutomation.NONE:
                assert lib.id is not None
                _sync_watcher_add(
                    ctx.deps,
                    path=lib.path,
                    library_id=lib.id,
                    recursive=lib.recursive,
                    patterns=list(lib.patterns or []),
                    skip_patterns=[lib.trailer_pattern, *(lib.blacklist_patterns or [])],
                    min_file_size=lib.min_file_size,
                )
        out = {"id": lib.id, "name": lib.name, "path": lib.path, "updated": True}
        trace_tool(ctx, "tool_result", {"tool": "update_library", "result": out})
        return out

    @cap.tool
    async def delete_library(ctx: RunContext[AgentDeps], library_id: int) -> dict[str, Any]:
        """Delete a library and its MediaFile index rows. Requires approval."""
        detail = f"删除媒体库 id={library_id} (仅索引, 不动磁盘文件)"
        trace_tool(ctx, "tool_call", {"tool": "delete_library", "library_id": library_id})
        require_approval(
            ctx,
            sql=detail,
            tool="delete_library",
            extra={"library_id": library_id},
        )
        existing = await ctx.deps.repo.list_libraries()
        if not any(lib.id == library_id for lib in existing):
            out = {"tool": "delete_library", "library_id": library_id, "deleted": False, "error": "不存在"}
            trace_tool(ctx, "tool_result", {"tool": "delete_library", "result": out})
            return out
        if ctx.deps.bridge.watcher is not None:
            ctx.deps.bridge.watcher.remove_library(library_id)
        deleted_media = await ctx.deps.repo.delete_library(library_id)
        out = {
            "tool": "delete_library",
            "library_id": library_id,
            "deleted": True,
            "deleted_media": deleted_media,
        }
        trace_tool(ctx, "tool_result", {"tool": "delete_library", "result": out})
        return out

    @cap.tool
    async def enqueue_library_refresh(
        ctx: RunContext[AgentDeps],
        library_id: int,
        scan_add: bool = True,
        scan_remove: bool = False,
    ) -> dict[str, Any]:
        """Enqueue a REFRESH task for a library (scan add/remove modes)."""
        trace_tool(
            ctx,
            "tool_call",
            {
                "tool": "enqueue_library_refresh",
                "library_id": library_id,
                "scan_add": scan_add,
                "scan_remove": scan_remove,
            },
        )
        lib = await ctx.deps.repo.get_library(library_id)
        if lib is None:
            return {"error": f"library {library_id} 不存在"}
        scan: set[ScanMode] = set()
        if scan_add:
            scan.add(ScanMode.add)
        if scan_remove:
            scan.add(ScanMode.remove)
        if not scan:
            return {"error": "至少启用 scan_add 或 scan_remove"}
        task = await ctx.deps.repo.create_task(
            TaskType.REFRESH,
            RefreshPayload(
                library_id=library_id,
                recursive=lib.recursive,
                patterns=list(lib.patterns or []),
                path=lib.path,
                scan=scan,
                scrape=set(),
            ),
        )
        assert task.id is not None
        out = {"task_id": task.id, "library_id": library_id, "scan": sorted(scan)}
        trace_tool(ctx, "tool_result", {"tool": "enqueue_library_refresh", "result": out})
        return out

    return cap
