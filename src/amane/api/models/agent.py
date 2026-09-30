from datetime import datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field, ValidationInfo, field_validator

from ...agent.rows import UiRow
from ...config import AgentThinkingMode
from ...db.models import DEFAULT_SESSION_TITLE, AgentSessionStatus, SavedQuery, SavedQueryEntity
from ...utils.model import create_partial_model


class AgentSessionCreateRequest(BaseModel):
    title: str = Field(default=DEFAULT_SESSION_TITLE, min_length=1, max_length=200)


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
    events: list[UiRow]
    turn_running: bool = False
    last_seq: int = 0


class AgentCancelResponse(BaseModel):
    cancelled: bool


class SavedQueryResponse(BaseModel):
    id: int
    name: str
    description: str
    sql: str
    entity: SavedQueryEntity
    session_id: int | None
    persisted: bool
    created_at: datetime
    updated_at: datetime


class SavedQueryListResponse(BaseModel):
    items: list[SavedQueryResponse]


class SavedQueryCreateRequest(BaseModel):
    """手动创建: 内容 + 类型; 归属与保留态由服务端固定 (无会话, 已保留)."""

    name: str
    description: str = ""
    sql: str
    entity: SavedQueryEntity

    @field_validator("name", "sql")
    @classmethod
    def _strip_non_empty(cls, value: str, info: ValidationInfo) -> str:
        text = value.strip()
        if not text:
            raise ValueError(f"{info.field_name} 不能为空")
        return text

    @field_validator("description")
    @classmethod
    def _strip_description(cls, value: str) -> str:
        return value.strip()


if TYPE_CHECKING:
    type SavedQueryUpdateRequest = SavedQuery

# 外部可写字段: 仅内容三项; 类型 / 归属 / 保留态不可经 PATCH 改动.
# 非空列显式 null → 422; 清空描述送空串 (前端经 schema-form 编码器出 body).
SavedQueryUpdateRequest = create_partial_model(
    SavedQuery, fields=("name", "description", "sql"), partial_cls_name="SavedQueryUpdateRequest"
)


class SavedQueryBatchIdsRequest(BaseModel):
    ids: list[int] = Field(min_length=1, description="查询预设 ID 列表")


class SavedQueryBatchDeleteResponse(BaseModel):
    deleted: int = Field(description="成功删除的数量")
    missing: int = Field(description="不存在的 id 数量")


class SavedQueryBatchPersistResponse(BaseModel):
    persisted: int = Field(description="找到并置为已保留的数量 (已保留的也计入, 幂等)")
    missing: int = Field(description="不存在的 id 数量")


class SavedQueryResultResponse(BaseModel):
    saved_query_id: int
    columns: list[str]
    rows: list[list[Any]]
    offset: int
    limit: int
    total: int
