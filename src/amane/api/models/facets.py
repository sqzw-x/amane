from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from .user_tags import UserTagResponse


class FacetResponse(BaseModel):
    id: int
    name: str
    count: int


class FacetListResponse(BaseModel):
    items: list[FacetResponse]
    total: int


class UserTagsCreateRequest(BaseModel):
    """批量取回或新建用户标签; 名称去重, 已存在的名称直接复用."""

    names: list[str] = Field(min_length=1, description="用户标签名称列表")

    @field_validator("names")
    @classmethod
    def _normalize(cls, value: list[str]) -> list[str]:
        unique = list(dict.fromkeys(name.strip() for name in value if name.strip()))
        if not unique:
            raise ValueError("名称不能为空")
        return unique


class UserTagsCreateResponse(BaseModel):
    items: list[UserTagResponse] = Field(description="与入参同序的标签")
    created: int = Field(description="本次新建的数量; 其余为已存在的名称")


class FacetRenameRequest(BaseModel):
    name: str = Field(min_length=1, description="新名称")


class FacetMergeRequest(BaseModel):
    target_id: int
    source_ids: list[int] = Field(min_length=1, description="待合并的来源 facet id 列表")


class FacetRuleResponse(BaseModel):
    id: int
    kind: str
    source_name: str
    action: str
    target_name: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class FacetRuleListResponse(BaseModel):
    items: list[FacetRuleResponse]
