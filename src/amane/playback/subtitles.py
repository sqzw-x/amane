"""Convert sidecar subtitle files to WebVTT text."""

from __future__ import annotations

import re

_SRT_TS = re.compile(r"(\d{2,3}:\d{2}:\d{2}),(\d{3})")
_ASS_OVERRIDE = re.compile(r"\{\\[^}]*\}")
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


def _strip_ass_overrides(text: str) -> str:
    """删除 ASS/SSA 覆盖代码 (``{\\an8}``、``{\\pos(192,210)}``、``{\\i1}`` 之类).

    只匹配花括号内以反斜杠开头的片段. 正文中的花括号文字 (``{note}``) 不是覆盖代码, 必须保留.
    覆盖代码被删除后仅剩空白的行按空行处理: 只含空白的正文行在浏览器端渲染为一条空字幕,
    仍会占据时间轴, 与「该行没有内容」的语义不符.
    """
    cleaned: list[str] = []
    for line in text.split("\n"):
        if "{\\" not in line:
            cleaned.append(line)
            continue
        without_codes = _ASS_OVERRIDE.sub("", line)
        cleaned.append("" if not without_codes.strip() else without_codes)
    return "\n".join(cleaned)


def to_webvtt(text: str, *, suffix: str) -> str:
    # 已带 WEBVTT 头的 .vtt 原样返回, 清洗须在分支之前完成, 两条路径才都会生效.
    body = _strip_ass_overrides(text.lstrip("\ufeff"))
    lowered = suffix.casefold()
    if lowered == ".vtt":
        stripped = body.lstrip()
        if stripped.upper().startswith("WEBVTT"):
            return body
        return f"WEBVTT\n\n{body}"
    converted = _SRT_TS.sub(r"\1.\2", body)
    return f"WEBVTT\n\n{converted}"
