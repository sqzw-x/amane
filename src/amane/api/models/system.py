from datetime import datetime

from pydantic import BaseModel, Field

from ...db.profiling import DbErrorKind


class DesktopResponse(BaseModel):
    version: str
    data_dir: str
    supervised: bool = False


class ReleaseResponse(BaseModel):
    current: str
    latest: str | None
    html_url: str | None
    newer: bool


class DbErrorStatResponse(BaseModel):
    """按类别累计的数据库错误; ``last_message`` 与 ``last_at`` 取最后一次."""

    kind: DbErrorKind
    count: int
    last_message: str | None = None
    last_at: datetime | None = None


class DatabaseMetricsResponse(BaseModel):
    """进程内累计, 重启清零. 提交时长含等待写锁与 fsync."""

    commits: int
    slow_commits: int
    max_commit_ms: float | None = None
    slowest_statement: str | None = None
    slowest_statement_ms: float | None = None
    pool_checked_out: int | None = None
    errors: list[DbErrorStatResponse] = Field(default_factory=list)
