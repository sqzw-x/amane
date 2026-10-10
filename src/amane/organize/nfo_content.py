"""NFO 正文模板: 字面量原样输出, 取值转义后写入, 不截断不折叠空段.

与路径 / STRM 模板的两处语义分叉:
- `[...]` 组内子树不含任何占位符时, 组没有条件可言, 连方括号一起原样输出;
- 取值取刮削原始值, 缺值为空串, 不文件名清洗、不回退 `Unknown`.

取值写进 XML 前逐一转义, 字面量不转义 (用户自己写标签). 片段占位符已转义, 只能写在元素内容位置.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections.abc import Iterator, Mapping, Sequence
from typing import TYPE_CHECKING, Annotated

import structlog
from pydantic import AfterValidator

from ..parsing.file_info import FileInfo, parse_file_info, split_number
from ..utils.text import strip_illegal_xml_chars
from .template import PLACEHOLDER_MAP_KEYS, TemplateContext, TemplateEngine, placeholder_names

if TYPE_CHECKING:
    from ..db.models import Metadata


logger = structlog.get_logger()

_TEMPLATE_NAME = "NFO 内容模板"

_XML_ESCAPE_MAP: dict[str, str] = {
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "'": "&apos;",
    '"': "&quot;",
}


def _escape_xml(text: str) -> str:
    """写进 XML 节点: 先剥非法字符 (存量脏数据兜底), 再转义; 换行原样保留."""
    escaped = strip_illegal_xml_chars(text)
    for char, entity in _XML_ESCAPE_MAP.items():
        escaped = escaped.replace(char, entity)
    return escaped


NFO_TEXT_PLACEHOLDERS: tuple[str, ...] = (
    "number",
    "prefix",
    "suffix",
    "title",
    "display_title",
    "video_name",
    "plot",
    "release",
    "year",
    "rating",
    "criticrating",
    "runtime",
    "series",
    "studio",
    "publisher",
    "actor",
    "actors",
    "directors",
    "tags",
    "poster",
    "cover",
    "trailer",
    "content_type",
    "mosaic?",
    "def?",
    "cd?",
    "sub?",
)

NFO_FRAGMENT_PLACEHOLDERS: tuple[str, ...] = (
    "xml_actor",
    "xml_tag",
    "xml_genre",
    "xml_director",
    "xml_external_id",
)
"""片段占位符: 已转义的整段元素, 只能写在元素内容位置. 内部结构固定, 不是通用循环."""

NFO_PLACEHOLDERS: tuple[str, ...] = NFO_TEXT_PLACEHOLDERS + NFO_FRAGMENT_PLACEHOLDERS

# 与路径模板的名字同义, 因此沿用同一张映射键表下发 schema.
NFO_PLACEHOLDER_MAP_KEYS: dict[str, tuple[str, ...]] = {
    name: PLACEHOLDER_MAP_KEYS[name] for name in ("content_type", "mosaic?", "def?", "sub?")
}

_XML_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]*")


def _element_lines(name: str, values: Sequence[str]) -> str:
    return "".join(f"  <{name}>{_escape_xml(value)}</{name}>\n" for value in values if value)


def _actor_fragment(actors: Sequence[str]) -> str:
    return "".join(
        f"  <actor>\n    <name>{_escape_xml(actor)}</name>\n    <type>Actor</type>\n  </actor>\n"
        for actor in actors
        if actor
    )


def _external_id_fragment(external_ids: Mapping[str, str]) -> str:
    """站点名直接进入标签名: 不是合法 XML 名时跳过 (多语言来源的键形如 `r18dev:zh_cn`)."""
    lines: list[str] = []
    for site, ext_id in external_ids.items():
        if not _XML_NAME.fullmatch(site):
            logger.warning("nfo external id skipped", site=site)
            continue
        lines.append(f"  <{site}id>{_escape_xml(ext_id)}</{site}id>\n")
    return "".join(lines)


def _nfo_variables(metadata: Metadata, file_info: FileInfo | None, video_name: str) -> dict[str, str]:
    """文件相位与路径模板同名同义; 相位取自本次文件的 FileInfo, 缺失时按番号推定."""
    number = metadata.number
    title = metadata.title or ""
    prefix, suffix = split_number(number) if number else ("", "")
    release = metadata.release or ""
    year_match = re.match(r"(\d{4})", release)
    score = metadata.score
    runtime = metadata.runtime
    info = file_info if file_info is not None else (parse_file_info(text=number) if number else None)
    cd = info.cd if info is not None else None
    actors = list(metadata.actors)
    tags = list(metadata.tags)
    directors = list(metadata.directors)
    external_ids = {site: str(ext_id) for site, ext_id in metadata.external_ids.items() if ext_id}

    return {
        "number": number,
        "prefix": prefix,
        "suffix": suffix,
        "title": title,
        "display_title": f"{number} {title}" if title else "",
        "video_name": video_name,
        "plot": metadata.plot or "",
        "release": release,
        "year": year_match.group(1) if year_match else "",
        "rating": "" if score is None else str(score),
        "criticrating": "" if score is None else str(int(score * 10)),
        "runtime": "" if runtime is None else str(runtime),
        "series": metadata.series or "",
        "studio": metadata.studio or "",
        "publisher": metadata.publisher or "",
        "actor": actors[0] if actors else "",
        "actors": ",".join(actors),
        "directors": ",".join(directors),
        "tags": ",".join(tags),
        "poster": metadata.poster_url or "",
        "cover": metadata.thumb_url or "",
        "trailer": metadata.trailer_url or "",
        "content_type": str(info.content_type) if info is not None else "",
        "mosaic?": str(info.mosaic) if info is not None and info.mosaic else "",
        "def?": info.definition if info is not None and info.definition else "",
        "cd?": str(cd) if cd is not None else "",
        "sub?": "C" if info is not None and info.has_subtitle else "",
        "xml_actor": _actor_fragment(actors),
        "xml_tag": _element_lines("tag", tags),
        "xml_genre": _element_lines("genre", tags),
        "xml_director": _element_lines("director", directors),
        "xml_external_id": _external_id_fragment(external_ids),
    }


class NfoEngine(TemplateEngine):
    """片段占位符按已转义片段写入, 其余取值转义后写入; 无占位符的组按字面输出; 结尾保证一个换行."""

    subject = _TEMPLATE_NAME
    plain_groups_literal = True

    def fill(self, ctx: TemplateContext) -> str:
        escaped = {
            name: value if name in NFO_FRAGMENT_PLACEHOLDERS else _escape_xml(value)
            for name, value in ctx.variables.items()
        }
        return super().fill(TemplateContext(variables=escaped))

    def clean(self, filled: str, ctx: TemplateContext) -> str:
        return filled if filled.endswith("\n") else f"{filled}\n"


NFO_CONTENT_TEMPLATE_DEFAULT = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<movie>
[  <plot>{plot}</plot>
  <outline>{plot}</outline>
][  <premiered>{release}</premiered>
  <releasedate>{release}</releasedate>
[  <year>{year}</year>
]]  <num>{number}</num>
[  <title>{display_title}</title>
  <originaltitle>{display_title}</originaltitle>
  <sorttitle>{display_title}</sorttitle>
]  <mpaa>JP-18+</mpaa>
{xml_actor}[  <rating>{rating}</rating>
  <criticrating>{criticrating}</criticrating>
][  <runtime>{runtime}</runtime>
][  <series>{series}</series>
  <set>
    <name>{series}</name>
  </set>
][  <studio>{studio}</studio>
  <maker>{studio}</maker>
][  <publisher>{publisher}</publisher>
  <label>{publisher}</label>
]{xml_tag}{xml_genre}[  <poster>{poster}</poster>
][  <cover>{cover}</cover>
][  <trailer>{trailer}</trailer>
]{xml_director}{xml_external_id}</movie>
"""

