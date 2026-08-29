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
    CD_SUFFIX_TEMPLATE_DEFAULT,
    normalize_link_template,
    render_strm_content,
    resolve_paths,
    resolve_subtitle_path,
    validate_cd_suffix_template,
    validate_strm_content_template,
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
    """创建测试用 Metadata, 填充默认值."""
    defaults = {
        "number": "ABC-123",
        "title": "Test Title",
        "actors": ["Actor1", "Actor2"],
        "studio": "StudioX",
        "release": "2024-01-15",
    }
    defaults.update(kwargs)
    return Metadata(**defaults)


class TestResolvePathsBasic:
    """基本模板渲染."""

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

    def test_cd_suffix(self, media: Path):
        wp = Library(name="t", path=str(media), video_template="{number}/{number}.{ext}")
        meta = _meta()
        result = resolve_paths(wp, meta, ext="mp4", cd=1)

        assert result.video == media / "ABC-123" / "ABC-123-CD1.mp4"

    def test_cd_suffix_2(self, media: Path):
        wp = Library(name="t", path=str(media), video_template="{number}/{number}.{ext}")
        meta = _meta()
        result = resolve_paths(wp, meta, ext="mp4", cd=2)

        assert result.video == media / "ABC-123" / "ABC-123-CD2.mp4"


class TestCdSuffixTemplate:
    """CD 分集后缀模板: 仅视频文件名, 用户可配置/关闭."""

    def test_default_suffix(self, media: Path):
        wp = Library(name="t", path=str(media), video_template="{number}/{number}.{ext}")
        meta = _meta()
        result = resolve_paths(wp, meta, ext="mp4", cd=1)
        assert result.video == media / "ABC-123" / "ABC-123-CD1.mp4"

    def test_custom_suffix(self, media: Path):
        wp = Library(
            name="t",
            path=str(media),
            video_template="{number}/{number}.{ext}",
            cd_suffix_template="-Part {cd}",
        )
        meta = _meta()
        result = resolve_paths(wp, meta, ext="mp4", cd=2)
        assert result.video == media / "ABC-123" / "ABC-123-Part 2.mp4"

    def test_empty_suffix_disables(self, media: Path):
        """空串关闭: 识别到分集也不追加 (用户显式选择)."""
        wp = Library(name="t", path=str(media), video_template="{number}/{number}.{ext}", cd_suffix_template="")
        meta = _meta()
        result = resolve_paths(wp, meta, ext="mp4", cd=1)
        assert result.video == media / "ABC-123" / "ABC-123.mp4"

    def test_no_cd_no_suffix_even_with_custom(self, media: Path):
        wp = Library(
            name="t",
            path=str(media),
            video_template="{number}/{number}.{ext}",
            cd_suffix_template="-Part {cd}",
        )
        meta = _meta()
        result = resolve_paths(wp, meta, ext="mp4", cd=None)
        assert result.video == media / "ABC-123" / "ABC-123.mp4"

    def test_suffix_only_affects_video(self, media: Path):
        """附属资源基于 {video_dir}, 不受 CD 后缀影响."""
        wp = Library(name="t", path=str(media), video_template="{number}/{number}.{ext}")
        meta = _meta()
        result = resolve_paths(wp, meta, ext="mp4", cd=1)
        assert result.nfo == media / "ABC-123" / "ABC-123.nfo"


class TestValidateCdSuffixTemplate:
    def test_default_is_valid(self):
        assert validate_cd_suffix_template(CD_SUFFIX_TEMPLATE_DEFAULT) == "-CD{cd}"

    def test_empty_and_whitespace_disables(self):
        assert validate_cd_suffix_template("") == ""
        assert validate_cd_suffix_template("   ") == ""

    def test_missing_cd_placeholder_rejected(self):
        with pytest.raises(ValueError, match="exactly \\{cd\\}"):
            validate_cd_suffix_template("-CD")

    def test_extra_braces_rejected(self):
        with pytest.raises(ValueError, match="exactly \\{cd\\}"):
            validate_cd_suffix_template("-CD{cd}-{n}")

    def test_path_separator_rejected(self):
        with pytest.raises(ValueError, match="path separators"):
            validate_cd_suffix_template("cd{cd}/disc")
        with pytest.raises(ValueError, match="path separators"):
            validate_cd_suffix_template("cd{cd}\\disc")

    def test_valid_custom_forms(self):
        assert validate_cd_suffix_template("-Part{cd}") == "-Part{cd}"
        assert validate_cd_suffix_template("  -第{cd}集  ") == "-第{cd}集"


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


