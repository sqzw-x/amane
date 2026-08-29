"""出站失败与来源失败的结构化类型.

WebClient / 爬虫 / 插件 / Recorder 共用这一组类型, 避免 crawlers ↔ observability 互引.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class FailureKind(StrEnum):
    """出站请求失败的结构化分类 (语义承载在 kind/status, message 仅供展示)."""

    HTTP_STATUS = "http_status"
    """HTTP 状态 >= 400 (status 字段给出具体码)."""
    TIMEOUT = "timeout"
    CURL = "curl"
    """传输层错误."""
    UNEXPECTED = "unexpected"


@dataclass(frozen=True, slots=True)
class RequestFailure:
    """一次出站请求失败的结构化描述."""

    kind: FailureKind
    message: str
    """人类可读描述 (日志/展示用, 不承诺可解析语义)."""
    status: int | None = None
    """HTTP 状态码 (kind=HTTP_STATUS 时必有)."""
    body: bytes | None = None
    """失败响应正文 (截断), 拦截判定用."""


class FailureReason(StrEnum):
    """来源抓取失败的结构化原因 (summary.json / task report 的 reason 字段)."""

    HTTP_ERROR = "http_error"
    """4xx/5xx 兜底 (具体状态码在 http_status)."""
    NOT_FOUND = "not_found"
    RATE_LIMITED = "rate_limited"
    SERVER_ERROR = "server_error"
    TIMEOUT = "timeout"
    NETWORK = "network"
    CLOUDFLARE_CHALLENGE = "cloudflare_challenge"
    """挑战页 ("Just a moment")."""
    CLOUDFLARE_BLOCKED = "cloudflare_blocked"
    """拦截页 (Ray ID, 无挑战)."""
    IP_BANNED = "ip_banned"
    GEO_RESTRICTED = "geo_restricted"
    AGE_VERIFICATION = "age_verification"
    EMPTY_RESPONSE = "empty_response"
    NO_USABLE_METADATA = "no_usable_metadata"
    """请求成功但未解析出元数据 (区别于 NOT_FOUND 的 HTTP 404)."""
    CRAWLER_UNAVAILABLE = "crawler_unavailable"
    """爬虫实例缺失 (演员侧)."""
    UNEXPECTED = "unexpected"


class SourceError(Exception):
    """来源可分类的失败. 由 invoke_source 写入站点 outcome, 不视为整任务崩溃."""

    def __init__(
        self,
        reason: FailureReason,
        *,
        http_status: int | None = None,
        detail: str | None = None,
        url: str | None = None,
    ) -> None:
        self.reason = reason
        self.http_status = http_status
        self.detail = detail
        self.url = url
        super().__init__(detail or reason)


class RequestError(SourceError):
    """出站请求在重试用尽后仍失败."""

    def __init__(self, url: str, failure: RequestFailure | str | None = None) -> None:
        self.url = url
        if isinstance(failure, RequestFailure):
            self.failure = failure
            self.message = failure.message
            reason = classify_request_error(failure)
            http_status = failure.status
        else:
            self.failure = None
            self.message = str(failure) if failure else "request failed"
            reason = FailureReason.NETWORK
            http_status = None
        super().__init__(reason, http_status=http_status, detail=self.message, url=url)
        Exception.__init__(self, f"{url}: {self.message}")


def _status_reason(status: int) -> FailureReason:
    if status == 404:
        return FailureReason.NOT_FOUND
    if status == 429:
        return FailureReason.RATE_LIMITED
    if 500 <= status < 600:
        return FailureReason.SERVER_ERROR
    return FailureReason.HTTP_ERROR


def classify_block(text: str, *, failure: RequestFailure | None = None) -> FailureReason | None:
    """正文启发式优先 (含失败响应正文), 状态码兜底, 空响应最后."""
    if text:
        reason = _classify_text(text)
        if reason is not None:
            return reason
    if failure is not None and failure.body:
        reason = _classify_text(failure.body.decode("utf-8", errors="replace"))
        if reason is not None:
            return reason
    if failure is not None and failure.status is not None and failure.status >= 400:
        return _status_reason(failure.status)
    if not text:
        return FailureReason.EMPTY_RESPONSE
    return None


def _classify_text(text: str) -> FailureReason | None:
    lower = text.lower()
    if "not available in your region" in lower or "お住まいの地域からはご利用になれません" in text:
        return FailureReason.GEO_RESTRICTED
    if "banned your access" in lower:
        return FailureReason.IP_BANNED
    if "ray-id" in lower and "cf-" in lower:
        return FailureReason.CLOUDFLARE_BLOCKED
    if "just a moment" in lower and "cloudflare" in lower:
        return FailureReason.CLOUDFLARE_CHALLENGE
    if "driver-verify" in lower:
        return FailureReason.AGE_VERIFICATION
    if "年齢認証" in text or "age verification" in lower:
        return FailureReason.AGE_VERIFICATION
    return None


def classify_request_error(failure: RequestFailure | None) -> FailureReason:
    """失败响应正文优先, 无信号按 kind/status 兜底."""
    if failure is None:
        return FailureReason.NETWORK
    if failure.body:
        reason = _classify_text(failure.body.decode("utf-8", errors="replace"))
        if reason is not None:
            return reason
    if failure.kind == FailureKind.TIMEOUT:
        return FailureReason.TIMEOUT
    if failure.kind == FailureKind.CURL:
        return FailureReason.NETWORK
    if failure.kind == FailureKind.UNEXPECTED:
        return FailureReason.UNEXPECTED
    if failure.status is not None and failure.status >= 400:
        return _status_reason(failure.status)
    return FailureReason.HTTP_ERROR