_PROBE_TEXT = "probe & < > \" '"
_PROBE_FRAGMENT = "<probe>x</probe>\n"

_XML_ERROR_HINTS: tuple[tuple[str, str], ...] = (
    ("mismatched tag", "标签开合不匹配"),
    ("unclosed token", "标签或属性没有闭合"),
    ("unclosed CDATA", "CDATA 没有闭合"),
    ("undefined entity", "有未定义的实体, 例如 `&nbsp;` 要写成 `&#160;`"),
    ("unbound prefix", "用了未绑定的 XML 前缀"),
    (
        "not well-formed (invalid token)",
        "字面量里的 `&` 要写成 `&amp;`、`<` 要写成 `&lt;` (取值由引擎转义, 字面量不转义); 也检查属性值里是否混入了 `<`",
    ),
    ("no element found", "正文只剩可选组, 全部取值为空时没有任何元素"),
    ("junk after document element", "根元素之后还有内容"),
    ("syntax error", "正文不是一个完整的 XML 文档, 根元素之外出现了内容"),
)
_XML_ERROR_POSITION = re.compile(r": line \d+, column \d+$")


def _xml_error_message(exc: ET.ParseError, subject: str) -> str:
    """按原因给中文提示; 不回报行列号, 它来自探针渲染结果, 与用户模板的行列对不上."""
    raw = _XML_ERROR_POSITION.sub("", str(exc))
    for marker, hint in _XML_ERROR_HINTS:
        if marker in raw:
            return f"{subject}渲染后不是合法 XML: {hint}"
    return f"{subject}渲染后不是合法 XML: {raw}"


