"""文件相位投影与条目相位聚合: 有码 / 无码取条目类型, 其余按任一文件具备."""

import pytest

from amane.parsing import (
    DEFINITION_VALUES,
    ContentType,
    FilePhase,
    Mosaic,
    MosaicNature,
    VideoSize,
    definition_of_size,
    file_phase_from_path,
    max_definition,
    mosaic_nature,
    parse_file_info,
    shows_uncensored,
    summarize_file_phases,
)


@pytest.mark.parametrize(
    ("path", "content_type", "mosaic"),
    [
        ("/media/MIDV-123.mp4", ContentType.CENSORED, None),
        ("/media/HEYZO-1234.mp4", ContentType.UNCENSORED, None),
        ("/media/MD0165-1.mp4", ContentType.CHINESE, None),
        ("/media/MIDV-123-U.mp4", ContentType.CENSORED, Mosaic.CRACKED),
        ("/media/MIDV-123-无码.mp4", ContentType.CENSORED, Mosaic.UNCENSORED),
        ("/media/无码/MIDV-123.mp4", ContentType.CENSORED, Mosaic.UNCENSORED),
    ],
)
def test_file_phase_from_path_keeps_marker_only(path: str, content_type: ContentType, mosaic: Mosaic | None) -> None:
    """类型不落文件相位, 马赛克只收词表命中的来路标记."""
    assert file_phase_from_path(path)["mosaic"] is mosaic
    assert parse_file_info(path).content_type is content_type


@pytest.mark.parametrize(
    ("mosaic", "content_type", "expected"),
    [
        (None, ContentType.UNCENSORED, True),
        (Mosaic.UNCENSORED, ContentType.CENSORED, True),
        (Mosaic.CRACKED, ContentType.UNCENSORED, True),
        (None, ContentType.CENSORED, False),
        (Mosaic.CRACKED, ContentType.CENSORED, False),
        # 未绑定条目的文件只看自身标记
        (None, None, False),
        (Mosaic.UNCENSORED, None, True),
    ],
)
def test_shows_uncensored(mosaic: Mosaic | None, content_type: ContentType | None, expected: bool) -> None:
    assert shows_uncensored(mosaic, content_type) is expected


@pytest.mark.parametrize(
    ("mosaic", "content_type", "expected"),
    [
        (None, ContentType.CENSORED, MosaicNature.CENSORED),
        (None, ContentType.UNCENSORED, MosaicNature.UNCENSORED),
        (Mosaic.UNCENSORED, ContentType.CENSORED, MosaicNature.UNCENSORED),
        (Mosaic.CRACKED, ContentType.UNCENSORED, MosaicNature.CRACKED),
        (Mosaic.LEAKED, ContentType.CENSORED, MosaicNature.LEAKED),
        (None, ContentType.CHINESE, None),
        (None, None, None),
    ],
)
def test_mosaic_nature(mosaic: Mosaic | None, content_type: ContentType | None, expected: MosaicNature | None) -> None:
    """性质: 标记优先, 没命中时继承条目类型的有码 / 无码."""
    assert mosaic_nature(mosaic, content_type) is expected


def test_every_mosaic_marker_has_a_nature() -> None:
    """标记与性质是两个枚举: 加标记时没同步, 整理落盘会在 mosaic_nature 抛 ValueError."""
    assert {marker.value for marker in Mosaic} <= {nature.value for nature in MosaicNature}


@pytest.mark.parametrize(
    ("width", "height", "expected"),
    [
        (7680, 4320, "8K"),
        # VR 等宽画幅: 短边只到 4K, 长边达到 8K
        (7680, 3840, "8K"),
        (3840, 2160, "4K"),
        (4096, 2160, "4K"),
        (2160, 3840, "4K"),
        # 长边恰好达到下限 / 差 1 像素
        (3600, 1500, "4K"),
        (3599, 1500, "1440p"),
        (3440, 1440, "1440p"),
        (2560, 1440, "1440p"),
        (1920, 1080, "1080p"),
        # 竖屏与横屏同档, 不按高归入 1440p
        (1080, 1920, "1080p"),
        # 裁切黑边的宽银幕按长边, 4:3 按短边
        (1920, 800, "1080p"),
        (1440, 1080, "1080p"),
        (1280, 720, "720p"),
        (960, 720, "720p"),
        (854, 480, "480p"),
        (640, 480, "480p"),
        (640, 360, "SD"),
        # 长短边都差 1 像素
        (799, 449, "SD"),
        (1, 1, "SD"),
    ],
)
def test_definition_of_size(width: int, height: int, expected: str) -> None:
    """长边或短边任一达到下限即命中; 结果必须是 DEFINITION_VALUES 内的档位, 否则筛选与角标不认."""
    result = definition_of_size(VideoSize(width, height))
    assert result == expected
    assert result in DEFINITION_VALUES


