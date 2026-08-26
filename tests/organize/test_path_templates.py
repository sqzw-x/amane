"""tests for amane.organize -- 路径模板解析.

路径统一由 pytest tmp_path 派生 (跨平台绝对路径, Windows 上带盘符).
不用 POSIX 硬编码绝对路径 (如 /media): 它们在 Windows 上是"无盘符的根相对路径",
与 resolve() 后补盘符的绝对路径不相等, 也会让 safe_dirs 的跨盘判定误判.
"""

from pathlib import Path
from typing import NamedTuple

import pytest

from amane.db.models import Library, Metadata
from amane.organize import CD_SUFFIX_TEMPLATE_DEFAULT, resolve_paths, validate_cd_suffix_template
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

    def test_subtitle_default(self, media: Path):
        wp = Library(name="t", path=str(media), video_template="{studio}/{number}/{number}.{ext}")
        meta = _meta()
        result = resolve_paths(wp, meta, ext="srt")

        assert result.subtitle == media / "StudioX" / "ABC-123" / "ABC-123.srt"


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


class TestSourceDirVariables:
    """源文件目录变量 {dir} / {dir_path}."""

    def test_dir_is_source_parent_name(self, media: Path):
        """{dir} 渲染为源文件所在目录的名称."""
        arch = media / "archive"
        wp = Library(name="t", path=str(arch), video_template=str(arch / "{dir}" / "{number}.{ext}"))
        meta = _meta()
        result = resolve_paths(wp, meta, ext="mp4", source_path=media / "incoming" / "Batch-01" / "ABC-123.mp4")
        assert result.video == arch / "Batch-01" / "ABC-123.mp4"

    def test_dir_path_is_full_source_parent(self, media: Path):
        """{dir_path} 渲染为源文件所在目录的完整路径 (绝对模板, 源目录在 safe_dirs 内)."""
        wp = Library(name="t", path=str(media / "archive"), video_template="{dir_path}/{number}.{ext}")
        meta = _meta()
        result = resolve_paths(
            wp,
            meta,
            ext="mp4",
            source_path=media / "incoming" / "Batch-01" / "ABC-123.mp4",
            safe_dirs=[media / "incoming"],
        )
        assert result.video == media / "incoming" / "Batch-01" / "ABC-123.mp4"

    def test_dir_empty_when_no_source_path(self, media: Path):
        """不传 source_path 时 {dir} 降级为空串 (非首段, 多余分隔符被 resolve 折叠)."""
        wp = Library(name="t", path=str(media), video_template="sub/{dir}/{number}.{ext}")
        meta = _meta()
        result = resolve_paths(wp, meta, ext="mp4")
        assert result.video == media / "sub" / "ABC-123.mp4"

    def test_dir_path_empty_when_no_source_path(self, media: Path):
        """不传 source_path 时 {dir_path} 降级为空串."""
        wp = Library(name="t", path=str(media), video_template="sub/{dir_path}/{number}.{ext}")
        meta = _meta()
        result = resolve_paths(wp, meta, ext="mp4")
        assert result.video == media / "sub" / "ABC-123.mp4"


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
    # cd 参数省略时从 file_info.cd 回退
    _RenderCase("MIDV-123-CD1.mp4", "{number}/{number}.{ext}", ("ABC-123", "ABC-123-CD1.mp4")),
    # 未走 ORGANIZE 的调用方不传 file_info: 与占位符缺失回退一致
    _RenderCase(None, "{mosaic}/{definition}/{number}.{ext}", ("Unknown", "Unknown", "ABC-123.mp4")),
)


@pytest.mark.parametrize("case", RENDER_CASES, ids=lambda c: c.source or "no-file-info")
def test_file_placeholder_render(case: _RenderCase, media: Path) -> None:
    """file 相位占位符 {mosaic} / {definition}: 来自源文件名 (parse_file_info)."""
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