class _RenderCase(NamedTuple):
    """file 相位占位符渲染用例: source 经 parse_file_info 后渲染 template, 断言 video 相对库根路径段."""

    source: str | None  # None 表示不传 file_info (未走 ORGANIZE 的调用方)
    template: str
    expected: tuple[str, ...]


RENDER_CASES: tuple[_RenderCase, ...] = (
    # 文件名标记优先 + 分辨率归一化
    _RenderCase("MIDV-123-4K-無碼.mp4", "{mosaic}/{definition}/{number}.{ext}", ("uncensored", "4K", "ABC-123.mp4")),
    # 无标记时按 content_type 兜底 (无码前缀番号 → uncensored)
    _RenderCase("HEYZO-123-1080p.mp4", "{mosaic}/{definition}/{number}.{ext}", ("uncensored", "1080p", "ABC-123.mp4")),
    # 无标记且非无码 → censored; 无分辨率命中 → Unknown (与其余占位符一致)
    _RenderCase("ABC-123.mp4", "{mosaic}/{definition}/{number}.{ext}", ("censored", "Unknown", "ABC-123.mp4")),
    # 破解/流出标记
    _RenderCase("[破解]MIDV-123.mp4", "{mosaic}/{number}.{ext}", ("cracked", "ABC-123.mp4")),
    # 下划线/汉字邻接的复杂文件名
    _RenderCase("MIDV-123_4K_无码.mp4", "{mosaic}/{definition}/{number}.{ext}", ("uncensored", "4K", "ABC-123.mp4")),
    # 目录名整段可补 mosaic; definition 仍不从目录读
    _RenderCase(
        "/media/uncensored/MIDV-123.mp4",
        "{mosaic}/{definition}/{number}.{ext}",
        ("uncensored", "Unknown", "ABC-123.mp4"),
    ),
    _RenderCase(
        "/media/4K/MIDV-123.mp4", "{mosaic}/{definition}/{number}.{ext}", ("censored", "Unknown", "ABC-123.mp4")
    ),
    # cd 参数省略时从 file_info.cd 回退
    _RenderCase("MIDV-123-CD1.mp4", "{number}/{number}.{ext}", ("ABC-123", "ABC-123-CD1.mp4")),
    # 未走 ORGANIZE 的调用方不传 file_info: 与占位符缺失回退一致
    _RenderCase(None, "{mosaic}/{definition}/{number}.{ext}", ("Unknown", "Unknown", "ABC-123.mp4")),
)


@pytest.mark.parametrize("case", RENDER_CASES, ids=lambda c: c.source or "no-file-info")
def test_file_placeholder_render(case: _RenderCase, media: Path) -> None:
    """file 相位占位符 {mosaic} / {definition}: 来自 parse_file_info (文件名优先, mosaic 可补目录名)."""
    wp = Library(name="t", path=str(media), video_template=case.template)
    meta = _meta()
    file_info = parse_file_info(case.source) if case.source is not None else None
    result = resolve_paths(wp, meta, ext="mp4", file_info=file_info)

    assert result.video == media.joinpath(*case.expected)


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

    def test_cd_suffix_applied_to_link(self, media: Path, other: Path):
        wp = Library(
            name="t",
            path=str(media),
            video_template="{number}/{number}.{ext}",
            link_template=str(other / "{number}" / "{number}.{ext}"),
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


class TestValidateStrmContentTemplate:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (None, None),
            ("", None),
            ("   ", None),
            ("\t\n ", None),  # 全空白视为清空, 其中的换行不触发拒绝
            ("/{video_relpath}", "/{video_relpath}"),
            ("  /OneDrive/{video_relpath}  ", "/OneDrive/{video_relpath}"),
            ("http://alist:5244/d{video_relpath}", "http://alist:5244/d{video_relpath}"),
        ],
    )
    def test_blank_is_unset_and_stripped(self, raw: str | None, expected: str | None):
        assert validate_strm_content_template(raw) == expected

    @pytest.mark.parametrize("raw", ["/a\n/b", "/a\r/b", "/a\r\n/b", "/a\nb  "])
    def test_multiline_rejected(self, raw: str):
        """strm 是一行路径; 多行会让播放端读出带尾巴的路径."""
        with pytest.raises(ValueError, match="single line"):
            validate_strm_content_template(raw)


