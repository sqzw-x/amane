"""tests for amane.organize -- 路径模板解析.

路径统一由 pytest tmp_path 派生 (跨平台绝对路径, Windows 上带盘符).
不用 POSIX 硬编码绝对路径 (如 /media): 它们在 Windows 上是"无盘符的根相对路径",
与 resolve() 后补盘符的绝对路径不相等, 也会让 safe_dirs 的跨盘判定误判.
"""

from pathlib import Path
from typing import NamedTuple

import pytest

from amane.db.models import Library, Metadata
from amane.enums import LinkMode
from amane.organize import (
    VIDEO_TEMPLATE_DEFAULT,
    normalize_link_template,
    render_path_template,
    resolve_paths,
    resolve_subtitle_path,
    validate_path_template,
)
from amane.parsing import parse_file_info


@pytest.fixture
def media(tmp_path: Path) -> Path:
    """媒体库根 (tmp_path 已 resolve, 无符号链接残留, 保证 resolve 后路径不变形)."""
    return tmp_path.resolve() / "media"


@pytest.fixture
def other(tmp_path: Path) -> Path:
    """媒体根之外的目录, 作绝对模板落点 / safe_dirs (多盘分存场景)."""
    return tmp_path.resolve() / "out"


@pytest.fixture
def etc(tmp_path: Path) -> Path:
    """媒体根之外的目录, 用于"逃逸所有边界"的反例."""
    return tmp_path.resolve() / "etc"


def _meta(**kwargs) -> Metadata:
    """创建测试用 Metadata, 填充默认值.

    番号固定 ABC-123, 与源文件名里的 MIDV-123 无关: 模板 {number} 来自刮削元数据, 不是 parse_file_info.
    """
    defaults = {
        "number": "ABC-123",
        "title": "Test Title",
        "actors": ["Actor1", "Actor2"],
        "studio": "StudioX",
        "release": "2024-01-15",
    }
    defaults.update(kwargs)
    return Metadata(**defaults)


class _RenderCase(NamedTuple):
    """引擎核心: 源路径 parse_file_info → 按 template 渲染, expected 为相对库根的 posix 路径."""

    source: str | None  # None: 不传 file_info
    template: str
    expected: str


