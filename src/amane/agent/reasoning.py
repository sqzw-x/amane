"""正文里内嵌的思维链.

上游把 `<think>` 与正文粘在同一段增量里时, SDK 只在整段增量恰好等于开始标签时才切出 ThinkingPart
(见 `pydantic_ai.models.openai._map_text_delta`), 落在正文里的整段思维链于是直接显示给用户.

切分放在**读回放行**时: 已落盘的 `text_delta` 一并被修正, 历史会话无需重跑就恢复正常, 直播与刷新也
因此同形. 模型上下文 (`messages.json`) 不动 — 不切分是 SDK 与端点的既定事实, 本地再切一次会让送回
模型的历史与模型产出不一致.

代价是一段响应被切成若干段: 思维链之后仍属这次响应的正文另起一段, 请求用量条因此可能落在两个正文段
之间. 没有标签时整段正文仍是一段, 行与不改写时完全一致.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator

from .rows import ReasoningDeltaRow, TextDeltaRow, UiRow

_OPEN = "<think>"
_CLOSE = "</think>"


class EmbeddedThinking:
    """一次助手响应正文的标签游标; 换响应即重开.

    输出按**段**分组: 每越过一个标签就是新的一段, 段号递增. 正文段与思考段分属不同的块, 页面据此
    按到达顺序排列, 不会把思维链之后的正文并回思维链之前.
    """

    def __init__(self) -> None:
        self._inside = False
        self._pending = ""
        self._segment = 0

    def feed(self, text: str) -> list[tuple[bool, str, int]]:
        """推入一段正文增量, 返回 [(是否思维链, 文本, 段号)].

        一段增量可能是 `<thi` | `k>` 或 `</thi` | `nk>`: 提前发出就再也拼不回标签, 故按当前要找
        的标签的最长可能前缀扣住尾巴.
        """
        buffer = self._pending + text
        self._pending = ""
        out: list[tuple[bool, str, int]] = []
        while buffer:
            tag = _CLOSE if self._inside else _OPEN
            index = buffer.find(tag)
            if index < 0:
                held = _unsettled(buffer, tag)
                self._emit(out, buffer[: len(buffer) - held])
                self._pending = buffer[len(buffer) - held :]
                break
            self._emit(out, buffer[:index])
            buffer = buffer[index + len(tag) :]
            self._inside = not self._inside
            self._segment += 1
        return out

    def close(self) -> list[tuple[bool, str, int]]:
        """收尾: 待定尾巴按当前所在区域归属, 此后不再有增量."""
        out: list[tuple[bool, str, int]] = []
        self._emit(out, self._pending)
        self._pending = ""
        return out

    def _emit(self, out: list[tuple[bool, str, int]], text: str) -> None:
        """并入结果; 同类同段的相邻输出合并, 没被标签打断的正文因此仍是一行."""
        if not text:
            return
        if out and out[-1][0] is self._inside and out[-1][2] == self._segment:
            out[-1] = (self._inside, out[-1][1] + text, self._segment)
            return
        out.append((self._inside, text, self._segment))


class EmbeddedThinkingRows:
    """逐行改写回放行; 直播与 `/trace` 共用, 两侧因此同形.

    归块靠 `block_id`: 同一块连续的正文增量属一次响应, 块号一换即重开游标并把尾巴收掉. 第 0 段的
    正文行原样返回, 没有标签的日志因此逐字节不变.
    """

    def __init__(self) -> None:
        self._cursor = EmbeddedThinking()
        self._block_id: str | None = None
        self._stamp: TextDeltaRow | None = None
        """块内最后一行; 补发的尾巴沿用它的时刻与 seq, 免得页面拿到空时间."""

    def feed(self, row: UiRow) -> list[UiRow]:
        if not isinstance(row, TextDeltaRow):
            return [row]
        out: list[UiRow] = []
        if row.block_id != self._block_id:
            out.extend(self.close())
            self._cursor = EmbeddedThinking()
            self._block_id = row.block_id
        self._stamp = row
        for item in self._cursor.feed(row.text):
            thinking, text, segment = item
            if not thinking and segment == 0:
                out.append(row)
            else:
                out.append(_rewrite(row, thinking, text, segment))
        return out

    def close(self) -> list[UiRow]:
        """块结束或流收尾: 把扣住的尾巴并回它所属的那一段."""
        if self._block_id is None:
            return []
        self._block_id = None
        return [_rewrite(self._stamp, *item) for item in self._cursor.close()]


def split_embedded_thinking(rows: Iterable[UiRow]) -> Iterator[UiRow]:
    """整段回放: 逐行改写, 收尾时补发尾巴."""
    rewriter = EmbeddedThinkingRows()
    for row in rows:
        yield from rewriter.feed(row)
    yield from rewriter.close()


def _rewrite(stamp: TextDeltaRow | None, thinking: bool, text: str, segment: int) -> UiRow:
    """按段号改写一行; 第 0 段的正文行由调用方原样返回, 无标签的日志因此逐字节不变."""
    assert stamp is not None
    block = stamp.block_id if segment == 0 else f"{stamp.block_id}#{segment}"
    if thinking:
        return ReasoningDeltaRow(type="reasoning_delta", block_id=block, text=text, seq=stamp.seq, at=stamp.at)
    return TextDeltaRow(type="text_delta", block_id=block, text=text, seq=stamp.seq, at=stamp.at)


def _unsettled(buffer: str, tag: str) -> int:
    """`buffer` 末尾可能是 `tag` 前缀的长度; 这些字符还不能发出."""
    for size in range(min(len(buffer), len(tag) - 1), 0, -1):
        if tag.startswith(buffer[-size:]):
            return size
    return 0
