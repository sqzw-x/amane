from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Self

from pydantic import BaseModel, Field, StringConstraints, model_validator

from ...db.models import SavedQueryEntity


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
    """手动创建: 名称 / 描述 / SQL 与类型; 归属与保留态由服务端固定 (无会话, 已保留)."""

    name: SavedQueryName
    description: SavedQueryDescription = ""
    sql: SavedQuerySql
    entity: SavedQueryEntity


class SavedQueryUpdateRequest(BaseModel):
    """仅名称 / 描述 / SQL 三项, 未知键被忽略; 显式 null 一律 422, 省略键才是「不更新」.

    字段不从 DB 模型派生: ``create_partial_model`` 会丢弃 ``StringConstraints``;
    约束别名与创建请求共用.
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


class SavedQueryBatchAction(StrEnum):
    DELETE = "delete"
    PERSIST = "persist"


class SavedQueryBatchRequest(BaseModel):
    action: SavedQueryBatchAction
    # 单条 IN 查询为每个 id 绑定一个变量, 上限防 SQLite 变量数超限
    ids: list[int] = Field(min_length=1, max_length=1000, description="查询预设 ID 列表")


class SavedQueryBatchResponse(BaseModel):
    affected: int = Field(
        description="成功处理的数量; delete 为实际删除的数量, persist 为找到并置为已保留的数量 (幂等, 已保留的也计入)"
    )
    missing: int = Field(description="不存在的 id 数量")


class SavedQueryResultResponse(BaseModel):
    saved_query_id: int
    columns: list[str]
    rows: list[list[Any]]
    offset: int
    limit: int
    total: int
