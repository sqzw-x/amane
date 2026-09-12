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
