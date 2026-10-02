"""出站失败与来源失败的结构化类型.

WebClient / 爬虫 / 插件 / Recorder 共用这一组类型, 避免 crawlers ↔ observability 互引.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from pydantic import ValidationError


class FailureKind(StrEnum):
    """语义在 kind/status, message 仅供展示."""

    HTTP_STATUS = "http_status"
    """status 给出具体码."""
    TIMEOUT = "timeout"
    CURL = "curl"
    UNEXPECTED = "unexpected"


@dataclass(frozen=True, slots=True)
class RequestFailure:
    kind: FailureKind
    message: str
    """日志/展示用, 不承诺可解析语义."""
    status: int | None = None
    """kind=HTTP_STATUS 时必有."""
    body: bytes | None = None
    """截断后的失败响应正文, 拦截判定用."""
    reason: FailureReason | None = None
    """失败语义已知时直接给出, 跳过正文启发式. 浏览器后端用它表达挑战未解决等下游无法从正文判定的失败."""


class FailureReason(StrEnum):
    """summary.json / task report 的 reason 字段."""

    HTTP_ERROR = "http_error"
    """其余 4xx/5xx; 具体状态码在 http_status."""
    API_ERROR = "api_error"
    """HTTP 状态正常而应答体表示失败 (GraphQL errors 等); 状态码不是失败原因, 因此不写 http_status."""
    NOT_FOUND = "not_found"
    RATE_LIMITED = "rate_limited"
    SERVER_ERROR = "server_error"
    TIMEOUT = "timeout"
    NETWORK = "network"
    CLOUDFLARE_CHALLENGE = "cloudflare_challenge"
    """Just a moment 挑战页."""
    CLOUDFLARE_BLOCKED = "cloudflare_blocked"
    """Ray ID 拦截页, 无挑战."""
    IP_BANNED = "ip_banned"
    GEO_RESTRICTED = "geo_restricted"
    AGE_VERIFICATION = "age_verification"
    EMPTY_RESPONSE = "empty_response"
    NO_USABLE_METADATA = "no_usable_metadata"
    """请求成功但未解析出元数据; 区别于 NOT_FOUND 的 HTTP 404."""
    PARSE_ERROR = "parse_error"
    """响应与读模型不符 (字段缺失/类型变化); 通常意味着站点 schema 已变更."""
    CRAWLER_UNAVAILABLE = "crawler_unavailable"
    """来源实例缺失或构造失败 (配置与插件不再匹配等)."""
    UNEXPECTED = "unexpected"


class SourceError(Exception):
    """由 invoke_source 写入站点 outcome, 不视为整任务崩溃."""

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


def parse_detail(exc: ValidationError) -> str:
    """把读模型校验失败压成字段路径 (如 ``data.ppvContent.sample2DMovie``).

    站点响应是外部输入, 只暴露定位所需的位置, 不把 pydantic 全文与内部类名写入 detail.
    """
    paths = [".".join(str(part) for part in error["loc"]) for error in exc.errors()]
    return ", ".join(dict.fromkeys(path for path in paths if path)) or "unknown"


def _status_reason(status: int) -> FailureReason:
    if status == 404:
        return FailureReason.NOT_FOUND
    if status == 429:
        return FailureReason.RATE_LIMITED
    if 500 <= status < 600:
        return FailureReason.SERVER_ERROR
    return FailureReason.HTTP_ERROR


def classify_block(text: str, *, failure: RequestFailure | None = None) -> FailureReason | None:
    """正文启发式优先, 再按状态码分类, 空响应最后."""
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
    # 挑战页按平台标记识别, 不依赖页面语言 (本地化标题不含 "just a moment"). ``_cf_chl_opt`` 只在
    # 挑战页出现: ``challenge-platform`` 亦是正常页面的 JS Detection 脚本, 不能单独作为判据.
    # 挑战页同样带 ray-id, 因此必须先于封禁页判定.
    if "_cf_chl_opt" in lower:
        return FailureReason.CLOUDFLARE_CHALLENGE
    if "just a moment" in lower and "cloudflare" in lower:
        return FailureReason.CLOUDFLARE_CHALLENGE
    if "ray-id" in lower and "cf-" in lower:
        return FailureReason.CLOUDFLARE_BLOCKED
    if "driver-verify" in lower:
        return FailureReason.AGE_VERIFICATION
    if "年齢認証" in text or "age verification" in lower:
        return FailureReason.AGE_VERIFICATION
    return None


def classify_request_error(failure: RequestFailure | None) -> FailureReason:
    """无正文信号时按 kind/status 分类."""
    if failure is None:
        return FailureReason.NETWORK
    if failure.reason is not None:
        return failure.reason
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
