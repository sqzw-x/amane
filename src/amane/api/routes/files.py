import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from ...utils.path import is_any_descendant
from ..deps import RuntimeDep

router = APIRouter(prefix="/files", tags=["files"])

_MAX_ITEMS = 1000


def _default_browse_root() -> Path:
    """``ALLOW_ALL`` 时相对 path 的缺省根: Windows ``C:\\``, POSIX ``/``."""
    return Path("C:/") if os.name == "nt" else Path("/")


class FileItem(BaseModel):
    """文件或目录条目"""

    name: str = Field(..., description="File or directory name.")
    path: str = Field(..., description="Absolute POSIX path.")
    type: Literal["file", "directory"] = Field(..., description="Entry type.")
    size: int | None = Field(default=None, description="File size in bytes (omitted for directories).")
    last_modified: datetime | None = Field(default=None, description="Last modification time.")


class FileListResponse(BaseModel):
    """目录列表响应"""

    path: str = Field(..., description="Canonical absolute path of the listed directory (POSIX-style).")
    items: list[FileItem] = Field(
        ..., description="Entries sorted directories-first, then by name (case-insensitive). Truncated to 1000."
    )
    total: int = Field(..., description="Total entry count before truncation.")


def _canonical_response_path(p: Path) -> str:
    """回传用规范路径: as_posix 形态, 清除 \\?\\ 设备前缀 (仅 Windows 可能出现)."""
    s = str(p.as_posix())
    if s.startswith("//?/UNC/"):
        return "//" + s[len("//?/UNC/") :]
    if s.startswith("//?/"):
        return s[len("//?/") :]
    return s


@router.get("", summary="List files and directories at a server path")
async def list_files(
    path: Annotated[
        str, Query(description="Server path to list. Relative paths resolve against `base` or the first safe dir.")
    ],
    runtime: RuntimeDep,
    base: Annotated[
        str | None,
        Query(description="Base directory (absolute canonical) for relative `path`; defaults to the first safe dir."),
    ] = None,
    show_hidden: Annotated[bool, Query(description="Whether to include hidden files (dotfiles).")] = False,
) -> FileListResponse:
    """列出目录内容. 仅允许访问启动时确定的安全目录内的路径; ``safe_dirs is None`` 时不限制."""
    safe_dirs = runtime.safe_dirs
    if safe_dirs is not None and not safe_dirs:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="No safe directories configured.")

    p = Path(path)
    try:
        # 非严格解析: 虚拟/网络挂载盘 (CloudDrive2 等) 无法规范化查询时按字面继续,
        # 存在性由下方 exists() 检查统一给出 404.
        if p.is_absolute():
            target_path = p.resolve()
        else:
            if base:
                base_dir = Path(base).resolve()
            elif safe_dirs:
                base_dir = safe_dirs[0]
            else:
                base_dir = _default_browse_root()
            target_path = (base_dir / p).resolve()
    except OSError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Path resolution failed — may not exist or lacks access permission.",
        )

    # 安全检查 (ALLOW_ALL 时 safe_dirs 为 None, 跳过)
    if safe_dirs is not None and not is_any_descendant(target_path, *safe_dirs):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access to this path is not permitted.")

    if not target_path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Path does not exist: {path}")

    # 如果路径是文件, 则列出其父目录
    if not target_path.is_dir():
        target_path = target_path.parent

    items: list[FileItem] = []
    try:
        for entry in os.scandir(target_path):
            if not show_hidden and entry.name.startswith("."):
                continue
            entry_path = Path(entry.path)
            item_type: Literal["file", "directory"] = "directory" if entry_path.is_dir() else "file"
            item = FileItem(name=entry.name, path=str(entry_path.as_posix()), type=item_type)
            try:
                stat_result = entry.stat()
                item.last_modified = datetime.fromtimestamp(stat_result.st_mtime, tz=UTC)
                if item_type == "file":
                    item.size = stat_result.st_size
            except OSError:
                pass
            items.append(item)
    except PermissionError:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied reading directory.")
    except OSError as exc:
        # 网络盘挂载失效 (macOS errno 6 "Device not configured") 等抛 OSError
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"无法读取目录: {exc.strerror or exc}",
        )

    # 排序: 目录优先, 然后按名称字母序 (不区分大小写)
    items.sort(key=lambda x: (x.type != "directory", x.name.lower()))
    total = len(items)
    return FileListResponse(path=_canonical_response_path(target_path), items=items[:_MAX_ITEMS], total=total)
