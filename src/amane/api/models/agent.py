from datetime import datetime
from typing import Annotated, Any, Self

from pydantic import BaseModel, Field, StringConstraints, model_validator

from ...agent.rows import UiRow
from ...config import AgentThinkingMode
from ...db.models import DEFAULT_SESSION_TITLE, AgentSessionStatus, SavedQueryEntity


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


SavedQueryName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
SavedQueryDescription = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]
SavedQuerySql = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class SavedQueryCreateRequest(BaseModel):
    """手动创建: 内容 + 类型; 归属与保留态由服务端固定 (无会话, 已保留)."""

    name: SavedQueryName
    description: SavedQueryDescription = ""
    sql: SavedQuerySql
    entity: SavedQueryEntity


class SavedQueryUpdateRequest(BaseModel):
    """内容三项均可选; 类型 / 归属 / 保留态不可改.

    显式 null → 422, 省略键才是「不更新」. 约束与创建请求共用, 不随 DB 模型派生
    (``create_partial_model`` 会丢弃 ``StringConstraints``).
    """

    name: SavedQueryName | None = None
    description: SavedQueryDescription | None = None
    sql: SavedQuerySql | None = None

    @model_validator(mode="after")
    def _reject_explicit_null(self) -> Self:
        if "name" in self.model_fields_set and self.name is None:
            raise ValueError("name cannot be null")
        if "description" in self.model_fields_set and self.description is None:
            raise ValueError("description cannot be null")
        if "sql" in self.model_fields_set and self.sql is None:
            raise ValueError("sql cannot be null")
        return self


class SavedQueryBatchIdsRequest(BaseModel):
    # 单条 IN 查询逐 id 绑定变量; 上限防 SQLite 变量数超限, 越界请求得到明确 422.
    ids: list[int] = Field(min_length=1, max_length=1000, description="查询预设 ID 列表")


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