@pytest.mark.parametrize(("width", "height"), [(0, 1080), (1920, 0), (-1920, 1080), (1920, -1)])
def test_video_size_rejects_non_positive(width: int, height: int) -> None:
    with pytest.raises(ValueError, match=f"画面尺寸必须是正整数, 实际为 {width}x{height}"):
        VideoSize(width, height)


@pytest.mark.parametrize(
    ("path", "size", "expected"),
    [
        # 画面尺寸给出时文件名的分辨率标记不参与
        ("/media/ABC-123-4K.mp4", VideoSize(1920, 1080), "1080p"),
        ("/media/ABC-123-HD.mp4", VideoSize(640, 360), "SD"),
        ("/media/ABC-123.mp4", VideoSize(3840, 2160), "4K"),
        # 未读出画面尺寸回退文件名
        ("/media/ABC-123-1080p.mp4", None, "1080p"),
        ("/media/ABC-123.mp4", None, None),
    ],
)
def test_definition_prefers_video_size(path: str, size: VideoSize | None, expected: str | None) -> None:
    assert parse_file_info(path, video_size=size).definition == expected
    assert file_phase_from_path(path, size)["definition"] == expected


def test_video_size_leaves_other_phases_to_path() -> None:
    """画面尺寸只决定分辨率, 中字与马赛克标记仍依据路径."""
    phase = file_phase_from_path("/media/MIDV-123-UC-4K.mp4", VideoSize(1280, 720))
    assert phase == {"mosaic": Mosaic.CRACKED, "has_subtitle": True, "definition": "720p"}


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ((), None),
        ((None, None), None),
        (("1080p", "4K", "720p"), "4K"),
        (("HD", "1080p"), "1080p"),
        (("unknown",), None),
    ],
)
def test_max_definition(values: tuple[str | None, ...], expected: str | None) -> None:
    assert max_definition(values) == expected


def test_summarize_file_phases_or_across_files() -> None:
    cracked: FilePhase = {"mosaic": Mosaic.CRACKED, "has_subtitle": True, "definition": "1080p"}
    leaked: FilePhase = {"mosaic": Mosaic.LEAKED, "has_subtitle": False, "definition": "4K"}
    summary = summarize_file_phases((cracked, leaked), ContentType.CENSORED)
    assert summary.censored is True
    assert summary.uncensored is False
    assert summary.has_subtitle is True
    assert summary.mosaics == (Mosaic.CRACKED, Mosaic.LEAKED)
    assert summary.definition == "4K"


@pytest.mark.parametrize(
    ("content_type", "censored", "uncensored"),
    [
        (ContentType.CENSORED, True, False),
        (ContentType.UNCENSORED, False, True),
        (ContentType.WESTERN, False, False),
    ],
)
def test_summarize_file_phases_without_files(content_type: ContentType, censored: bool, uncensored: bool) -> None:
    """没有挂载文件时条目相位仍然成立."""
    summary = summarize_file_phases((), content_type)
    assert summary.censored is censored
    assert summary.uncensored is uncensored
    assert summary.mosaics == ()
    assert summary.definition is None


def test_summarize_file_phases_uncensored_marker_on_censored_entry() -> None:
    """有码番的无码标记副本: 条目有码, 无码由文件标记并进来."""
    marked: FilePhase = {"mosaic": Mosaic.UNCENSORED, "has_subtitle": False, "definition": None}
    summary = summarize_file_phases((marked,), ContentType.CENSORED)
    assert summary.censored is True
    assert summary.uncensored is True
    assert summary.mosaics == (Mosaic.UNCENSORED,)
