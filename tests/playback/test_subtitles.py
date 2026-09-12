from amane.playback.subtitles import to_webvtt


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
