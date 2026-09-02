from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

from pydantic import AfterValidator

from ..enums import LinkMode
from ..parsing.file_info import FileInfo
from ..utils.path import is_descendant
from .template import PLACEHOLDER_MAP_KEYS as PLACEHOLDER_MAP_KEYS
from .template import PLACEHOLDERS as PLACEHOLDERS
from .template import Parser, PathEngine, TemplateContext

if TYPE_CHECKING:
    from ..db.models import Library, Metadata


@dataclass
class ResolvedPaths:
    """渲染后的所有输出路径 (完整路径含扩展名)."""

    video: Path
    thumb: Path
    poster: Path
    fanart: Path
    extrafanart_dir: Path
    nfo: Path
    trailer: Path
    link: Path | None = None


VIDEO_TEMPLATE_DEFAULT = "{studio}/{number}/{number}[-CD{cd?}][-{sub?}].{ext}"
THUMB_TEMPLATE_DEFAULT = "{link_dir}/thumb.jpg"
POSTER_TEMPLATE_DEFAULT = "{link_dir}/poster.jpg"
FANART_TEMPLATE_DEFAULT = "{link_dir}/fanart.jpg"
EXTRAFANART_TEMPLATE_DEFAULT = "{link_dir}/extrafanart"
NFO_TEMPLATE_DEFAULT = "{link_dir}/{number}.nfo"
TRAILER_TEMPLATE_DEFAULT = "{link_dir}/trailer.mp4"
SUBTITLE_TEMPLATE_DEFAULT = "{link_dir}/{raw_srt_name}.{ext}"


def normalize_link_template(value: str | None) -> str | None:
    """空白 link_template 视为未设置 (不创建链接)."""
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def validate_path_template(value: str) -> str:
    """校验路径模板结构 (括号 / 占位符 / 值映射). 空串合法."""
    Parser(value).parse()
    return value


PathTemplate = Annotated[str, AfterValidator(validate_path_template)]


def render_path_template(template: str, variables: dict[str, str]) -> str:
    """渲染占位符与可选组, 并折叠空路径段."""
    return PathEngine(template).render(TemplateContext.from_mapping(variables))


def resolve_paths(
    library: Library,
    metadata: Metadata,
    ext: str = "",
    cd: int | None = None,
    source_path: Path | None = None,
    file_info: FileInfo | None = None,
    safe_dirs: Sequence[Path] | None = (),
) -> ResolvedPaths:
    """根据 Library 模板配置和元数据渲染所有输出路径.

    Args:
        library: 媒体库配置 (含模板字段)
        metadata: 元数据对象 (需有 number, title, actors, studio 等属性)
        ext: 原始文件扩展名 (不含点, 如 "mp4", "mkv")
        cd: CD/分片编号; None 时回退 file_info.cd. 写入 ``{cd?}``, 由模板可选组决定是否出现在路径中.
        source_path: 源文件完整路径, 提供 {raw_dir} (源父目录名) 与 {raw_name} (源文件名不含扩展名)
        file_info: 源文件解析结果 (parse_file_info), 提供 {mosaic?} / {def?} / {sub?} / {cd?}
        safe_dirs: 允许绝对路径模板落地的可信目录集 (多盘分存等). base_path 始终可信, 无需重复列出.
            ``None`` 表示不限制绝对模板落点 (相对模板仍须在 base_path 下).

    Returns:
        ResolvedPaths 包含视频与刮削产物路径 (字幕按源文件逐条渲染, 见 resolve_subtitle_path).

    Raises:
        ValueError: 任一模板渲染后逃逸了 base_path 与 safe_dirs 构成的边界
    """
    base_path = Path(library.path)
    ctx = TemplateContext.from_metadata(metadata, ext=ext, source_path=source_path, file_info=file_info, cd=cd)

    video = PathEngine(library.video_template).resolve(ctx, base_path, safe_dirs)
    ctx.apply_video(video, base_path)
    link = _resolve_link_path(library, ctx, base_path, safe_dirs)
    ctx.apply_link(link)

    thumb = PathEngine(library.thumb_template or THUMB_TEMPLATE_DEFAULT).resolve(ctx, base_path, safe_dirs)
    poster = PathEngine(library.poster_template or POSTER_TEMPLATE_DEFAULT).resolve(ctx, base_path, safe_dirs)
    fanart = PathEngine(library.fanart_template or FANART_TEMPLATE_DEFAULT).resolve(ctx, base_path, safe_dirs)
    extrafanart_dir = PathEngine(library.extrafanart_template or EXTRAFANART_TEMPLATE_DEFAULT).resolve(
        ctx, base_path, safe_dirs
    )
    nfo = PathEngine(library.nfo_template or NFO_TEMPLATE_DEFAULT).resolve(ctx, base_path, safe_dirs)
    trailer = PathEngine(library.trailer_template or TRAILER_TEMPLATE_DEFAULT).resolve(ctx, base_path, safe_dirs)

    return ResolvedPaths(
        video=video,
        thumb=thumb,
        poster=poster,
        fanart=fanart,
        extrafanart_dir=extrafanart_dir,
        nfo=nfo,
        trailer=trailer,
        link=link,
    )