def _require_well_formed(rendered: str, subject: str) -> None:
    if not rendered.strip():
        raise ValueError(f"{subject}渲染后没有任何元素: 正文只剩可选组时, 全部取值为空不会写出内容")
    try:
        ET.fromstring(rendered)
    except ET.ParseError as exc:
        raise ValueError(_xml_error_message(exc, subject)) from exc


def _fragment_in_tag(source: str) -> str | None:
    """片段出现在标签内部 (属性位置) 的模板, 直接给出原因.

    预检只看源文本: 该位置之前最后一个 `<` 若在最后一个 `>` 之后, 说明它落在某个标签里.
    标签属性值里出现 `>` 的少数写法会漏判, 那时仍由探针的 XML 解析兜底.
    """
    for name in NFO_FRAGMENT_PLACEHOLDERS:
        start = 0
        while (index := source.find(f"{{{name}", start)) != -1:
            if source.rfind("<", 0, index) > source.rfind(">", 0, index):
                return name
            start = index + 1
    return None


def _probe_variants() -> Iterator[dict[str, str]]:
    """全满、逐个占位符置空、全空: 覆盖「某个值缺失后标签配对变化」这类模板.

    探针是启发式收紧, 会造出真实不可达的取值组合 (例如 `{display_title}` 与 `{title}`
    恒同空非空), 因此可能误拒个别模板; 兜底仍是写出期校验与告警.
    """
    fragments = dict.fromkeys(NFO_FRAGMENT_PLACEHOLDERS, _PROBE_FRAGMENT)
    all_full = {**dict.fromkeys(NFO_TEXT_PLACEHOLDERS, _PROBE_TEXT), **fragments}
    yield all_full
    for name in (*NFO_TEXT_PLACEHOLDERS, *NFO_FRAGMENT_PLACEHOLDERS):
        yield {**all_full, name: ""}
    yield dict.fromkeys((*NFO_TEXT_PLACEHOLDERS, *NFO_FRAGMENT_PLACEHOLDERS), "")


def normalize_nfo_content_template(value: str | None) -> str | None:
    """空白 nfo_content_template 视为未设置 (使用默认模板)."""
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def validate_nfo_content_template(value: str) -> str:
    """空串合法. 校验语法、占位符名, 以及各探针取值下渲染结果的 XML 良构性."""
    if not value.strip():
        return value
    unknown = [name for name in placeholder_names(value, _TEMPLATE_NAME) if name not in NFO_PLACEHOLDERS]
    if unknown:
        raise ValueError(f"{_TEMPLATE_NAME}包含未知占位符: {', '.join(unknown)}")
    misplaced = _fragment_in_tag(value)
    if misplaced is not None:
        raise ValueError(f"{_TEMPLATE_NAME}里的 {{{misplaced}}} 写在标签内部: 片段占位符只能写在元素内容位置")
    for variables in _probe_variants():
        _require_well_formed(NfoEngine(value).render(TemplateContext(variables=variables)), _TEMPLATE_NAME)
    return value


NfoContentTemplate = Annotated[str, AfterValidator(validate_nfo_content_template)]


def render_nfo_content(
    template: str | None,
    metadata: Metadata,
    *,
    file_info: FileInfo | None = None,
    video_name: str = "",
) -> str:
    """未设置模板时渲染默认模板. 结果不是良构 XML 时抛出 ValueError, 不写出错误正文."""
    ctx = TemplateContext(variables=_nfo_variables(metadata, file_info, video_name))
    rendered = NfoEngine(normalize_nfo_content_template(template) or NFO_CONTENT_TEMPLATE_DEFAULT).render(ctx)
    _require_well_formed(rendered, "NFO 正文")
    return rendered


__all__ = [
    "NFO_CONTENT_TEMPLATE_DEFAULT",
    "NFO_FRAGMENT_PLACEHOLDERS",
    "NFO_PLACEHOLDERS",
    "NFO_PLACEHOLDER_MAP_KEYS",
    "NFO_TEXT_PLACEHOLDERS",
    "NfoContentTemplate",
    "normalize_nfo_content_template",
    "render_nfo_content",
    "validate_nfo_content_template",
]
