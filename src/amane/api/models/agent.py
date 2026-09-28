from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from ...agent.rows import TraceRow
from ...config import AgentThinkingMode
from ...db.models import AgentSessionStatus, SavedQueryEntity


class AgentSessionCreateRequest(BaseModel):
    title: str = Field(default="新会话", min_length=1, max_length=200)


class AgentSessionUpdateRequest(BaseModel):
    """title / thinking 均可选; thinking=null 表示取消覆盖, 继承全局默认."""

    title: str | None = Field(default=None, min_length=1, max_length=200)
    thinking: AgentThinkingMode | None = None


class AgentSessionResponse(BaseModel):
    id: int
    title: str
    status: AgentSessionStatus
    thinking: AgentThinkingMode | None = None
    """会话思考覆盖; null 表示继承 hot.agent.thinking."""
    created_at: datetime
    updated_at: datetime


class AgentSessionTitleRequest(BaseModel):
    """首条用户输入: 标题只依据它生成."""

    prompt: str = Field(min_length=1, max_length=4000)


class AgentSessionTitleResponse(BaseModel):
    title: str


class AgentSessionListResponse(BaseModel):
    items: list[AgentSessionResponse]


class AgentTraceResponse(BaseModel):
    meta: dict[str, Any]
    events: list[TraceRow]
    turn_running: bool = False
    last_seq: int = 0


class AgentCancelResponse(BaseModel):
    cancelled: bool


class SavedQueryResponse(BaseModel):
    id: int
    name: str
    sql: str
    entity: SavedQueryEntity
    session_id: int | None
    persisted: bool
    created_at: datetime
    updated_at: datetime


class SavedQueryListResponse(BaseModel):
    items: list[SavedQueryResponse]


class SavedQueryUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    persisted: bool | None = None


class SavedQueryResultResponse(BaseModel):
    saved_query_id: int
    columns: list[str]
    rows: list[list[Any]]
    offset: int
    limit: int
    total: int
