"""连通性探测的结果类型与传输原语.

一次探测 = 一次请求 + 一次判定. 判定复用刮削路径上的分类器 (``classify_block`` 与 ``RequestError``
自带的 ``FailureReason``), 因此结论与真实刮削同源: 正文命中拦截页、被地域限制、超时都按同一套枚举报出.
来源级编排 (枚举来源、逐来源调用) 见 ``amane.crawlers.connectivity``.

本模块只依赖 ``net.errors``, 不 import 爬虫层与配置层, 以便插件 SDK (``amane.plugin``) 直接复用结果类型.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from .errors import FailureReason, RequestError, classify_block

if TYPE_CHECKING:
    from curl_cffi.requests import Response

    from .http import WebClient


class ConnectivityStatus(StrEnum):
    """一次探测的结论. ``SKIPPED`` 是该来源本次不探测, 不是失败."""

    OK = "ok"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class ConnectivityOutcome:
    """单个来源的探测结论.

    ``url`` / ``http_status`` / ``reason`` 只在与状态相符时给出; ``detail`` 只写用户能据以行动的信息,
    不写上游地址与密钥 (失败原因的本地化由前端按 ``reason`` 完成, ``detail`` 是补充说明).
    """

    status: ConnectivityStatus
    url: str | None = None
    http_status: int | None = None
    reason: FailureReason | None = None
    detail: str | None = None

    @classmethod
    def ok(cls, url: str, http_status: int | None) -> ConnectivityOutcome:
        return cls(ConnectivityStatus.OK, url=url, http_status=http_status)

    @classmethod
    def failed(
        cls,
        reason: FailureReason,
        *,
        url: str | None = None,
        http_status: int | None = None,
        detail: str | None = None,
    ) -> ConnectivityOutcome:
        return cls(ConnectivityStatus.FAILED, url=url, http_status=http_status, reason=reason, detail=detail)

    @classmethod
    def skipped(cls, detail: str) -> ConnectivityOutcome:
        return cls(ConnectivityStatus.SKIPPED, detail=detail)


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
    timeout: float | None = None,
) -> ConnectivityOutcome:
    """GET 探测一个地址.

    单次尝试: 探测要回答的是「此刻能否连通」, 重试只会把同一个结论拖长; 探测也不设时限预算, 用
    当前 ``network.timeout``. 失败分类直接取 ``RequestError`` 上已有的 ``reason`` / ``http_status``.
    """
    try:
        resp = await web.request("GET", url, cookies=cookies, headers=headers, timeout=timeout, max_attempts=1)
    except RequestError as exc:
        return ConnectivityOutcome.failed(exc.reason, url=url, http_status=exc.http_status)
    return assess_response(url, resp)
