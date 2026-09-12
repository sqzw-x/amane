"""Convert sidecar subtitle files to WebVTT text."""

from __future__ import annotations

import re

_SRT_TS = re.compile(r"(\d{2}:\d{2}:\d{2}),(\d{3})")


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