class TestRenderStrmContent:
    """strm 内容模板: 把本地挂载路径换成远端标识 (OpenList / HTTP)."""

    def _lib(self, media: Path, template: str | None) -> Library:
        return Library(name="t", path=str(media), strm_content_template=template)

    def test_unset_writes_absolute_video_path(self, media: Path):
        video = media / "OD" / "VC" / "ABC-123" / "ABC-123.mp4"
        assert render_strm_content(self._lib(media, None), _meta(), video) == str(video)

    @pytest.mark.parametrize(
        ("template", "expected"),
        [
            ("/{video_relpath}", "/OD/VC/ABC-123/ABC-123.mp4"),
            ("/OneDrive/{video_relpath}", "/OneDrive/OD/VC/ABC-123/ABC-123.mp4"),
            ("http://alist:5244/d/{video_relpath}", "http://alist:5244/d/OD/VC/ABC-123/ABC-123.mp4"),
            ("/OD/VC/{number}/{number}.{ext}", "/OD/VC/ABC-123/ABC-123.mp4"),
            ("/{studio}/{video_relpath}", "/StudioX/OD/VC/ABC-123/ABC-123.mp4"),
            ("/fixed/path.mp4", "/fixed/path.mp4"),  # 无占位符原样输出
            ("/{nope}/{video_relpath}", "/Unknown/OD/VC/ABC-123/ABC-123.mp4"),  # 未知占位符回退 Unknown
        ],
    )
    def test_renders_remote_reference(self, media: Path, template: str, expected: str):
        video = media / "OD" / "VC" / "ABC-123" / "ABC-123.mp4"
        assert render_strm_content(self._lib(media, template), _meta(), video) == expected

    def test_video_path_is_absolute_local_path(self, media: Path):
        video = media / "OD" / "ABC-123.mp4"
        assert render_strm_content(self._lib(media, "{video_path}"), _meta(), video) == str(video)

    def test_relpath_keeps_cd_suffix_and_collision_rename(self, media: Path):
        """relpath 取自实际落地路径, 因此 CD 后缀与碰撞改名都保留 (手写元数据模板会丢)."""
        lib = self._lib(media, "/{video_relpath}")
        assert render_strm_content(lib, _meta(), media / "ABC-123" / "ABC-123-CD2.mp4") == "/ABC-123/ABC-123-CD2.mp4"
        assert render_strm_content(lib, _meta(), media / "ABC-123" / "ABC-123(1).mp4") == "/ABC-123/ABC-123(1).mp4"

    def test_ext_comes_from_landed_video(self, media: Path):
        """{ext} 取实际落地文件的扩展名, 不需要调用方再传一份."""
        content = render_strm_content(self._lib(media, "/{number}.{ext}"), _meta(), media / "ABC-123.mkv")
        assert content == "/ABC-123.mkv"

    def test_relpath_outside_library_root_raises(self, media: Path, other: Path):
        """绝对 video_template 落到别的盘时 relpath 无意义, 宁可报错也不写出错误 strm."""
        with pytest.raises(ValueError, match="not under library root"):
            render_strm_content(self._lib(media, "/{video_relpath}"), _meta(), other / "ABC-123.mp4")

    def test_outside_library_root_ok_when_relpath_unused(self, media: Path, other: Path):
        """模板不引用 {video_relpath} 就不该因为视频在库外而失败."""
        video = other / "ABC-123.mp4"
        assert render_strm_content(self._lib(media, "{video_path}"), _meta(), video) == str(video)

    def test_source_placeholders_available(self, media: Path):
        """metadata / source / file 相位占位符一并可用."""
        source = Path("/inbox/uncensored/ABC-123-4K.mp4")
        content = render_strm_content(
            self._lib(media, "/{mosaic}/{definition}/{raw_name}/{video_relpath}"),
            _meta(),
            media / "ABC-123.mp4",
            source_path=source,
            file_info=parse_file_info(source),
        )
        assert content == "/uncensored/4K/ABC-123-4K/ABC-123.mp4"
