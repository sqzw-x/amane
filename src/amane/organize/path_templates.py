import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

from pydantic import AfterValidator

from ..parsing.file_info import FileInfo
from ..parsing.number import ContentType
from ..utils.path import is_any_descendant, is_descendant

if TYPE_CHECKING:
    from collections.abc import Sequence

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
    subtitle: Path


# --- 默认模板 (当对应字段为 None 时使用) ---

VIDEO_TEMPLATE_DEFAULT = "{studio}/{number}/{number}.{ext}"

# CD 分集后缀模板: 识别到分集时追加到视频文件名 (扩展名之前); 空串关闭.
CD_SUFFIX_TEMPLATE_DEFAULT = "-CD{cd}"

_CD_SUFFIX_RE = re.compile(r"[^{}]*\{cd\}[^{}]*")


def validate_cd_suffix_template(value: str) -> str:
    """校验 CD 后缀模板 (空串合法, 表示关闭).

    非空时必须:
    - 恰好含 {cd} 这一个占位符 (其余花括号一概拒绝, 渲染时无歧义);
    - 不含路径分隔符 (只用于文件名段, 不能引入目录层级).

    渲染后的格式应保持可被 _detect_cd 反推 (如 -CD1 / -Part1), 否则二次整理会丢失分集标识;
    该约束当前不做强制.
    """
    stripped = value.strip()
    if not stripped:
        return ""
    if _CD_SUFFIX_RE.fullmatch(stripped) is None:
        raise ValueError("cd_suffix_template must contain exactly {cd} and no other braces")
    if "/" in stripped or "\\" in stripped:
        raise ValueError("cd_suffix_template must not contain path separators")
    return stripped


CdSuffixTemplate = Annotated[str, AfterValidator(validate_cd_suffix_template)]


def render_cd_suffix(template: str, cd: int) -> str:
    """渲染 CD 后缀 (调用前提: cd 非 None 且模板非空; 校验已保证恰好一个 {cd})."""
    return template.format(cd=cd)


OPTIONAL_TEMPLATE_DEFAULTS: dict[str, str] = {
    "thumb_template": "{video_dir}/thumb.jpg",
    "poster_template": "{video_dir}/poster.jpg",
    "fanart_template": "{video_dir}/fanart.jpg",
    "extrafanart_template": "{video_dir}/extrafanart",
    "nfo_template": "{video_dir}/{number}.nfo",
    "trailer_template": "{video_dir}/trailer.mp4",
    "subtitle_template": "{video_dir}/{number}.{ext}",
}

# 占位符相位: metadata 来自 Metadata; source 需 source_path (源文件目录);
# file 来自源文件名 (parse_file_info, 整理时检测); post_video 在视频路径渲染后注入.
PlaceholderPhase = str  # "metadata" | "source" | "file" | "post_video"

PLACEHOLDERS: tuple[tuple[str, PlaceholderPhase], ...] = (
    ("number", "metadata"),
    ("title", "metadata"),
    ("actor", "metadata"),
    ("actors", "metadata"),
    ("studio", "metadata"),
    ("publisher", "metadata"),
    ("series", "metadata"),
    ("year", "metadata"),
    ("release", "metadata"),
    ("ext", "metadata"),
    ("dir", "source"),
    ("dir_path", "source"),
    ("mosaic", "file"),
    ("definition", "file"),
    ("video_dir", "post_video"),
)


def path_template_schema() -> dict[str, object]:
    """供 API/前端消费的路径模板契约 (真源与 resolve_paths 同模块)."""
    return {
        "video_default": VIDEO_TEMPLATE_DEFAULT,
        "cd_suffix_default": CD_SUFFIX_TEMPLATE_DEFAULT,
        "optional_defaults": dict(OPTIONAL_TEMPLATE_DEFAULTS),
        "placeholders": [{"name": name, "phase": phase} for name, phase in PLACEHOLDERS],
    }