# 模板 + 源文件 → 整理后相对库根路径. {number}/{studio} 来自 _meta, 标记来自 source.
RENDER_CASES: tuple[_RenderCase, ...] = (
    # --- 默认模板: [-CD{cd?}][-{sub?}] 两个并列组 ---
    _RenderCase("MIDV-123.mp4", VIDEO_TEMPLATE_DEFAULT, "StudioX/ABC-123/ABC-123.mp4"),
    _RenderCase("MIDV-123-CD1.mp4", VIDEO_TEMPLATE_DEFAULT, "StudioX/ABC-123/ABC-123-CD1.mp4"),
    _RenderCase("MIDV-123-C.mp4", VIDEO_TEMPLATE_DEFAULT, "StudioX/ABC-123/ABC-123-C.mp4"),
    _RenderCase("MIDV-123-CD1-C.mp4", VIDEO_TEMPLATE_DEFAULT, "StudioX/ABC-123/ABC-123-CD1-C.mp4"),
    _RenderCase("MIDV-123-UC.mp4", VIDEO_TEMPLATE_DEFAULT, "StudioX/ABC-123/ABC-123-C.mp4"),
    # --- 单占位符可选组: 空则连字面量一起省略 ---
    _RenderCase("MIDV-123-CD2.mp4", "{number}/{number}[-Part {cd?}].{ext}", "ABC-123/ABC-123-Part 2.mp4"),
    _RenderCase("MIDV-123.mp4", "{number}/{number}[-Part {cd?}].{ext}", "ABC-123/ABC-123.mp4"),
    _RenderCase("MIDV-123-C.mp4", "{number}/{number}[-{sub?}].{ext}", "ABC-123/ABC-123-C.mp4"),
    _RenderCase("MIDV-123.mp4", "{number}/{number}[-{sub?}].{ext}", "ABC-123/ABC-123.mp4"),
    _RenderCase("ABC-123-4K.mp4", "{number}[[{def?}]].{ext}", "ABC-123[4K].mp4"),
    _RenderCase("ABC-123.mp4", "{number}[[{def?}]].{ext}", "ABC-123.mp4"),
    # --- 同组多个可空: 有一个非空就渲染, 空的不输出 ---
    _RenderCase("MIDV-123-UC.mp4", "{number}[-{mosaic?|uncensored=U}{sub?}].{ext}", "ABC-123-UC.mp4"),
    _RenderCase("MIDV-123-C-U.mp4", "{number}[-{mosaic?|uncensored=U}{sub?}].{ext}", "ABC-123-UC.mp4"),
    _RenderCase("MIDV-123-U.mp4", "{number}[-{mosaic?|uncensored=U}{sub?}].{ext}", "ABC-123-U.mp4"),
    _RenderCase("MIDV-123-C.mp4", "{number}[-{mosaic?|uncensored=U}{sub?}].{ext}", "ABC-123-C.mp4"),
    _RenderCase("MIDV-123.mp4", "{number}[-{mosaic?|uncensored=U}{sub?}].{ext}", "ABC-123.mp4"),
    _RenderCase("[破解]MIDV-123.mp4", "{number}[-{mosaic?|uncensored=U}{sub?}].{ext}", "ABC-123-cracked.mp4"),
    # 字面量跟着整组: 仅中字时仍带上 -CD
    _RenderCase("MIDV-123-C.mp4", "{number}[-CD{cd?}{sub?}].{ext}", "ABC-123-CDC.mp4"),
    _RenderCase("MIDV-123-CD1-C.mp4", "{number}[-CD{cd?}{sub?}].{ext}", "ABC-123-CD1C.mp4"),
    # --- 嵌套组: 外层只看自己的直接占位符 ---
    _RenderCase("MIDV-123-U-4K.mp4", "{number}[-{mosaic?|uncensored=U}[-{def?}]].{ext}", "ABC-123-U-4K.mp4"),
    _RenderCase("MIDV-123-U.mp4", "{number}[-{mosaic?|uncensored=U}[-{def?}]].{ext}", "ABC-123-U.mp4"),
    _RenderCase("ABC-123-4K.mp4", "{number}[-{mosaic?|uncensored=U}[-{def?}]].{ext}", "ABC-123.mp4"),
    # --- 值映射 ---
    _RenderCase(
        "MIDV-123-無碼.mp4",
        "{mosaic?}/{number}[-{mosaic?|uncensored=U,cracked=破解}].{ext}",
        "uncensored/ABC-123-U.mp4",
    ),
    _RenderCase(
        "[破解]MIDV-123.mp4",
        "{mosaic?}/{number}[-{mosaic?|uncensored=U,cracked=破解}].{ext}",
        "cracked/ABC-123-破解.mp4",
    ),
    _RenderCase("MIDV-123-C.mp4", "{number}/{number}[-{sub?|C=中字}].{ext}", "ABC-123/ABC-123-中字.mp4"),
    _RenderCase("ABC-123-4K.mp4", "{number}[[{def?|4K=2160p}]].{ext}", "ABC-123[2160p].mp4"),
    _RenderCase("MIDV-123-CD1.mp4", "{number}/{number}[-第{cd?|1=一}集].{ext}", "ABC-123/ABC-123-第一集.mp4"),
    _RenderCase("MIDV-123-CD2.mp4", "{number}/{number}[-第{cd?|1=一}集].{ext}", "ABC-123/ABC-123-第2集.mp4"),
    _RenderCase("MIDV-123-無碼.mp4", "{number}[-{mosaic?|uncensored=}].{ext}", "ABC-123.mp4"),
    # --- file 相位未检出为空, 空路径段折叠 ---
    _RenderCase("MIDV-123-4K-無碼.mp4", "{mosaic?}/{def?}/{number}.{ext}", "uncensored/4K/ABC-123.mp4"),
    _RenderCase("HEYZO-123-1080p.mp4", "{mosaic?}/{def?}/{number}.{ext}", "1080p/ABC-123.mp4"),
    _RenderCase("ABC-123.mp4", "{mosaic?}/{def?}/{number}.{ext}", "ABC-123.mp4"),
    _RenderCase("[破解]MIDV-123.mp4", "{mosaic?}/{number}.{ext}", "cracked/ABC-123.mp4"),
    _RenderCase("MIDV-123_4K_无码.mp4", "{mosaic?}/{def?}/{number}.{ext}", "uncensored/4K/ABC-123.mp4"),
    _RenderCase("/media/uncensored/MIDV-123.mp4", "{mosaic?}/{def?}/{number}.{ext}", "uncensored/ABC-123.mp4"),
    _RenderCase("/media/4K/MIDV-123.mp4", "{mosaic?}/{def?}/{number}.{ext}", "ABC-123.mp4"),
    _RenderCase(None, "{mosaic?}/{def?}/{number}.{ext}", "ABC-123.mp4"),
    # {cd} 不是 {cd?}, 视为未知 key
    _RenderCase("MIDV-123-CD1.mp4", "{number}[-CD{cd}].{ext}", "ABC-123-CDUnknown.mp4"),
)


