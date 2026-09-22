"""长文本归一: 把上游的 HTML 片段 / 实体 / 异体空白收成纯文本.

调用时机是「聚合出口 + 落库写入」两处, 因此必须幂等 — 实体解码排在标签处理之前,
``&lt;br&gt;`` 与 ``<br>`` 都收成换行, 二次调用不再变化 (代价是实体编码的标签也当标签处理).
唯一例外是上游**双重编码**的实体 (``&amp;amp;``): 每次调用再解一层 — 这正落在它想要的字符上.

段落用空行 (``\\n\\n``) 表示, 单个 ``\\n`` 表示折行: Kodi 逐行折行, Emby / Jellyfin 按
markdown 渲染时只有空行才分段.
"""

from __future__ import annotations

import html
import re

_LINE_BREAK = re.compile(r"(?i)<br\s*/?>")
_BLOCK_END = re.compile(r"(?i)</(?:p|div|li|h[1-6]|tr|td|section|article)\s*>")
# 标签名以字母开头, 避免把 ``5 < 6 > 4`` 这类数学文本当标签吞掉.
_TAG = re.compile(r"</?[a-zA-Z][a-zA-Z0-9-]*(?:\s[^<>]*?)?/?>")
_UNICODE_SPACES = re.compile("[\u00a0\u1680\u2000-\u200a\u202f\u205f\u3000]")
_BLANK_RUN = re.compile(r"\n{3,}")
# XML 1.0 非法字符 (C0 除 \t\n\r, C1, 孤立代理, BOM 与非字符).
_ILLEGAL_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\ud800-\udfff\ufeff\ufffe\uffff]")


def strip_illegal_xml_chars(text: str) -> str:
    """去掉无法写进 XML 的字符; 写 NFO 前的兜底 (存量数据可能仍带脏字符)."""
    return _ILLEGAL_XML.sub("", text)


def normalize_long_text(value: str | None) -> str | None:
    """长文本字段 (简介等) → 纯文本; 无有效内容返回 None."""
    if value is None:
        return None
    text = html.unescape(value)
    text = _LINE_BREAK.sub("\n", text)
    text = _BLOCK_END.sub("\n\n", text)
    text = _TAG.sub("", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _UNICODE_SPACES.sub(" ", text)
    text = strip_illegal_xml_chars(text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    return _BLANK_RUN.sub("\n\n", text).strip() or None
