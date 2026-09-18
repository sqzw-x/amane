from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from pydantic_ai import RunContext
from pydantic_ai.capabilities import Capability

from ..api.support.path_validation import check_directory_path
from ..db.models import Library, TaskType
from ..db.repo_types import LibraryUpdates
from ..enums import LibraryAutomation, LibraryIngest
from ..handlers.models import RefreshPayload, ScanMode
from .tools import TOOL_OK, AgentDeps, require_approval, trace_tool, unknown_field_error

_LIBRARY_UPDATE_KEYS = frozenset(
    {
        "name",
        "path",
        "automation",
        "ingest",
        "cloud_path",
        "recursive",
        "patterns",
        "move_mode",
        "video_template",
        "link_template",
        "link_mode",
        "strm_content_template",
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


def _sync_library(deps: AgentDeps, lib: Library) -> None:
    watcher = deps.bridge.watcher
    if watcher is not None:
        watcher.sync_library(lib)


def build_library_ops_capability() -> Capability[AgentDeps]:
    """删除须批准."""
    cap: Capability[AgentDeps] = Capability(
        id="library-ops",
        instructions=(
            "A library path must exist and lie under safe_dirs; create_library rejects anything else. "
            "After create, prefer enqueue_library_refresh when the user wants an initial scan."
        ),
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
            await check_directory_path(path, ctx.deps.bridge.safe_dirs)
        except ValueError as exc:
            return {"error": str(exc)}
        display = name or Path(path).name
        try:
            lib = await ctx.deps.repo.create_library(
                name=display,
                path=path,
                automation=automation,
                recursive=recursive,
                patterns=patterns,
            )
        except ValueError as exc:
            return {"error": str(exc)}
        assert lib.id is not None
        _sync_library(ctx.deps, lib)
        out: dict[str, Any] = {"library_id": lib.id}
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
            out["refresh_task_id"] = task.id
        trace_tool(ctx, "tool_result", {"tool": "create_library", "result": out})
        return out

    @cap.tool
    async def update_library(
        ctx: RunContext[AgentDeps], library_id: int, patch: dict[str, Any]
    ) -> str | dict[str, Any]:
        """Patch library config fields."""
        trace_tool(ctx, "tool_call", {"tool": "update_library", "library_id": library_id, "patch": patch})
        if not patch:
            return {"error": "patch 为空"}
        unknown = sorted(set(patch) - _LIBRARY_UPDATE_KEYS)
        if unknown:
            return unknown_field_error(unknown, _LIBRARY_UPDATE_KEYS)
        if "automation" in patch:
            try:
                patch["automation"] = LibraryAutomation(patch["automation"])
            except ValueError:
                return {"error": f"无效的 automation: {patch['automation']}"}
        if "ingest" in patch:
            try:
                patch["ingest"] = LibraryIngest(patch["ingest"])
            except ValueError:
                return {"error": f"无效的 ingest: {patch['ingest']}"}
        if "path" in patch and patch["path"] is not None:
            try:
                await check_directory_path(str(patch["path"]), ctx.deps.bridge.safe_dirs)
            except ValueError as exc:
                return {"error": str(exc)}
        try:
            lib = await ctx.deps.repo.update_library(library_id, **cast(LibraryUpdates, patch))
        except ValueError as exc:
            return {"error": str(exc)}
        if lib is None:
            return {"error": f"library {library_id} 不存在"}
        watch_fields = {
            "automation",
            "ingest",
            "cloud_path",
            "path",
            "recursive",
            "patterns",
            "trailer_pattern",
            "blacklist_patterns",
            "min_file_size",
        }
        if watch_fields & set(patch):
            _sync_library(ctx.deps, lib)
        trace_tool(ctx, "tool_result", {"tool": "update_library", "result": TOOL_OK})
        return TOOL_OK

    @cap.tool
    async def delete_library(ctx: RunContext[AgentDeps], library_id: int) -> str | dict[str, Any]:
        """Delete a library and its MediaFile index rows."""
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
            return {"error": f"library {library_id} 不存在"}
        if ctx.deps.bridge.watcher is not None:
            ctx.deps.bridge.watcher.remove_library(library_id)
        await ctx.deps.repo.delete_library(library_id)
        trace_tool(ctx, "tool_result", {"tool": "delete_library", "result": TOOL_OK})
        return TOOL_OK

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
        out = {"task_id": task.id}
        trace_tool(ctx, "tool_result", {"tool": "enqueue_library_refresh", "result": out})
        return out

    return cap
