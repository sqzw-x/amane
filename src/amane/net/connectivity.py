"""连通性探测的结果类型与传输原语.

一次探测 = 一次请求 + 一次判定. 判定复用刮削路径上的分类器 (``classify_block`` 与 ``RequestError``
自带的 ``FailureReason``), 因此结论与真实刮削同源: 正文命中拦截页、被地域限制、超时都按同一套枚举报出.
来源级编排 (枚举来源、逐来源调用) 见 ``amane.crawlers.connectivity``.

本模块只依赖 ``net.errors``, 不 import 爬虫层与配置层, 以便插件 SDK (``amane.plugin``) 直接复用结果类型.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Self

from pydantic import BaseModel, ConfigDict, model_validator

from .errors import FailureReason, RequestError, classify_block

if TYPE_CHECKING:
    from curl_cffi.requests import Response

    from .http import WebClient


class ConnectivityStatus(StrEnum):
    """一次探测的结论. ``SKIPPED`` 是该来源本次不探测, 不是失败."""

    OK = "ok"
    FAILED = "failed"
    SKIPPED = "skipped"


class SkipReason(StrEnum):
    """``SKIPPED`` 的原因.

    与 ``FailureReason`` 分开: 这一档不是失败, 文案也不进任务报告. 界面按它本地化, 因此每种原因
    都要能独立读懂, 不依赖 ``detail``.
    """

    UNKNOWN_SOURCE = "unknown_source"
    """来源不存在或未启用. 与 ``FailureReason.NOT_FOUND`` 区分: 后者是站点未找到该内容."""
    NO_HTTP_UPSTREAM = "no_http_upstream"
    """本源不经 HTTP 取数据 (离线镜像等)."""
    MISSING_CREDENTIAL = "missing_credential"
    """需要凭据而凭据缺失, 刮削同样会跳过."""
    UNDECLARED = "undeclared"
    """来源未声明探测方式."""
    NO_URL = "no_url"
    """插件既未声明探测方式, 也未声明来源 URL."""


class ConnectivityOutcome(BaseModel):
    """单个来源的探测结论.

    ``url`` / ``http_status`` / ``reason`` 只在与状态相符时给出; ``skip_reason`` 说明不探测的原因.
    ``detail`` 只写补充说明, 且必须是语言中立的 (界面原样渲染, 不翻译): 失败原因的本地化由前端按
    ``reason`` / ``skip_reason`` 完成, 因此这里不放上游地址、密钥与中文句子.

    取值由 pydantic 校验, 且必须在这里: 结论类型是插件 SDK 的出口, 手写取值若留到响应模型才被拒, 就会在
    逐来源保护之外抛校验错误, 让整个端点 500 并丢掉其它来源的结论; 在构造处抛出则落进
    ``ConnectivityChecker._run`` 的异常保护, 只使该来源报 ``unexpected``. 枚举字段接受值字符串 (API JSON
    里的形态) 并收敛成成员, 拼错与跨枚举照旧被拒.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: ConnectivityStatus
    url: str | None = None
    http_status: int | None = None
    reason: FailureReason | None = None
    skip_reason: SkipReason | None = None
    detail: str | None = None

    @model_validator(mode="after")
    def _reason_matches_status(self) -> Self:
        """原因字段只在对应状态出现: 失败必有 ``reason``, 未探测必有 ``skip_reason``, 其余两者皆空."""
        if (self.reason is not None) != (self.status is ConnectivityStatus.FAILED):
            raise ValueError("reason must be set exactly when status is FAILED")
        if (self.skip_reason is not None) != (self.status is ConnectivityStatus.SKIPPED):
            raise ValueError("skip_reason must be set exactly when status is SKIPPED")
        return self

    @classmethod
    def ok(cls, url: str, http_status: int | None) -> Self:
        return cls(status=ConnectivityStatus.OK, url=url, http_status=http_status)

    @classmethod
    def failed(
        cls,
        reason: FailureReason,
        *,
        url: str | None = None,
        http_status: int | None = None,
        detail: str | None = None,
    ) -> Self:
        return cls(status=ConnectivityStatus.FAILED, url=url, http_status=http_status, reason=reason, detail=detail)

    @classmethod
    def skipped(cls, reason: SkipReason, detail: str | None = None) -> Self:
        return cls(status=ConnectivityStatus.SKIPPED, skip_reason=reason, detail=detail)


def assess_response(url: str, resp: Response) -> ConnectivityOutcome:
    """按响应正文判定成功: 2xx 也可能是拦截页或空页, 与 ``HttpClient.get_html`` 用同一套启发式."""
    try:
        text = resp.text
    except Exception:
        text = ""
    reason = classify_block(text)
    if reason is not None:
        return ConnectivityOutcome.failed(reason, url=url, http_status=resp.status_code)
    return ConnectivityOutcome.ok(url, resp.status_code)


async def probe_get(
    web: WebClient,
    url: str,
    *,
    cookies: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
) -> ConnectivityOutcome:
    """GET 探测一个地址.

    单次尝试: 探测要回答的是「此刻能否连通」, 重试只会把同一个结论拖长; 时限用当前 ``network.timeout``.
    失败分类直接取 ``RequestError`` 上已有的 ``reason`` / ``http_status``.
    """
    try:
        resp = await web.request("GET", url, cookies=cookies, headers=headers, max_attempts=1)
    except RequestError as exc:
        return ConnectivityOutcome.failed(exc.reason, url=url, http_status=exc.http_status)
    return assess_response(url, resp)
