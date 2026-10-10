"""NFO 内容模板: 默认模板与旧生成器逐节点一致; 取值转义; 非法模板在保存时拒绝."""

import xml.etree.ElementTree as ET

import pytest

from amane.db.models import Metadata
from amane.organize.nfo_content import (
    NFO_CONTENT_TEMPLATE_DEFAULT,
    NFO_PLACEHOLDERS,
    normalize_nfo_content_template,
    render_nfo_content,
    validate_nfo_content_template,
)
from amane.organize.template import placeholder_names
from amane.parsing.file_info import parse_file_info

_DEFAULT_OUTPUT = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<movie>
  <plot>line1 &amp; &lt;tag&gt;
line2</plot>
  <outline>line1 &amp; &lt;tag&gt;
line2</outline>
  <premiered>2026-01-15</premiered>
  <releasedate>2026-01-15</releasedate>
  <year>2026</year>
  <num>MIDV-123</num>
  <title>MIDV-123 测试 &amp; &lt;标题&gt;</title>
  <originaltitle>MIDV-123 测试 &amp; &lt;标题&gt;</originaltitle>
  <sorttitle>MIDV-123 测试 &amp; &lt;标题&gt;</sorttitle>
  <mpaa>JP-18+</mpaa>
  <actor>
    <name>演员A</name>
    <type>Actor</type>
  </actor>
  <actor>
    <name>Actor B</name>
    <type>Actor</type>
  </actor>
  <rating>85.0</rating>
  <criticrating>850</criticrating>
  <runtime>120</runtime>
  <series>Series Y</series>
  <set>
    <name>Series Y</name>
  </set>
  <studio>Studio &amp; X</studio>
  <maker>Studio &amp; X</maker>
  <publisher>Pub P</publisher>
  <label>Pub P</label>
  <tag>Drama</tag>
  <tag>Romance</tag>
  <genre>Drama</genre>
  <genre>Romance</genre>
  <poster>http://p/p.jpg</poster>
  <cover>http://t/t.jpg</cover>
  <trailer>http://v/t.mp4</trailer>
  <director>Director Z</director>
  <javdbid>ABC123</javdbid>
