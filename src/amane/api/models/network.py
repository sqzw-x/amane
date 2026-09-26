"""``/api/network`` 的请求与响应.

``source_id`` 一律是 ``str``: 来源集合含第三方插件 ID (``namespace.local``), 不收成 ``SiteName`` 枚举,
否则插件来源既不能被请求, 回显也会被 schema 拒掉.
"""

from pydantic import BaseModel, ConfigDict, Field

from ...crawlers.connectivity import SourceKind
from ...net.connectivity import ConnectivityStatus, SkipReason
from ...net.errors import FailureReason


class ConnectivityCheckRequest(BaseModel):
    """缺省或空 ``source_ids`` = 探测当前配置真正会请求的全部来源."""

    model_config = ConfigDict(extra="forbid")

    source_ids: list[str] | None = None


class ConnectivityItemResponse(BaseModel):
    """一个来源的探测结果.

    ``reason`` (失败) 与 ``skip_reason`` (未探测) 都是枚举, 本地化由前端完成; ``detail`` 是语言中立的
    补充说明 (例如异常类名), 界面原样渲染. ``elapsed_ms`` 只在真正探测过时有值.
    """

    model_config = ConfigDict(extra="forbid")

    source_id: str
    name: str
    kind: SourceKind
    status: ConnectivityStatus
    url: str | None = None
    http_status: int | None = None
    reason: FailureReason | None = None
    skip_reason: SkipReason | None = None
    detail: str | None = None
    elapsed_ms: int | None = None


class ConnectivityReportResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ConnectivityItemResponse] = Field(default_factory=list)