@pytest.mark.parametrize("case", RENDER_CASES, ids=lambda c: f"{c.source} -> {c.expected}")
def test_render_from_file(case: _RenderCase, media: Path) -> None:
    """模板引擎核心表: source → FileInfo → template → 相对库根路径."""
    wp = Library(name="t", path=str(media), video_template=case.template)
    file_info = parse_file_info(case.source) if case.source is not None else None
    result = resolve_paths(wp, _meta(), ext="mp4", file_info=file_info)
    assert result.video == media.joinpath(*case.expected.split("/"))


class TestResolvePathsBasic:
    """基本模板渲染."""

    def test_library_default_video_template(self):
        assert Library(name="t", path="/m").video_template == VIDEO_TEMPLATE_DEFAULT

    def test_default_video_template(self, media: Path):
        wp = Library(name="t", path=str(media), video_template="{studio}/{number}/{number}.{ext}")
        meta = _meta()
        result = resolve_paths(wp, meta, ext="mp4")

        assert result.video == media / "StudioX" / "ABC-123" / "ABC-123.mp4"

    def test_relative_path_resolved_to_watch_path(self, media: Path):
        wp = Library(name="t", path=str(media / "data" / "videos"), video_template="{number}/{number}.{ext}")
        meta = _meta()
        result = resolve_paths(wp, meta, ext="mkv")

        assert result.video == media / "data" / "videos" / "ABC-123" / "ABC-123.mkv"

    def test_absolute_template(self, media: Path, other: Path):
        wp = Library(
            name="t",
            path=str(media),
            video_template=str(other / "{studio}" / "{number}" / "{number}.{ext}"),
        )
        meta = _meta()
        result = resolve_paths(wp, meta, ext="mp4", safe_dirs=[other])

        assert result.video == other / "StudioX" / "ABC-123" / "ABC-123.mp4"


class TestOptionalGroups:
    """路径解析边界: 可选组不改附属默认; 结构错误在写入时拒绝."""

    def test_group_does_not_affect_nfo(self, media: Path):
        wp = Library(name="t", path=str(media), video_template="{number}/{number}[-CD{cd?}].{ext}")
        result = resolve_paths(wp, _meta(), ext="mp4", cd=1)
        assert result.nfo == media / "ABC-123" / "ABC-123.nfo"

    def test_unclosed_group_rejected(self):
        with pytest.raises(ValueError, match="unclosed optional group"):
            validate_path_template("{number}[-CD{cd?}.{ext}")


class TestValueMapping:
    """`{name|k=v}` 值替换: 未列出的 key 保持原值; 空源不映射; 映射成空则省略可选组."""

    def test_unmapped_key_keeps_canonical(self):
        rendered = render_path_template("{mosaic?|cracked=破解}", {"mosaic?": "uncensored"})
        assert rendered == "uncensored"

    def test_empty_source_skips_mapping(self):
        rendered = render_path_template("{mosaic?|uncensored=U}", {"mosaic?": ""})
        assert rendered == ""

    def test_map_present_to_empty_omits_group(self):
        rendered = render_path_template("x[{mosaic?|uncensored=}]", {"mosaic?": "uncensored"})
        assert rendered == "x"

    def test_unknown_metadata_can_be_mapped(self, media: Path):
        wp = Library(name="t", path=str(media), video_template="{studio|Unknown=未分类}/{number}.{ext}")
        result = resolve_paths(wp, _meta(studio=None), ext="mp4")
        assert result.video == media / "未分类" / "ABC-123.mp4"


