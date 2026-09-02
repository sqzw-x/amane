"""ORGANIZE 在 link_template 位置创建指向真实视频的 strm 或软链接."""

from pathlib import Path

from amane.enums import LinkMode
from amane.utils.threads import in_thread

from .file import OrganizeResult


@in_thread
def create_video_link(
    target: Path,
    link_path: Path,
    mode: LinkMode,
    *,
    content: str | None = None,
) -> OrganizeResult:
    """在 link_path 创建指向 target 的 strm 或软链接.

    strm 默认写 target 的绝对路径 (一行 + 换行); content 非空时用该正文.
    已就位则成功不改写. 占用路径若不是可替换的链接产物 (已有 strm / 软链接) 则拒绝覆盖.
    """
    try:
        link_path.parent.mkdir(parents=True, exist_ok=True)
        if mode == LinkMode.STRM:
            return _write_strm(target, link_path, content)
        return _write_symlink(target, link_path)
    except OSError as e:
        return OrganizeResult(success=False, error=str(e))


def _write_strm(target: Path, link_path: Path, content: str | None = None) -> OrganizeResult:
    text = content if content is not None else f"{target}\n"
    if link_path.exists() and not link_path.is_symlink():
        if link_path.suffix.lower() == ".strm":
            if link_path.read_text(encoding="utf-8") == text:
                return OrganizeResult(success=True, dest=link_path)
        else:
            return OrganizeResult(success=False, error=f"Refusing to overwrite {link_path}")
    if link_path.exists() or link_path.is_symlink():
        link_path.unlink()
    link_path.write_text(text, encoding="utf-8")
    return OrganizeResult(success=True, dest=link_path)


def _write_symlink(target: Path, link_path: Path) -> OrganizeResult:
    if link_path.is_symlink():
        try:
            if link_path.resolve() == target.resolve():
                return OrganizeResult(success=True, dest=link_path)
        except OSError:
            pass
        link_path.unlink()
    elif link_path.exists():
        return OrganizeResult(success=False, error=f"Refusing to overwrite {link_path}")
    link_path.symlink_to(target)
    return OrganizeResult(success=True, dest=link_path)