class _SafeDict(dict):
    """在 format_map() 中对缺失的 key 返回 'Unknown'."""

    def __missing__(self, key: str) -> str:
        return "Unknown"


def _safe(value: str | None) -> str | None:
    """清理字符串以使其可安全用于文件路径."""
    if not value:
        return None
    return (
        value.replace("/", " ")
        .replace("\\", " ")
        .replace(":", " ")
        .replace("*", "")
        .replace("?", "")
        .replace('"', "")
        .replace("<", "")
        .replace(">", "")
        .replace("|", "")
        .strip()
    )


def _mosaic_value(file_info: FileInfo | None) -> str:
    """{mosaic} 取值: 文件名标记 (uncensored/cracked) → 内容类型推断 → 兜底 censored.

    有码/无码是全域语义, 默认 censored 比占位符失效 (Unknown) 更能保证目录名稳定;
    file_info 缺失 (未走 ORGANIZE 的调用方) 时与其余占位符一致回退 Unknown.
    """
    if file_info is None:
        return "Unknown"
    if file_info.mosaic is not None:
        return file_info.mosaic
    if file_info.content_type == ContentType.UNCENSORED.value:
        return "uncensored"
    return "censored"


def _build_variables(
    metadata: Metadata,
    ext: str = "",
    source_dir: Path | None = None,
    file_info: FileInfo | None = None,
) -> dict[str, str]:
    """从元数据构建模板变量字典.

    Args:
        metadata: 元数据对象
        ext: 文件扩展名 (不含点)
        source_dir: 源文件所在目录, 提供 {dir} (目录名) 与 {dir_path} (完整路径); None 时二者为空串
        file_info: 源文件解析结果 (parse_file_info), 提供 {mosaic} 与 {definition}; None 时二者为 Unknown
    """
    year = metadata.release[:4] if metadata.release and len(metadata.release) >= 4 else None
    actor = metadata.actors[0] if metadata.actors else None

    return {
        "number": metadata.number,
        "title": _safe(metadata.title) or metadata.number,
        "actor": _safe(actor) or "Unknown",
        "actors": ", ".join(metadata.actors) if metadata.actors else "Unknown",
        "studio": _safe(metadata.studio) or "Unknown",
        "publisher": _safe(metadata.publisher) or "Unknown",
        "series": _safe(metadata.series) or "Unknown",
        "year": year or "Unknown",
        "release": _safe(metadata.release) or "Unknown",
        "ext": ext,
        "dir": source_dir.name if source_dir else "",
        "dir_path": str(source_dir) if source_dir else "",
        "mosaic": _mosaic_value(file_info),
        "definition": (file_info.definition if file_info else None) or "Unknown",
    }


def _render_template(
    template: str,
    variables: dict[str, str],
    base_path: Path,
    safe_dirs: Sequence[Path],
) -> Path:
    """渲染模板并解析为绝对路径, 强制约束在允许的边界内.

     边界规则 (任何情况都不允许逃逸):
    - 相对路径模板: 相对 base_path 解析, 渲染后必须是 base_path 的后代.
    - 绝对路径模板: 渲染后必须位于 base_path 或 safe_dirs 任一目录之下.
       base_path 始终可信 (默认模板经 {video_dir} 展开即为 base_path 下的绝对路径);
       safe_dirs 额外允许多盘分存等指向其他可信位置的绝对路径.

     所有路径都经过 resolve() 消除 .. 等符号后再校验.

     Raises:
         ValueError: 渲染结果逃逸了允许边界
    """
    rendered = template.format_map(_SafeDict(variables))
    path = Path(rendered)
    if path.is_absolute():
        resolved = path.resolve()
        # base_path 始终是可信根 (默认模板的 {video_dir} 即为 base_path 下的绝对路径);
        # safe_dirs 额外扩展可信集, 用于多盘分存等明确指向其他位置的绝对模板.
        allowed_roots = [base_path, *safe_dirs]
        if not is_any_descendant(resolved, *allowed_roots):
            raise ValueError(
                f"Path traversal detected: rendered path '{resolved}' escapes base '{base_path}' and safe directories"
            )
        return resolved
    # 相对路径模板: 必须是 base_path 的后代
    resolved = (base_path / path).resolve()
    if not is_descendant(resolved, base_path):
        raise ValueError(f"Path traversal detected: rendered path '{resolved}' escapes base '{base_path}'")
    return resolved