class TestValidatePathTemplate:
    def test_default_is_valid(self):
        assert validate_path_template(VIDEO_TEMPLATE_DEFAULT) == VIDEO_TEMPLATE_DEFAULT

    def test_nested_groups(self):
        assert validate_path_template("{number}[-{mosaic?}[-{def?}]]") == "{number}[-{mosaic?}[-{def?}]]"

    def test_unclosed_placeholder(self):
        with pytest.raises(ValueError, match="unclosed placeholder"):
            validate_path_template("{number")

    @pytest.mark.parametrize(
        "template",
        [
            "{mosaic?|uncensored=U,cracked=破解}",
            "{mosaic?|cracked=破解}",
            "{sub?|C=中字}",
            "{def?|4K=2160p,1080p=FHD}",
            "{cd?|1=一,2=二}",
            "{studio|Unknown=未分类}",
            "{mosaic?|uncensored=}",
        ],
    )
    def test_value_mapping_accepted(self, template: str):
        assert validate_path_template(template) == template

    @pytest.mark.parametrize(
        ("template", "match"),
        [
            ("{mosaic?|}", "empty placeholder mapping"),
            ("{mosaic?|uncensored}", "invalid placeholder mapping"),
            ("{mosaic?|=U}", "empty mapping key"),
            ("{mosaic?|uncensored=U,uncensored=V}", "duplicate mapping key"),
            ("{mosaic?|uncencored=U}", "unknown mapping key"),
            ("{mosaic?|censored=有码}", "unknown mapping key"),
            ("{def?|2160p=4K}", "unknown mapping key"),
            ("{sub?|CH=中字}", "unknown mapping key"),
            ("{mosaic?|uncensored=U,}", "invalid placeholder mapping"),
        ],
    )
    def test_value_mapping_rejected(self, template: str, match: str):
        with pytest.raises(ValueError, match=match):
            validate_path_template(template)


class TestResolvePathsDefaults:
    """默认推导测试 (模板字段为 None)."""

    def test_thumb_default(self, media: Path):
        wp = Library(name="t", path=str(media), video_template="{studio}/{number}/{number}.{ext}")
        meta = _meta()
        result = resolve_paths(wp, meta, ext="mp4")

        assert result.thumb == media / "StudioX" / "ABC-123" / "thumb.jpg"

    def test_poster_default(self, media: Path):
        wp = Library(name="t", path=str(media), video_template="{studio}/{number}/{number}.{ext}")
        meta = _meta()
        result = resolve_paths(wp, meta, ext="mp4")

        assert result.poster == media / "StudioX" / "ABC-123" / "poster.jpg"

    def test_fanart_default(self, media: Path):
        wp = Library(name="t", path=str(media), video_template="{studio}/{number}/{number}.{ext}")
        meta = _meta()
        result = resolve_paths(wp, meta, ext="mp4")

        assert result.fanart == media / "StudioX" / "ABC-123" / "fanart.jpg"

    def test_extrafanart_default(self, media: Path):
        wp = Library(name="t", path=str(media), video_template="{studio}/{number}/{number}.{ext}")
        meta = _meta()
        result = resolve_paths(wp, meta, ext="mp4")

        assert result.extrafanart_dir == media / "StudioX" / "ABC-123" / "extrafanart"

    def test_nfo_default(self, media: Path):
        wp = Library(name="t", path=str(media), video_template="{studio}/{number}/{number}.{ext}")
        meta = _meta()
        result = resolve_paths(wp, meta, ext="mp4")

        assert result.nfo == media / "StudioX" / "ABC-123" / "ABC-123.nfo"

    def test_trailer_default(self, media: Path):
        wp = Library(name="t", path=str(media), video_template="{studio}/{number}/{number}.{ext}")
        meta = _meta()
        result = resolve_paths(wp, meta, ext="mp4")

        assert result.trailer == media / "StudioX" / "ABC-123" / "trailer.mp4"

    def test_subtitle_default_keeps_raw_name(self, media: Path):
        wp = Library(name="t", path=str(media), video_template="{studio}/{number}/{number}.{ext}")
        meta = _meta()
        video = resolve_paths(wp, meta, ext="mp4")
        sub = resolve_subtitle_path(wp, meta, Path("/inbox/MIDV-123.zh.srt"), video_dir=video.video.parent)
        assert sub == media / "StudioX" / "ABC-123" / "MIDV-123.zh.srt"


