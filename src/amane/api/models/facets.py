from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator

from .user_tags import UserTagResponse


class FacetResponse(BaseModel):
    id: int
    name: str
    count: int
    #: 不支持收藏的分类恒为假.
    is_favorite: bool


class FacetBatchAction(StrEnum):
    """分类批量动作; 两个动作都是整体赋值且幂等.

    不设逐项取反 (toggle): 多选混合态下点「收藏」必须保持收藏, 取反会取消已收藏的条目. 枚举是
    动作信封的扩展位, 新增动作 (如批量删除) 时 ``FacetBatchResponse`` 的字段按动作解释.
    """

    FAVORITE = "favorite"
    UNFAVORITE = "unfavorite"


class FacetBatchRequest(BaseModel):
    """对一批分类执行同一个动作; 重复 id 去重, 使结果计数以去重后的条目数为准."""

    facet_ids: list[int] = Field(min_length=1, description="分类 ID 列表")
    action: FacetBatchAction = Field(description="favorite 置为已收藏, unfavorite 置为未收藏; 两者均幂等")

    @field_validator("facet_ids")
    @classmethod
    def _normalize(cls, value: list[int]) -> list[int]:
        return list(dict.fromkeys(value))


class FacetBatchResponse(BaseModel):
    """批量动作的结果计数, 字段按 action 解释; 两个已实现动作下三者之和等于去重后的条目数."""

    changed: int = Field(description="收藏位确实发生写入的分类数")
    unchanged: int = Field(description="已处于目标取值的分类数")
    missing: int = Field(description="不存在的分类 id 数")


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
