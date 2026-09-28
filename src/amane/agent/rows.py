"""`events.jsonl` 的契约: 会话 UI 重建的唯一数据源.

每行是一条**已成形的展示事实** — 协议里那些需要前端二次推断的东西 (工具参数增量拼装、结果按名配对、
用量位置、未决中断还原) 都在这里定形. 前端只按到达顺序折叠, 不认识 `ag_ui` 事件.

联合以 `type` 判别, 经 OpenAPI 生成前端类型: 加字段或改字段名即在前端编译期暴露.
`seq` 与 `at` 由 `SessionStore` 落盘时补, 读取时一定存在.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from ag_ui.core import Interrupt
from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter

from .usage import RequestTokenUsage, TurnTokenUsage


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


class RowBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    seq: int | None = None
    at: str = Field(default_factory=_utcnow_iso)


class UserMessageRow(RowBase):
    """用户输入. 批准 / 拒绝只以 tool return 进模型上下文, 不产生此行的旁白."""

    type: Literal["user_message"]
    text: str


class ReasoningDeltaRow(RowBase):
    """思考增量; `block_id` 取自协议的消息 id, 前端据此归块而不靠相邻关系."""

    type: Literal["reasoning_delta"]
    block_id: str
    text: str


class TextDeltaRow(RowBase):
    """正文增量; 归块同 `ReasoningDeltaRow`."""

    type: Literal["text_delta"]
    block_id: str
    text: str


class ToolCallRow(RowBase):
    """工具调用成形 (协议按增量传参, 这里已解析). 卡片的名字与参数由此行给出."""

    type: Literal["tool_call"]
    tool_call_id: str
    name: str
    args: JsonValue


class ToolResultRow(RowBase):
    """工具回执. 名字与参数在同 id 的 `ToolCallRow`, 故本行只带结果 (续跑的回合不会再报调用名)."""

    type: Literal["tool_result"]
    tool_call_id: str
    result: JsonValue


class RequestUsageRow(RowBase):
    """单次模型请求的用量; `usage.after_tool_call` 标明它在回合里出现的位置."""

    type: Literal["request_usage"]
    usage: RequestTokenUsage


class TurnUsageRow(RowBase):
    """回合收尾: 聚合用量归属当前助手消息, 同时标志本轮结束."""

    type: Literal["turn_usage"]
    usage: TurnTokenUsage


class ApprovalsRow(RowBase):
    """未决审批快照. 每个回合结束发一条, **空列表表示已无未决**, 后者覆盖前者.

    原样携带 AG-UI 中断: 前端既用它渲染审批入口, 也把它交给 runtime 完成 `resume`.
    """

    type: Literal["approvals"]
    interrupts: list[Interrupt]


class ErrorRow(RowBase):
    """回合异常; 文案直接进助手气泡."""

    type: Literal["error"]
    message: str


class CancelledRow(RowBase):
    """回合被显式终止."""

    type: Literal["cancelled"]


class AguiEventRow(RowBase):
    """AG-UI 事件原样透传, 供 `POST .../agui` 分发给协议客户端; 页面不读它."""

    type: Literal["agui"]
    event: dict[str, Any]


TraceRow = Annotated[
    UserMessageRow
    | ReasoningDeltaRow
    | TextDeltaRow
    | ToolCallRow
    | ToolResultRow
    | RequestUsageRow
    | TurnUsageRow
    | ApprovalsRow
    | ErrorRow
    | CancelledRow
    | AguiEventRow,
    Field(discriminator="type"),
]

TRACE_ROW: TypeAdapter[TraceRow] = TypeAdapter(TraceRow)
