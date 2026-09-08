from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ...scheduler.clouddrive import CloudDriveChange


def _parse_is_dir(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes"}:
            return True
        if lowered in {"false", "0", "no", ""}:
            return False
        raise ValueError("is_dir 必须是 true 或 false")
    raise ValueError("is_dir 必须是 true 或 false")


class CloudDriveChangeItem(BaseModel):
    action: Literal["create", "delete", "rename"]
    is_dir: bool
    source_file: str
    destination_file: str = ""

    @field_validator("action", mode="before")
    @classmethod
    def _normalize_action(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator("is_dir", mode="before")
    @classmethod
    def _normalize_is_dir(cls, value: object) -> bool:
        return _parse_is_dir(value)

    @field_validator("source_file", "destination_file", mode="before")
    @classmethod
    def _stringify(cls, value: object) -> str:
        if value is None:
            return ""
        return str(value)

    def to_change(self) -> CloudDriveChange:
        return CloudDriveChange(
            action=self.action,
            is_dir=self.is_dir,
            source_file=self.source_file,
            destination_file=self.destination_file,
        )


class CloudDriveNotifyRequest(BaseModel):
    """CloudDrive file_system_watcher 模板体. 未知字段忽略."""

    model_config = ConfigDict(extra="ignore")

    data: list[CloudDriveChangeItem] = Field(default_factory=list)

    @model_validator(mode="after")
    def _drop_empty_sources(self) -> Self:
        self.data = [item for item in self.data if item.source_file.strip()]
        return self
