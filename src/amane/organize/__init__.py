from amane.enums import MoveMode

from .file import OrganizeResult, execute_organize
from .link import create_video_link
from .path_templates import (
    CD_SUFFIX_TEMPLATE_DEFAULT,
    OPTIONAL_TEMPLATE_DEFAULTS,
    VIDEO_TEMPLATE_DEFAULT,
    CdSuffixTemplate,
    ResolvedPaths,
    StrmContentTemplate,
    normalize_link_template,
    path_template_schema,
    render_cd_suffix,
    render_strm_content,
    resolve_paths,
    resolve_subtitle_path,
    validate_cd_suffix_template,
    validate_strm_content_template,
)
from .subtitles import discover_subtitles, place_subtitles

__all__ = [
    "CD_SUFFIX_TEMPLATE_DEFAULT",
    "OPTIONAL_TEMPLATE_DEFAULTS",
    "VIDEO_TEMPLATE_DEFAULT",
    "CdSuffixTemplate",
    "MoveMode",
    "OrganizeResult",
    "ResolvedPaths",
    "StrmContentTemplate",
    "create_video_link",
    "discover_subtitles",
    "execute_organize",
    "normalize_link_template",
    "path_template_schema",
    "place_subtitles",
    "render_cd_suffix",
    "render_strm_content",
    "resolve_paths",
    "resolve_subtitle_path",
    "validate_cd_suffix_template",
    "validate_strm_content_template",
]
