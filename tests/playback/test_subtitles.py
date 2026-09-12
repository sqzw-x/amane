import pytest

from amane.playback.subtitles import decode_subtitle, to_webvtt


def test_to_webvtt_passthrough() -> None:
    text = "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nHi\n"
    assert to_webvtt(text, suffix=".vtt") == text


def test_to_webvtt_wraps_plain_vtt() -> None:
    assert to_webvtt("00:00:01.000 --> 00:00:02.000\nHi\n", suffix=".vtt").startswith("WEBVTT\n\n")


def test_to_webvtt_converts_srt() -> None:
    srt = "1\n00:00:01,000 --> 00:00:04,500\nHello\n\n"
    out = to_webvtt(srt, suffix=".srt")
    assert out.startswith("WEBVTT\n\n")
    assert "00:00:01.000 --> 00:00:04.500" in out


def test_to_webvtt_converts_three_digit_hour() -> None:
    """SRT 允许三位小时数; 未转换的时间戳在浏览器端非法, 整条字幕会被丢弃."""
    srt = "1\n100:00:01,000 --> 100:00:04,500\nHello\n\n"
    out = to_webvtt(srt, suffix=".srt")
    assert "100:00:01.000 --> 100:00:04.500" in out
    assert "," not in out


def test_decode_subtitle_accepts_gbk() -> None:
    assert decode_subtitle("中文字幕".encode("gbk")) == "中文字幕"


def test_decode_subtitle_rejects_undecodable_bytes() -> None:
    assert decode_subtitle(b"\xff\xfe\x9c\x80\x81") is None


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("{\\an8}", ""),
        ("{\\an8}顶部对齐", "顶部对齐"),
        ("顶部对齐{\\an8}", "顶部对齐"),
        ("前段{\\an8}后段", "前段后段"),
        ("{\\an8}   ", ""),
        ("{\\i1}斜体{\\i0}与{\\pos(192,210)}定位", "斜体与定位"),
        ("{note}", "{note}"),
        ("{\\an8}{note}", "{note}"),
        ("花括号文字 {中文} 保留", "花括号文字 {中文} 保留"),
        ("未闭合{\\an8 保留原文", "未闭合{\\an8 保留原文"),
    ],
)
def test_to_webvtt_strips_ass_overrides_in_srt(body: str, expected: str) -> None:
    """SRT 内嵌的 ASS/SSA 覆盖代码不是正文, 残留会被浏览器当作普通文字显示."""
    srt = f"1\n00:00:01,000 --> 00:00:04,500\n{body}\n"
    assert to_webvtt(srt, suffix=".srt") == f"WEBVTT\n\n1\n00:00:01.000 --> 00:00:04.500\n{expected}\n"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n{\\an8}Hi\n",
            "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nHi\n",
        ),
        (
            "00:00:01.000 --> 00:00:02.000\n{\\an8}Hi\n",
            "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nHi\n",
        ),
        (
            "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n{\\an8}\n",
            "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n\n",
        ),
    ],
)
def test_to_webvtt_strips_ass_overrides_in_vtt(text: str, expected: str) -> None:
    """已带 WEBVTT 头的 .vtt 原样返回, 覆盖代码的清洗同样必须生效."""
    assert to_webvtt(text, suffix=".vtt") == expected
