from abc import ABC, abstractmethod
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from ..db.models import TaskType

type ProgressCallback = Callable[[int, int, str], Coroutine[Any, Any, None]]


class FollowupTask(BaseModel):
    """后继任务只经 TaskResult.followups 进入完成事务; handler 不能直接写队列."""

    key: str
    """父节点内后继语义键 (扇出须含实体 id, 如 scrape:{media_file_id}); 同父同名只留一条."""
    task_type: TaskType
    payload: dict
    """已完成绑定的类型化 payload; handler 自己组装, 不能透传父任务字段."""
    priority: int = 0


@dataclass
class TaskResult[R: BaseModel | dict]:
    success: bool
    result: R | None = None
    error: str | None = None
    followups: list[FollowupTask] = field(default_factory=list)

    def as_dict(self) -> dict | None:
        """结果载荷的唯一落库形状: 以 JSON 模式 dump, 由 `tasks.result` 列的 `json.dumps` 直接写出.

        传 dict 时不做校验, 载荷须自带 JSON 原生值 (与 payload 侧的 `_payload_dict` 对称).
        """
        if isinstance(self.result, dict):
            return self.result
        if isinstance(self.result, BaseModel):
            return self.result.model_dump(mode="json")
        return None


class TaskHandler[P: BaseModel | dict = dict, R: BaseModel | dict = dict](ABC):
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)

    def __init__(self, payload_t: type[P], result_t: type[R]) -> None:
        self.payload_type = payload_t
        self.result_type = result_t

    @abstractmethod
    async def handle(self, payload: P) -> TaskResult[R]: ...

    def parse_payload(self, raw: dict) -> P:
        """已是目标类型时原样返回, 不重新校验."""
        if isinstance(raw, self.payload_type):
            return raw
        if issubclass(self.payload_type, BaseModel):
            return self.payload_type.model_validate(raw)  # type: ignore
        raise TypeError(f"Unsupported payload type: {self.payload_type}")

    # --- Progress reporting ---

    _progress_callback: ProgressCallback | None = None

    def set_progress_callback(self, callback: ProgressCallback | None) -> None:
        self._progress_callback = callback

    async def report_progress(self, current: int, total: int, message: str = "") -> None:
        """无回调时静默忽略."""
        if self._progress_callback:
            await self._progress_callback(current, total, message)