class TestResolvePathsCustomTemplates:
    """自定义模板测试."""

    def test_custom_thumb_template_absolute(self, media: Path, other: Path):
        wp = Library(
            name="t",
            path=str(media),
            video_template="{studio}/{number}/{number}.{ext}",
            thumb_template=str(other / "{number}" / "thumb.jpg"),
        )
        meta = _meta()
        result = resolve_paths(wp, meta, ext="mp4", safe_dirs=[other])

        assert result.thumb == other / "ABC-123" / "thumb.jpg"

    def test_custom_thumb_template_with_video_dir(self, media: Path):
        wp = Library(
            name="t",
            path=str(media),
            video_template="{studio}/{number}/{number}.{ext}",
            thumb_template="{video_dir}/images/thumb.jpg",
        )
        meta = _meta()
        result = resolve_paths(wp, meta, ext="mp4")

        assert result.thumb == media / "StudioX" / "ABC-123" / "images" / "thumb.jpg"

    def test_custom_nfo_template(self, media: Path):
        wp = Library(
            name="t",
            path=str(media),
            video_template="{number}/{number}.{ext}",
            nfo_template="{video_dir}/metadata/{number}.nfo",
        )
        meta = _meta()
        result = resolve_paths(wp, meta, ext="mp4")

        assert result.nfo == media / "ABC-123" / "metadata" / "ABC-123.nfo"

    def test_custom_extrafanart_template_relative(self, media: Path):
        wp = Library(
            name="t",
            path=str(media),
            video_template="{number}/{number}.{ext}",
            extrafanart_template="gallery/{number}",
        )
        meta = _meta()
        result = resolve_paths(wp, meta, ext="mp4")

        assert result.extrafanart_dir == media / "gallery" / "ABC-123"


class TestResolvePathsEdgeCases:
    """边界情况."""

    def test_missing_metadata_fields(self, media: Path):
        wp = Library(name="t", path=str(media), video_template="{studio}/{series}/{number}.{ext}")
        meta = _meta(studio=None, series=None)
        result = resolve_paths(wp, meta, ext="mp4")

        assert result.video == media / "Unknown" / "Unknown" / "ABC-123.mp4"

    def test_empty_ext(self, media: Path):
        wp = Library(name="t", path=str(media), video_template="{number}/{number}.{ext}")
        meta = _meta()
        result = resolve_paths(wp, meta, ext="")

        assert result.video == media / "ABC-123" / "ABC-123."

    def test_video_dir_computed_from_absolute_video(self, media: Path, other: Path):
        wp = Library(name="t", path=str(media), video_template=str(other / "{number}" / "{number}.{ext}"))
        meta = _meta()
        result = resolve_paths(wp, meta, ext="mp4", safe_dirs=[other])

        # video_dir should be other/ABC-123/
        assert result.thumb == other / "ABC-123" / "thumb.jpg"

    def test_year_variable(self, media: Path):
        wp = Library(name="t", path=str(media), video_template="{year}/{number}/{number}.{ext}")
        meta = _meta(release="2024-05-01")
        result = resolve_paths(wp, meta, ext="mp4")

        assert result.video == media / "2024" / "ABC-123" / "ABC-123.mp4"


class TestSourceVariables:
    """源文件变量 {raw_dir} / {raw_name}."""

    def test_raw_dir_is_source_parent_name(self, media: Path):
        """{raw_dir}: A/B/C.mp4 → B."""
        arch = media / "archive"
        wp = Library(name="t", path=str(arch), video_template=str(arch / "{raw_dir}" / "{number}.{ext}"))
        meta = _meta()
        result = resolve_paths(wp, meta, ext="mp4", source_path=media / "A" / "B" / "C.mp4")
        assert result.video == arch / "B" / "ABC-123.mp4"

    def test_raw_name_is_source_stem(self, media: Path):
        """{raw_name}: A/B.mp4 → B."""
        wp = Library(name="t", path=str(media), video_template="{studio}/{number}/{raw_name}.{ext}")
        meta = _meta()
        result = resolve_paths(wp, meta, ext="mp4", source_path=media / "A" / "B.mp4")
        assert result.video == media / "StudioX" / "ABC-123" / "B.mp4"

    def test_raw_dir_empty_when_no_source_path(self, media: Path):
        """不传 source_path 时 {raw_dir} 降级为空串 (非首段, 多余分隔符被 resolve 折叠)."""
        wp = Library(name="t", path=str(media), video_template="sub/{raw_dir}/{number}.{ext}")
        meta = _meta()
        result = resolve_paths(wp, meta, ext="mp4")
        assert result.video == media / "sub" / "ABC-123.mp4"

    def test_raw_name_empty_when_no_source_path(self, media: Path):
        """不传 source_path 时 {raw_name} 降级为空串."""
        wp = Library(name="t", path=str(media), video_template="sub/{raw_name}.{ext}")
        meta = _meta()
        result = resolve_paths(wp, meta, ext="mp4")
        assert result.video == media / "sub" / ".mp4"

    def test_dir_alias_matches_raw_dir(self, media: Path):
        """{dir} 与 {raw_dir} 同值."""
        arch = media / "archive"
        wp = Library(name="t", path=str(arch), video_template=str(arch / "{dir}" / "{number}.{ext}"))
        meta = _meta()
        result = resolve_paths(wp, meta, ext="mp4", source_path=media / "A" / "B" / "C.mp4")
        assert result.video == arch / "B" / "ABC-123.mp4"


