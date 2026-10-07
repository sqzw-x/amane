"""``safe_dirs is None`` (``AMANE_SAFE_DIRS=ALLOW_ALL``) 跳过边界层;
空列表表示未配置可用根, 无法校验.
"""

from pathlib import Path

from fastapi import HTTPException, status

from ...utils.path import is_any_descendant
from ...utils.threads import in_thread


class PathValidationError(ValueError):
    """路径校验失败; 子类各自携带对外的状态码."""

    status_code = status.HTTP_400_BAD_REQUEST


class SafeDirsMissing(PathValidationError):
    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR


class OutsideSafeDirs(PathValidationError):
    status_code = status.HTTP_403_FORBIDDEN


class PathMissing(PathValidationError):
    status_code = status.HTTP_404_NOT_FOUND


def _resolve_under_safe_dirs(raw_path: str, safe_dirs: list[Path] | None) -> Path:
    """``safe_dirs is None`` 表示无限制; 空列表表示未配置可用根, 无法校验."""
    if not raw_path or not raw_path.strip():
        raise PathValidationError("路径不能为空")

    p = Path(raw_path.strip())
    try:
        resolved = p.expanduser().resolve()
    except OSError as e:
        raise PathValidationError(f"路径解析失败: {e}") from e

    # 安全边界; None 表示不限制
    if safe_dirs is not None:
        if not safe_dirs:
            raise SafeDirsMissing("尚未配置安全目录, 无法校验路径")
        if not is_any_descendant(resolved, *safe_dirs):
            raise OutsideSafeDirs(f"路径 {raw_path} 不在允许的安全目录内")

    if not resolved.exists():
        raise PathMissing(f"路径不存在: {raw_path}")

    return resolved


@in_thread
def check_directory_path(raw_path: str, safe_dirs: list[Path] | None) -> Path:
    """失败抛 ``ValueError``."""
    resolved = _resolve_under_safe_dirs(raw_path, safe_dirs)
    if not resolved.is_dir():
        raise PathValidationError(f"不是目录: {raw_path}")
    return resolved


@in_thread
def check_plugin_install_path(raw_path: str, safe_dirs: list[Path] | None) -> Path:
    resolved = _resolve_under_safe_dirs(raw_path, safe_dirs)
    if resolved.is_dir():
        return resolved
    if resolved.is_file() and resolved.suffix.casefold() == ".zip":
        return resolved
    raise ValueError("只接受插件目录或 zip 文件")


def _http_from_path_error(exc: ValueError) -> HTTPException:
    code = exc.status_code if isinstance(exc, PathValidationError) else status.HTTP_400_BAD_REQUEST
    return HTTPException(status_code=code, detail=str(exc))


async def validate_directory_path(raw_path: str, safe_dirs: list[Path] | None) -> Path:
    try:
        return await check_directory_path(raw_path, safe_dirs)
    except ValueError as exc:
        raise _http_from_path_error(exc) from exc


async def validate_plugin_install_path(raw_path: str, safe_dirs: list[Path] | None) -> Path:
    try:
        return await check_plugin_install_path(raw_path, safe_dirs)
    except ValueError as exc:
        raise _http_from_path_error(exc) from exc