</movie>
"""


def _full_metadata() -> Metadata:
    return Metadata(
        number="MIDV-123",
        title="测试 & <标题>",
        actors=["演员A", "Actor B"],
        studio="Studio & X",
        release="2026-01-15",
        runtime=120,
        tags=["Drama", "Romance"],
        series="Series Y",
        scores={"javdb": 85.0},
        directors=["Director Z"],
        publisher="Pub P",
        poster_urls=["http://p/p.jpg"],
        thumb_urls=["http://t/t.jpg"],
        trailer_urls=["http://v/t.mp4"],
        external_ids={"javdb": "ABC123"},
        plot="line1 & <tag>\nline2",
    )


def test_default_template_matches_legacy_output() -> None:
    """默认模板替代旧生成器, 输出逐字节一致 (tag 与 genre 由交错改为分组)."""
    assert render_nfo_content(None, _full_metadata()) == _DEFAULT_OUTPUT


def test_default_template_is_valid() -> None:
    assert validate_nfo_content_template(NFO_CONTENT_TEMPLATE_DEFAULT) == NFO_CONTENT_TEMPLATE_DEFAULT


@pytest.mark.parametrize(
    ("metadata", "missing", "present"),
    [
        (Metadata(number="ABC-1"), ["<plot>", "<title>", "<rating>", "<runtime>", "<actor>"], ["<num>ABC-1</num>"]),
        (
            Metadata(number="ABC-2", title="T", release="Spring 2026", scores={"x": 0.0}, runtime=0, tags=["", "keep"]),
            ["<year>", "<plot>"],
            [
                "<premiered>Spring 2026</premiered>",
                "<title>ABC-2 T</title>",
                "<rating>0.0</rating>",
                "<runtime>0</runtime>",
                "<tag>keep</tag>",
            ],
        ),
        (
            Metadata(number="ABC-3", title="T"),
            ["<year>", "<cover>", "<director>"],
            ["<mpaa>JP-18+</mpaa>", "<title>ABC-3 T</title>"],
        ),
    ],
)
def test_default_template_drops_empty_groups(metadata: Metadata, missing: list[str], present: list[str]) -> None:
    body = render_nfo_content(None, metadata)
    for node in missing:
        assert node not in body
    for node in present:
        assert node in body


@pytest.mark.parametrize(
    ("template", "expected"),
    [
        (None, None),
        ("", None),
        ("  \n ", None),
        ("<movie>{number}</movie>", "<movie>{number}</movie>"),
    ],
)
def test_normalize(template: str | None, expected: str | None) -> None:
    assert normalize_nfo_content_template(template) == expected


@pytest.mark.parametrize(
    "template",
    [
        pytest.param("<movie>{titel}</movie>", id="未知占位符"),
        pytest.param("<movie><plot>{plot}</plot>&nbsp;</movie>", id="未定义实体"),
        pytest.param("<movie>[<a>{plot}][{release}</a>]</movie>", id="单个取值缺失后标签错配"),
        pytest.param("<movie><title>{title}</title>", id="标签未闭合"),
        pytest.param("<movie><title>{title}</movie>", id="层级错乱"),
        pytest.param("<movie>{number}[-CD{cd?}</movie>", id="可选组未闭合"),
        pytest.param("<movie>{number}]</movie>", id="落单的右括号"),
        pytest.param("<movie>{number}</movie", id="占位符未闭合"),
        pytest.param("<movie xml:lang='x' y='{xml_actor}'></movie>", id="片段写进属性"),
        pytest.param("[{plot}<plot>{plot}</plot>]", id="整份正文可能渲染为空"),
    ],
)
def test_validate_rejects(template: str) -> None:
    with pytest.raises(ValueError):
        validate_nfo_content_template(template)


def test_validate_reports_unknown_placeholder_name() -> None:
    with pytest.raises(ValueError, match="titel"):
        validate_nfo_content_template("<movie>{titel}</movie>")


@pytest.mark.parametrize(
    ("template", "hint"),
    [
        ("<movie><plot>{plot}</plot>&nbsp;</movie>", "&#160;"),
        ("<movie xml:lang='x' y='{xml_actor}'></movie>", "元素内容位置"),
        ('<movie><poster url="a>b {xml_actor}"/></movie>', "元素内容位置"),
        ("<movie><!-- {xml_actor} --><num>{number}</num></movie>", "注释或 CDATA"),
        ("<movie><plot><![CDATA[{xml_actor}]]></plot></movie>", "注释或 CDATA"),
        ("<movie><title>A < B {xml_actor}</title></movie>", "未转义的 `<`"),
        ("[<movie>{number}</movie>]", "没有任何元素"),
        ("[{number}<n>{number}</n>]", "完整的 XML 文档"),
        ("<movie><title>{display_title} & {title}</title></movie>", "&amp;"),
    ],
)
def test_validate_messages_are_actionable(template: str, hint: str) -> None:
    with pytest.raises(ValueError, match=hint):
        validate_nfo_content_template(template)


def test_fragment_in_content_position_is_accepted() -> None:
    """片段与取值都在元素内容位置时正常通过."""
    assert validate_nfo_content_template("<movie><num>{number}</num>{xml_actor}{xml_tag}</movie>")


def test_validate_syntax_error_names_the_template() -> None:
    with pytest.raises(ValueError, match="NFO 内容模板"):
        validate_nfo_content_template("<movie>[{number}</movie>")


@pytest.mark.parametrize(
    ("template", "expected"),
    [
        ("<movie><n>{number}</n></movie>", "<movie><n>ABC-1</n></movie>"),
        ("<movie>{title}</movie>", "<movie>T &amp; &lt;x&gt;</movie>"),
        ("<movie><actors>{actors}</actors></movie>", "<movie><actors>A,B</actors></movie>"),
        ("<movie>{xml_tag}</movie>", "<movie>  <tag>T</tag>\n</movie>"),
    ],
)
def test_render_custom_template(template: str, expected: str) -> None:
    assert render_nfo_content(template, Metadata(number="ABC-1", title="T & <x>", actors=["A", "B"], tags=["T"])) == (
        f"{expected}\n"
    )


def test_render_drops_group_when_placeholder_empty() -> None:
    assert render_nfo_content("<movie>[<t>{title}</t>]</movie>", Metadata(number="ABC-1")) == "<movie></movie>\n"


def test_render_escapes_values_only() -> None:
    """字面量原样输出, 取值转义; 解析后文本与原始取值一致."""
    meta = Metadata(number="ABC-1", title="A & B <c> \"d\" 'e'", plot="第一行\n第二行 \x0b")
    body = render_nfo_content("<movie><title>{title}</title><plot>{plot}</plot></movie>", meta)
    root = ET.fromstring(body)
    assert root.findtext("title") == "A & B <c> \"d\" 'e'"
    assert root.findtext("plot") == "第一行\n第二行 "


def test_render_reserved_brackets_via_char_refs() -> None:
    """正文里的字面括号写成字符引用, 模板语言不会把它当作可选组."""
    meta = Metadata(number="ABC-1", title="T")
    body = render_nfo_content("<movie><title>{number} &#91;{title}&#93;</title></movie>", meta)
    assert ET.fromstring(body).findtext("title") == "ABC-1 [T]"


def test_render_fragments() -> None:
    meta = Metadata(
        number="ABC-1",
        actors=["A", "B"],
        tags=["t1"],
        directors=["D"],
        external_ids={"javdb": "X1", "javbus": ""},
    )
    body = render_nfo_content("<movie>{xml_actor}{xml_tag}{xml_genre}{xml_director}{xml_external_id}</movie>", meta)
    root = ET.fromstring(body)
    assert [node.findtext("name") for node in root.findall("actor")] == ["A", "B"]
    assert [node.text for node in root.findall("tag")] == ["t1"]
    assert [node.text for node in root.findall("genre")] == ["t1"]
    assert [node.text for node in root.findall("director")] == ["D"]
    assert [node.text for node in root.findall("javdbid")] == ["X1"]


def test_render_phase_placeholders() -> None:
    """文件相位与路径模板同名同义; 未传 FileInfo 时按番号推定内容类型."""
    info = parse_file_info(text="ABC-123-CD2-C-4K")
    meta = Metadata(number="ABC-123", title="T")
    body = render_nfo_content(
        "<movie><ct>{content_type}</ct><m>{mosaic?}</m><d>{def?}</d><cd>{cd?}</cd><s>{sub?}</s></movie>",
        meta,
        file_info=info,
    )
    root = ET.fromstring(body)
    assert root.findtext("ct") == "censored"
    assert root.findtext("m") == "censored"
    assert root.findtext("d") == "4K"
    assert root.findtext("cd") == "2"
    assert root.findtext("s") == "C"
    assert "censored" in render_nfo_content("<movie><ct>{content_type}</ct></movie>", meta)


def test_render_rejects_broken_skeleton() -> None:
    """写出前的兜底: 未经保存期校验的骨架直接渲染时拒绝输出."""
    with pytest.raises(ValueError, match="标签开合不匹配"):
        render_nfo_content("<movie><title>{title}</movie>", Metadata(number="ABC-1", title="T"))


def test_external_id_skips_invalid_site_name() -> None:
    """站点键不是合法 XML 名时跳过该元素, 其余元素与正文不受影响."""
    meta = Metadata(number="ABC-1", external_ids={"jav db": "1", "r18dev:zh_cn": "2", "javdb": "X1"})
    body = render_nfo_content("<movie>{xml_external_id}<n>{number}</n></movie>", meta)
    root = ET.fromstring(body)
    assert [node.text for node in root.findall("javdbid")] == ["X1"]
    assert root.findtext("n") == "ABC-1"


def test_plain_group_keeps_brackets() -> None:
    """组内没有占位符时没有条件可言, 方括号按字面输出而不是被吞掉."""
    meta = Metadata(number="ABC-1", title="T")
    body = render_nfo_content("<movie><sorttitle>[Blu-ray] {title}</sorttitle></movie>", meta)
    assert ET.fromstring(body).findtext("sorttitle") == "[Blu-ray] T"


def test_plain_group_inside_placeholder_group() -> None:
    """含占位符的组仍然可省略, 组内的字面方括号保留."""
    meta = Metadata(number="ABC-1")
    assert render_nfo_content("<movie>[<t>{title} [HD]</t>]</movie>", meta) == "<movie></movie>\n"
    assert "[HD]" in render_nfo_content("<movie>[<t>{title} [HD]</t>]</movie>", Metadata(number="A-1", title="T"))


@pytest.mark.parametrize(
    ("release", "present"),
    [("2024-01-15", True), ("19-01-01", False), ("abcd-ef", False), ("1024-01-01", True)],
)
def test_year_rule_matches_legacy(release: str, present: bool) -> None:
    """年份取 `re.match(r"(\\d{4})")` 的捕获组, 而不是前四位字符."""
    body = render_nfo_content("<movie>[<year>{year}</year>]</movie>", Metadata(number="A-1", release=release))
    assert ("<year>" in body) is present


def test_video_name() -> None:
    body = render_nfo_content(
        "<movie><t>{video_name}</t></movie>",
        Metadata(number="A-1"),
        video_name="Studio/A-1",
    )
    assert ET.fromstring(body).findtext("t") == "Studio/A-1"


@pytest.mark.parametrize(
    "template",
    [
        pytest.param("<movie>[<a>{plot}][{release}</a>]</movie>", id="文本占位符"),
        pytest.param("<movie>[{xml_actor}<a>][{xml_director}</a>]</movie>", id="片段占位符"),
    ],
)
def test_probe_rejects_template_broken_by_single_empty_value(template: str) -> None:
    """全满与全空两轮探针都通过, 单个取值缺失时标签错配."""
    with pytest.raises(ValueError, match="标签开合不匹配"):
        validate_nfo_content_template(template)


def test_every_placeholder_has_a_value() -> None:
    """目录即取值表键集: 漏配会让正文静默写出 `Unknown`, 而它仍是合法 XML."""
    meta = Metadata(number="A-1", title="T", actors=["A"], tags=["t"], directors=["D"], external_ids={"javdb": "X"})
    for name in NFO_PLACEHOLDERS:
        assert "Unknown" not in render_nfo_content(f"<movie><v>{{{name}}}</v></movie>", meta), name


@pytest.mark.parametrize(
    ("template", "names"),
    [
        ("<movie>{number}{number}</movie>", ("number",)),
        ("<movie>[<t>{title}</t>]{xml_actor}</movie>", ("title", "xml_actor")),
        ("<movie>plain</movie>", ()),
    ],
)
def test_placeholder_names(template: str, names: tuple[str, ...]) -> None:
    assert placeholder_names(template) == names


def test_placeholder_catalog_covers_text_and_fragments() -> None:
    assert "plot" in NFO_PLACEHOLDERS
    assert "video_name" in NFO_PLACEHOLDERS
    assert "xml_actor" in NFO_PLACEHOLDERS
    assert "video_dir" not in NFO_PLACEHOLDERS


def test_default_template_has_no_unknown_placeholder() -> None:
    assert set(placeholder_names(NFO_CONTENT_TEMPLATE_DEFAULT)) <= set(NFO_PLACEHOLDERS)