def _resolve_link_path(
    library: Library,
    ctx: TemplateContext,
    base_path: Path,
    safe_dirs: Sequence[Path] | None,
) -> Path | None:
    """渲染 link_template; 空模板返回 None. 结果必须落在库根之外.

    此时 `{video_dir}` / `{video_name}` / `{video_relpath}` 已注入, `{link_dir}` / `{link_name}` 尚未注入.
    """
    template = normalize_link_template(library.link_template)
    if template is None:
        return None
    link = PathEngine(template).resolve(ctx, base_path, safe_dirs)
    if library.link_mode == LinkMode.STRM:
        link = link.with_suffix(".strm")
    if is_descendant(link, base_path):
        raise ValueError(f"link_template must resolve outside the library root: '{link}' is under '{base_path}'")
    return link


def resolve_subtitle_path(
    library: Library,
    metadata: Metadata,
    subtitle_source: Path,
    *,
    video_dir: Path,
    link_dir: Path | None = None,
    video_name: str = "",
    video_dest: Path | None = None,
    link_name: str | None = None,
    source_path: Path | None = None,
    file_info: FileInfo | None = None,
    safe_dirs: Sequence[Path] | None = (),
) -> Path:
    """按字幕模板渲染单个字幕的目标路径.

    `{ext}` / `{raw_srt_name}` 取自该字幕源文件; `{raw_name}` / `{raw_dir}` 仍是视频源.
    `{video_dir}` / `{video_name}` 为整理后视频父目录与文件名 (不含扩展名);
    `{video_relpath}` 为整理后视频相对库根的路径 (`video_dest` 未传时为空);
    `{link_dir}` / `{link_name}` 为链接父目录与文件名 (未设链接时分别与视频侧相同).
    默认模板保持原文件名与扩展名.
    """
    base_path = Path(library.path)
    ctx = TemplateContext.from_metadata(metadata, source_path=source_path, file_info=file_info)
    if video_dest is not None:
        ctx.apply_video(video_dest, base_path)
    else:
        ctx.variables["video_dir"] = str(video_dir)
        ctx.variables["video_name"] = video_name
        ctx.variables["video_relpath"] = ""
    ctx.apply_link(None)
    if link_dir is not None:
        ctx.variables["link_dir"] = str(link_dir)
    if link_name is not None:
        ctx.variables["link_name"] = link_name
    ctx.apply_subtitle(subtitle_source)
    template = library.subtitle_template or SUBTITLE_TEMPLATE_DEFAULT
    return PathEngine(template).resolve(ctx, base_path, safe_dirs)