class TestPathTraversalProtection:
    """路径逃逸防护."""

    def test_relative_template_with_dotdot_raises(self, media: Path):
        """相对模板中含 .. 导致逃逸时抛出 ValueError"""
        wp = Library(name="t", path=str(media / "incoming"), video_template="../../etc/{number}.{ext}")
        meta = _meta()
        with pytest.raises(ValueError, match="Path traversal detected"):
            resolve_paths(wp, meta, ext="mp4")

    def test_metadata_title_sanitized_no_escape(self, media: Path):
        """元数据字段中的 ../ 被 _safe 清理, 不会导致逃逸"""
        wp = Library(name="t", path=str(media / "incoming"), video_template="{title}/{number}.{ext}")
        meta = _meta(title="../../escape")
        result = resolve_paths(wp, meta, ext="mp4")
        # _safe 将 / 替换为空格, 结果安全地在 base 内
        assert str(result.video).startswith(str(media / "incoming"))

    def test_absolute_template_rejected_without_safe_dir(self, media: Path, other: Path):
        """绝对路径模板逃逸 base 且无 safe_dirs 覆盖时, 抛出 ValueError"""
        wp = Library(name="t", path=str(media), video_template=str(other / "{number}" / "{number}.{ext}"))
        meta = _meta()
        with pytest.raises(ValueError, match="Path traversal detected"):
            resolve_paths(wp, meta, ext="mp4")

    def test_absolute_template_allowed_within_safe_dir(self, media: Path, other: Path):
        """绝对路径模板落在 safe_dirs 内时允许 (多盘分存场景)"""
        wp = Library(name="t", path=str(media), video_template=str(other / "{number}" / "{number}.{ext}"))
        meta = _meta()
        result = resolve_paths(wp, meta, ext="mp4", safe_dirs=[other])
        assert result.video == other / "ABC-123" / "ABC-123.mp4"

    def test_absolute_template_escaping_safe_dir_rejected(self, media: Path, other: Path, etc: Path):
        """绝对路径模板逃逸所有 safe_dirs 时, 仍抛出 ValueError"""
        wp = Library(name="t", path=str(media), video_template=str(etc / "{number}" / "{number}.{ext}"))
        meta = _meta()
        with pytest.raises(ValueError, match="Path traversal detected"):
            resolve_paths(wp, meta, ext="mp4", safe_dirs=[other])

    def test_absolute_template_within_base_ok(self, media: Path):
        """绝对路径模板落在 base_path 内时无需 safe_dirs"""
        wp = Library(name="t", path=str(media), video_template=str(media / "{number}" / "{number}.{ext}"))
        meta = _meta()
        result = resolve_paths(wp, meta, ext="mp4")
        assert result.video == media / "ABC-123" / "ABC-123.mp4"

    def test_relative_template_within_base_ok(self, media: Path):
        """相对模板在 base 内正常工作"""
        wp = Library(name="t", path=str(media), video_template="sub/dir/{number}.{ext}")
        meta = _meta()
        result = resolve_paths(wp, meta, ext="mp4")
        assert result.video == media / "sub" / "dir" / "ABC-123.mp4"


class TestNormalizeLinkTemplate:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (None, None),
            ("", None),
            ("   ", None),
            ("/out/{number}/{number}.{ext}", "/out/{number}/{number}.{ext}"),
            ("  /out/{number}.strm  ", "/out/{number}.strm"),
        ],
    )
    def test_blank_is_unset(self, raw: str | None, expected: str | None):
        assert normalize_link_template(raw) == expected


