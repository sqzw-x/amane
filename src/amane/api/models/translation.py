"""独立翻译入口的响应模型."""

from enum import StrEnum

from pydantic import BaseModel, Field

from ...enums import ActorField, MetadataField


class TranslationStatus(StrEnum):
    """逐字段的翻译结果."""

    TRANSLATED = "translated"
    """取到译文且与原文不同, 已写入."""

    UNCHANGED = "unchanged"
    """无需翻译, 或译文与原文逐字相同 (不写库)."""

    LOCKED = "locked"
    """字段已锁定, 未写入."""

    FAILED = "failed"
    """需要翻译但未取到译文, 保留原值."""


class MetadataTranslationOutcome(BaseModel):
    field: MetadataField
    status: TranslationStatus


class ActorTranslationOutcome(BaseModel):
    field: ActorField
    status: TranslationStatus


class MetadataTranslationResponse(BaseModel):
    """影片独立翻译结果; 未配置或文本为空的字段不出现."""

    outcomes: list[MetadataTranslationOutcome] = Field(default_factory=list)


class ActorTranslationResponse(BaseModel):
    """演员独立翻译结果; 未配置或文本为空的字段不出现."""

    outcomes: list[ActorTranslationOutcome] = Field(default_factory=list)
