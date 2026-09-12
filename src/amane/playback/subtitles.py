"""Convert sidecar subtitle files to WebVTT text."""

from __future__ import annotations

import re

_SRT_TS = re.compile(r"(\d{2,3}:\d{2}:\d{2}),(\d{3})")
SUBTITLE_ENCODINGS = ("utf-8-sig", "gbk")


def decode_subtitle(raw: bytes) -> str | None:
    """按 UTF-8 与 GBK 顺序解码字幕正文.

    两种编码都失败时返回 ``None``. 不允许回退到不会失败的编码 (latin-1 之类): 那会把乱码
    交给浏览器, 用户看到的是错字而不是「无法读取」, 探测阶段也无从判断该轨道是否可用.
    """
    for encoding in SUBTITLE_ENCODINGS:
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return None


def to_webvtt(text: str, *, suffix: str) -> str:
    body = text.lstrip("\ufeff")
    lowered = suffix.casefold()
    if lowered == ".vtt":
        stripped = body.lstrip()
        if stripped.upper().startswith("WEBVTT"):
            return body
        return f"WEBVTT\n\n{body}"
    converted = _SRT_TS.sub(r"\1.\2", body)
    return f"WEBVTT\n\n{converted}"
