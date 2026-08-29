"""
共享的路径校验工具.

供需要校验用户输入路径的端点使用 (scan/organize/watch_paths). 路径检查包含三个层次:

1. 解析存在性: 路径必须能 resolve 到磁盘上的实际位置 (可选 strict).
2. 类型: 目录端点要求目录; 插件安装还允许 ``.zip`` 文件.
3. 安全边界: 必须位于 ``runtime.safe_dirs`` 之下, 防止任意路径访问.
   ``safe_dirs is None`` (``AMANE_SAFE_DIRS=ALLOW_ALL``) 时跳过此层.

校验失败时统一抛出 ``HTTPException(400|403|404)`` 并附带可操作的中文消息.
"""

from pathlib import Path

from fastapi import HTTPException, status

from ...utils.path import is_any_descendant


def _resolve_under_safe_dirs(raw_path: str, safe_dirs: list[Path] | None) -> Path:
    """Resolve an existing path that lies under ``safe_dirs``.

    ``safe_dirs is None`` 表示无限制; 空列表表示未配置可用根, 无法校验.
    """
    if not raw_path or not raw_path.strip():
        raise ValueError("Path cannot be empty.")

    p = Path(raw_path.strip())
    try:
        resolved = p.expanduser().resolve()
    except OSError as e:
        raise ValueError(f"Path resolution failed: {e}") from e

    if safe_dirs is not None:
        if not safe_dirs:
            raise ValueError("No safe directories configured; cannot validate path.")
        if not is_any_descendant(resolved, *safe_dirs):
            raise ValueError(f"Path '{raw_path}' is outside the configured safe directories.")

    if not resolved.exists():
        raise ValueError(f"Path does not exist: {raw_path}")

    return resolved


def check_directory_path(raw_path: str, safe_dirs: list[Path] | None) -> Path:
    """校验目录路径; 失败抛 ``ValueError`` (供 Agent 工具等非 HTTP 调用方)."""
    resolved = _resolve_under_safe_dirs(raw_path, safe_dirs)
    if not resolved.is_dir():
        raise ValueError(f"Not a directory: {raw_path}")
    return resolved


def check_plugin_install_path(raw_path: str, safe_dirs: list[Path] | None) -> Path:
    """Resolve a plugin source: directory or ``.zip`` file inside ``safe_dirs``."""
    resolved = _resolve_under_safe_dirs(raw_path, safe_dirs)
    if resolved.is_dir():
        return resolved
    if resolved.is_file() and resolved.suffix.casefold() == ".zip":
        return resolved
    raise ValueError("只接受插件目录或 zip 文件")


def _http_from_path_error(exc: ValueError) -> HTTPException:
    msg = str(exc)
    if msg.startswith("No safe directories"):
        return HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=msg)
    if "outside the configured safe directories" in msg:
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=msg)
    if msg.startswith("Path does not exist"):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=msg)
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=msg)


def validate_directory_path(raw_path: str, safe_dirs: list[Path] | None) -> Path:
    """
    校验用户提交的目录路径, 返回 resolve 后的 ``Path``.

    Raises:
        HTTPException(400): 路径无法 resolve (符号链接循环, 权限不足等) / 非目录
        HTTPException(403): 路径不在 safe_dirs 范围内
        HTTPException(404): 路径不存在
        HTTPException(500): safe_dirs 未配置
    """
    try:
        return check_directory_path(raw_path, safe_dirs)
    except ValueError as exc:
        raise _http_from_path_error(exc) from exc


def validate_plugin_install_path(raw_path: str, safe_dirs: list[Path] | None) -> Path:
    """校验插件安装源路径 (目录或 zip), 返回 resolve 后的 ``Path``."""
    try:
        return check_plugin_install_path(raw_path, safe_dirs)
    except ValueError as exc:
        raise _http_from_path_error(exc) from exc
