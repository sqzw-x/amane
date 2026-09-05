from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

from pydantic import AfterValidator

from ..enums import ActorGender, LinkMode
from ..parsing.file_info import FileInfo
from ..utils.path import is_descendant
from .template import PLACEHOLDER_MAP_KEYS as PLACEHOLDER_MAP_KEYS
from .template import PLACEHOLDERS as PLACEHOLDERS
from .template import Parser, PathEngine, TemplateContext

if TYPE_CHECKING:
    from ..db.models import Library, Metadata


@dataclass
class ResolvedPaths:
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
    """空串合法."""
    Parser(value).parse()
    return value


PathTemplate = Annotated[str, AfterValidator(validate_path_template)]


def render_path_template(template: str, variables: dict[str, str]) -> str:
    return PathEngine(template).render(TemplateContext.from_mapping(variables))


def resolve_paths(
    library: Library,
    metadata: Metadata,
    ext: str = "",
    cd: int | None = None,
    source_path: Path | None = None,
    file_info: FileInfo | None = None,
    safe_dirs: Sequence[Path] | None = (),
    actor_genders: Mapping[str, ActorGender] | None = None,
) -> ResolvedPaths:
    """``safe_dirs is None`` 时不限制绝对模板写出的路径; 相对模板仍须在 base_path 下."""
    base_path = Path(library.path)
    ctx = TemplateContext.from_metadata(
        metadata,
        ext=ext,
        source_path=source_path,
        file_info=file_info,
        cd=cd,
        actor_genders=actor_genders,
    )

    # 先渲染视频, 注入 `{video_*}` 后再渲染链接与刮削产物.
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
    """结果必须在库外. 此时 `{video_dir}` / `{video_name}` / `{video_relpath}` 已注入, `{link_dir}` / `{link_name}` 尚未注入."""
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
    actor_genders: Mapping[str, ActorGender] | None = None,
) -> Path:
    """`{ext}` / `{raw_srt_name}` 取自该字幕源文件; `{raw_name}` / `{raw_dir}` 仍是视频源.
    `{video_dir}` / `{video_name}` 为整理后视频父目录与文件名 (不含扩展名);
    `{video_relpath}` 为整理后视频相对库根目录的路径 (`video_dest` 未传时为空);
    `{link_dir}` / `{link_name}` 为链接父目录与文件名 (未设置链接时分别与视频侧相同).
    默认模板保持原文件名与扩展名.
    """
    base_path = Path(library.path)
    ctx = TemplateContext.from_metadata(
        metadata, source_path=source_path, file_info=file_info, actor_genders=actor_genders
    )
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