def resolve_paths(
    library: Library,
    metadata: Metadata,
    ext: str = "",
    cd: int | None = None,
    source_path: Path | None = None,
    file_info: FileInfo | None = None,
    safe_dirs: Sequence[Path] = (),
) -> ResolvedPaths:
    """根据 Library 模板配置和元数据渲染所有输出路径.

    Args:
        library: 媒体库配置 (含模板字段)
        metadata: 元数据对象 (需有 number, title, actors, studio 等属性)
        ext: 原始文件扩展名 (不含点, 如 "mp4", "mkv")
        cd: CD/分片编号, 非 None 且库的 cd_suffix_template 非空时按该模板追加后缀到视频文件名 (默认 -CD{n});
            None 时回退 file_info.cd
        source_path: 源文件完整路径, 提供 {dir} (源目录名) 与 {dir_path} (源目录完整路径) 变量
        file_info: 源文件解析结果 (parse_file_info), 提供 {mosaic} / {definition} 变量
        safe_dirs: 允许绝对路径模板落地的可信目录集 (多盘分存等). base_path 始终可信, 无需重复列出.

    Returns:
        ResolvedPaths 包含所有文件类型的完整路径

    Raises:
        ValueError: 任一模板渲染后逃逸了 base_path 与 safe_dirs 构成的边界
    """
    base_path = Path(library.path)
    source_dir = source_path.parent if source_path else None
    if cd is None and file_info is not None:
        cd = file_info.cd
    variables = _build_variables(metadata, ext, source_dir, file_info)

    # 1. 渲染视频路径
    video = _render_template(library.video_template, variables, base_path, safe_dirs)
    if cd is not None and library.cd_suffix_template:
        # 在扩展名前插入 CD 后缀 (模板由用户配置, 默认 -CD{n}; 空串关闭)
        video = video.with_name(f"{video.stem}{render_cd_suffix(library.cd_suffix_template, cd)}{video.suffix}")

    # 2. 计算 video_dir 变量
    video_dir = str(video.parent)
    variables["video_dir"] = video_dir

    # 3. 渲染其他路径 (None 时使用默认模板)
    thumb = _render_template(
        library.thumb_template or OPTIONAL_TEMPLATE_DEFAULTS["thumb_template"], variables, base_path, safe_dirs
    )
    poster = _render_template(
        library.poster_template or OPTIONAL_TEMPLATE_DEFAULTS["poster_template"], variables, base_path, safe_dirs
    )
    fanart = _render_template(
        library.fanart_template or OPTIONAL_TEMPLATE_DEFAULTS["fanart_template"], variables, base_path, safe_dirs
    )
    extrafanart_dir = _render_template(
        library.extrafanart_template or OPTIONAL_TEMPLATE_DEFAULTS["extrafanart_template"],
        variables,
        base_path,
        safe_dirs,
    )
    nfo = _render_template(
        library.nfo_template or OPTIONAL_TEMPLATE_DEFAULTS["nfo_template"], variables, base_path, safe_dirs
    )
    trailer = _render_template(
        library.trailer_template or OPTIONAL_TEMPLATE_DEFAULTS["trailer_template"], variables, base_path, safe_dirs
    )
    subtitle = _render_template(
        library.subtitle_template or OPTIONAL_TEMPLATE_DEFAULTS["subtitle_template"], variables, base_path, safe_dirs
    )

    return ResolvedPaths(
        video=video,
        thumb=thumb,
        poster=poster,
        fanart=fanart,
        extrafanart_dir=extrafanart_dir,
        nfo=nfo,
        trailer=trailer,
        subtitle=subtitle,
    )