class TestResolvePathsLink:
    """link_template 渲染: 库外入口 + {link_dir} 换根."""

    def test_unset_link_matches_video_dir(self, media: Path):
        wp = Library(name="t", path=str(media), video_template="{studio}/{number}/{number}.{ext}")
        result = resolve_paths(wp, _meta(), ext="mp4")
        assert result.link is None
        assert result.nfo == result.video.parent / "ABC-123.nfo"

    def test_strm_forces_suffix_and_sidecars_follow_link_dir(self, media: Path, other: Path):
        wp = Library(
            name="t",
            path=str(media),
            video_template="{studio}/{number}/{number}.{ext}",
            link_template=str(other / "{studio}" / "{number}" / "{number}.{ext}"),
            link_mode=LinkMode.STRM,
        )
        result = resolve_paths(wp, _meta(), ext="mp4", safe_dirs=[other])
        assert result.video == media / "StudioX" / "ABC-123" / "ABC-123.mp4"
        assert result.link == other / "StudioX" / "ABC-123" / "ABC-123.strm"
        assert result.nfo == other / "StudioX" / "ABC-123" / "ABC-123.nfo"
        assert result.poster == other / "StudioX" / "ABC-123" / "poster.jpg"

    def test_symlink_keeps_video_extension(self, media: Path, other: Path):
        wp = Library(
            name="t",
            path=str(media),
            video_template="{number}/{number}.{ext}",
            link_template=str(other / "{number}" / "{number}.{ext}"),
            link_mode=LinkMode.SYMLINK,
        )
        result = resolve_paths(wp, _meta(), ext="mkv", safe_dirs=[other])
        assert result.link == other / "ABC-123" / "ABC-123.mkv"

    def test_cd_in_link_only_if_template_asks(self, media: Path, other: Path):
        wp = Library(
            name="t",
            path=str(media),
            video_template="{number}/{number}[-CD{cd?}].{ext}",
            link_template=str(other / "{number}" / "{number}[-CD{cd?}].{ext}"),
            link_mode=LinkMode.STRM,
        )
        result = resolve_paths(wp, _meta(), ext="mp4", cd=2, safe_dirs=[other])
        assert result.video == media / "ABC-123" / "ABC-123-CD2.mp4"
        assert result.link == other / "ABC-123" / "ABC-123-CD2.strm"

    def test_custom_video_dir_stays_with_real_video(self, media: Path, other: Path):
        wp = Library(
            name="t",
            path=str(media),
            video_template="{number}/{number}.{ext}",
            link_template=str(other / "{number}" / "{number}.{ext}"),
            nfo_template="{video_dir}/{number}.nfo",
        )
        result = resolve_paths(wp, _meta(), ext="mp4", safe_dirs=[other])
        assert result.nfo == media / "ABC-123" / "ABC-123.nfo"
        assert result.link == other / "ABC-123" / "ABC-123.strm"

    def test_relative_link_rejected(self, media: Path):
        wp = Library(
            name="t",
            path=str(media),
            video_template="{number}/{number}.{ext}",
            link_template="{number}/{number}.{ext}",
        )
        with pytest.raises(ValueError, match="outside the library root"):
            resolve_paths(wp, _meta(), ext="mp4")

    def test_absolute_link_inside_library_rejected(self, media: Path):
        wp = Library(
            name="t",
            path=str(media),
            video_template="{number}/{number}.{ext}",
            link_template=str(media / "links" / "{number}.{ext}"),
        )
        with pytest.raises(ValueError, match="outside the library root"):
            resolve_paths(wp, _meta(), ext="mp4")

    def test_subtitle_default_follows_link_dir(self, media: Path, other: Path):
        wp = Library(
            name="t",
            path=str(media),
            video_template="{studio}/{number}/{number}.{ext}",
            link_template=str(other / "{studio}" / "{number}" / "{number}.{ext}"),
        )
        video = resolve_paths(wp, _meta(), ext="mp4", safe_dirs=[other])
        assert video.link is not None
        sub = resolve_subtitle_path(
            wp,
            _meta(),
            Path("/inbox/MIDV-123.zh.srt"),
            video_dir=video.video.parent,
            link_dir=video.link.parent,
            safe_dirs=[other],
        )
        assert sub == other / "StudioX" / "ABC-123" / "MIDV-123.zh.srt"
