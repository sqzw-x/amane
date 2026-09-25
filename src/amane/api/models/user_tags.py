from datetime import datetime

from pydantic import BaseModel, Field


class UserTagResponse(BaseModel):
    id: int
    name: str
    created_at: datetime | None = None
    updated_at: datetime | None = None


class UserTagLinksResponse(BaseModel):
    """用户标签挂载/卸载的结果计数; 三个字段均以条目 id 为单位, 之和等于去重后的条目数."""

    changed: int = Field(description="至少一处挂载关系发生变更的条目数")
    unchanged: int = Field(description="已处于目标态、未修改的条目数")
    missing: int = Field(description="不存在的条目 id 数")
