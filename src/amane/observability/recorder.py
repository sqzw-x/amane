"""叙事日志与结构化产物同一目录、同一生命周期. WebClient 经 ContextVar 接入 HTTP 缓冲."""

from __future__ import annotations

import contextlib
import json
import logging
import shutil
from contextvars import ContextVar, Token
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from ..net.errors import FailureReason
from ..net.recording import bind_http_recorder_lookup, skip_http_body
from ..version import get_version
from .models import (
    OUTCOME_DETAIL_LIMIT,
    RECORD_VERSION,
    SECRETS_HOT_FILENAME,
    CaptureReason,
    HttpExchangeMeta,
    RecordManifest,
    SiteOutcomeKind,
    SiteOutcomeRecord,
    TaskSnapshot,
    TaskSummary,
)
from .redact import hot_slice_for_task, needs_secrets_file, redact_hot

if TYPE_CHECKING:
    from ..config import HotSettings
    from ..db.models import Task

_log = structlog.get_logger()

_recorder_ctx: ContextVar[Recorder | None] = ContextVar("task_recorder", default=None)
_task_id_ctx: ContextVar[int | None] = ContextVar("task_id", default=None)


def get_recorder() -> Recorder | None:
    """无 begin 时为 None."""
    return _recorder_ctx.get()


def current() -> Recorder | _LogOnly:
    """无任务上下文时退回仅转发 logger 的空壳."""
    return _recorder_ctx.get() or _LogOnly(_log)


def get_task_id() -> int | None:
    return _task_id_ctx.get()


class TaskIdFilter(logging.Filter):
    def __init__(self, task_id: int) -> None:
        super().__init__()
        self._task_id = task_id

    def filter(self, record: logging.LogRecord) -> bool:
        return _task_id_ctx.get() == self._task_id


def task_dir_for(log_dir: Path, task_id: int) -> Path:
    return log_dir / "tasks" / f"task-{task_id}"


def remove_task_dir(log_dir: Path, task_id: int) -> None:
    path = task_dir_for(log_dir, task_id)
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)


def amane_version() -> str:
    return get_version()


@dataclass
class _PendingExchange:
    meta: HttpExchangeMeta
    body: bytes | None


@dataclass(frozen=True, slots=True)
class _LogOnly:
    """无 begin 时叙事转发 logger, 结构化落盘为空操作."""

    _logger: Any

    def debug(self, event: str, **kwargs: Any) -> None:
        self._logger.debug(event, **kwargs)

    def info(self, event: str, **kwargs: Any) -> None:
        self._logger.info(event, **kwargs)

    def warning(self, event: str, **kwargs: Any) -> None:
        self._logger.warning(event, **kwargs)

    def error(self, event: str, **kwargs: Any) -> None:
        self._logger.error(event, **kwargs)

    def exception(self, event: str, **kwargs: Any) -> None:
        self._logger.exception(event, **kwargs)

    def update_summary(self, **kwargs: Any) -> None:
        return None

    def record_site_outcome(
        self,
        *,
        site: str,
        outcome: SiteOutcomeKind,
        reason: FailureReason | None = None,
        http_status: int | None = None,
        detail: str | None = None,
    ) -> None:
        return None

    def site_outcomes(self) -> list[SiteOutcomeRecord]:
        return []

    def note_cache_hit(self, cache_key: str) -> None:
        return None

    def write_raw_cache(self, raw: dict[str, Any]) -> None:
        return None

    def record_http(self, **kwargs: Any) -> None:
        return None


class Recorder:
    """叙事方法经 structlog, 由本实例安装的 FileHandler 写入 task.log."""

    def __init__(self, root: Path, task_id: int):
        self.root = root
        self.task_id = task_id
        self.summary = TaskSummary()
        self._exchanges: list[_PendingExchange] = []
        self._seq = 0
        self._http_captured = False
        self._capture_reason = CaptureReason.NONE
        self._recorder_token: Token[Recorder | None] | None = None
        self._task_id_token: Token[int | None] | None = None
        self._log_handler: logging.FileHandler | None = None
        self._logger = structlog.get_logger()

    @classmethod
    def begin(cls, log_dir: Path, task: Task, hot: HotSettings) -> Recorder:
        assert task.id is not None
        root = task_dir_for(log_dir, task.id)
        root.mkdir(parents=True, exist_ok=True)
        (root / "summary.json").unlink(missing_ok=True)  # id 复用时删除残留刮削摘要
        rec = cls(root, task.id)
        rec._task_id_token = _task_id_ctx.set(task.id)
        rec._recorder_token = _recorder_ctx.set(rec)
        rec._install_log_handler()
        rec._write_task_snapshot(task)
        task_type = str(task.type)
        hot_dump = hot_slice_for_task(hot.model_dump(mode="json"), task_type)
        rec._write_json(root / "config.hot.json", redact_hot(hot_dump))
        if needs_secrets_file(hot_dump):
            rec._write_json(root / SECRETS_HOT_FILENAME, hot_dump)
        return rec

    def debug(self, event: str, **kwargs: Any) -> None:
        self._logger.debug(event, **kwargs)

    def info(self, event: str, **kwargs: Any) -> None:
        self._logger.info(event, **kwargs)

    def warning(self, event: str, **kwargs: Any) -> None:
        self._logger.warning(event, **kwargs)

    def error(self, event: str, **kwargs: Any) -> None:
        self._logger.error(event, **kwargs)

    def exception(self, event: str, **kwargs: Any) -> None:
        self._logger.exception(event, **kwargs)

    def record_http(
        self,
        *,
        method: str,
        url: str,
        status: int | None,
        error: str | None,
        content_type: str | None,
        body: bytes | None,
        elapsed_ms: int | None,
        attempts: int | None = None,
        capture_body: bool = True,
    ) -> None:
        """body 只写入 http/, 不写入 task.log."""
        site = _site_from_context()
        self._seq += 1
        seq = self._seq
        body_file: str | None = None
        stored: bytes | None = None
        if capture_body and body is not None and not skip_http_body():
            ext = _guess_ext(content_type, body)
            body_file = f"bodies/{seq:04d}{ext}"
            stored = body
        self._exchanges.append(
            _PendingExchange(
                meta=HttpExchangeMeta(
                    seq=seq,
                    site=site,
                    method=method.upper(),
                    url=url,
                    status=status,
                    error=error,
                    content_type=content_type,
                    body_file=body_file,
                    elapsed_ms=elapsed_ms,
                ),
                body=stored,
            )
        )
        payload: dict[str, Any] = {
            "seq": seq,
            "site": site,
            "method": method.upper(),
            "url": url,
            "status": status,
            "elapsed_ms": elapsed_ms,
        }
        if error:
            payload["error"] = error
        if attempts is not None:
            payload["attempts"] = attempts
        if error:
            self.warning("http exchange", **payload)
        else:
            self.debug("http exchange", **payload)

    def update_summary(self, **kwargs: Any) -> None:
        self.summary = self.summary.model_copy(update=kwargs)

    def record_site_outcome(
        self,
        *,
        site: str,
        outcome: SiteOutcomeKind,
        reason: FailureReason | None = None,
        http_status: int | None = None,
        detail: str | None = None,
    ) -> None:
        """同站点多次上报合并: outcome 取更差; 已写入的 reason 不被后续默认值覆盖."""
        existing = self.summary.outcomes.get(site)
        if existing is None:
            record = SiteOutcomeRecord(
                site=site, outcome=outcome, reason=reason, http_status=http_status, detail=detail
            )
        else:
            record = existing.model_copy(
                update={
                    "outcome": _worse_outcome(existing.outcome, outcome),
                    "reason": existing.reason or reason,
                    "http_status": existing.http_status if existing.http_status is not None else http_status,
                    "detail": existing.detail or detail,
                }
            )
        self.summary = self.summary.model_copy(update={"outcomes": {**self.summary.outcomes, site: record}})

    def site_outcomes(self) -> list[SiteOutcomeRecord]:
        """本次任务已上报的站点结果, 按查询顺序; `detail` 截断后随任务结果返回."""
        out: list[SiteOutcomeRecord] = []
        for site in self.summary.sites_queried:
            record = self.summary.outcomes.get(site)
            if record is None:
                continue
            detail = record.detail
            if detail is not None and len(detail) > OUTCOME_DETAIL_LIMIT:
                record = record.model_copy(update={"detail": detail[:OUTCOME_DETAIL_LIMIT], "detail_truncated": True})
            out.append(record)
        return out

    def note_cache_hit(self, cache_key: str) -> None:
        self.record_site_outcome(site=cache_key, outcome=SiteOutcomeKind.CACHE_HIT)
        self.debug("site cache hit", cache_key=cache_key)

    def write_raw_cache(self, raw: dict[str, Any]) -> None:
        self._write_json(self.root / "raw_cache.json", raw)

    def finalize(
        self,
        task: Task,
        *,
        success: bool,
        error: str | None,
        debug_capture: bool,
    ) -> None:
        try:
            self._close_log_handler()
            self._write_task_snapshot(task)
            if self.summary.eligible_sites or self.summary.sites_queried or self.summary.outcomes:
                self._write_json(self.root / "summary.json", self.summary.model_dump(mode="json"))

            keep_http = (not success) or debug_capture
            if keep_http and self._exchanges:
                self._flush_http(self.root / "http")
                self._http_captured = True
                self._capture_reason = CaptureReason.DEBUG_FLAG if debug_capture and success else CaptureReason.FAILURE
            else:
                self._discard_http_bodies()
                self._http_captured = False
                self._capture_reason = CaptureReason.NONE

            manifest = RecordManifest(
                record_version=RECORD_VERSION,
                amane_version=amane_version(),
                created_at=datetime.now(UTC),
                task_id=self.task_id,
                redacted=True,
                http_captured=self._http_captured,
                capture_reason=self._capture_reason,
            )
            self._write_json(self.root / "manifest.json", manifest.model_dump(mode="json"))
        finally:
            self.close()

    def close(self) -> None:
        self._close_log_handler()
        # Worker stop / lifespan teardown may run in a different asyncio Context than begin();
        # ContextVar.reset then raises ValueError - clear tokens without resetting foreign context.
        if self._recorder_token is not None:
            with contextlib.suppress(ValueError):
                _recorder_ctx.reset(self._recorder_token)
            self._recorder_token = None
        if self._task_id_token is not None:
            with contextlib.suppress(ValueError):
                _task_id_ctx.reset(self._task_id_token)
            self._task_id_token = None

    def _install_log_handler(self) -> None:
        handler = logging.FileHandler(self.root / "task.log", encoding="utf-8")
        handler.setFormatter(
            structlog.stdlib.ProcessorFormatter(
                processors=[
                    structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                    structlog.processors.JSONRenderer(),
                ]
            )
        )
        handler.addFilter(TaskIdFilter(self.task_id))
        logging.getLogger("amane").addHandler(handler)
        self._log_handler = handler

    def _close_log_handler(self) -> None:
        handler = self._log_handler
        if handler is None:
            return
        self._log_handler = None
        root_logger = logging.getLogger("amane")
        root_logger.removeHandler(handler)
        handler.close()

    def _flush_http(self, http_dir: Path) -> None:
        http_dir.mkdir(parents=True, exist_ok=True)
        (http_dir / "bodies").mkdir(parents=True, exist_ok=True)
        with (http_dir / "index.jsonl").open("w", encoding="utf-8") as f:
            for item in self._exchanges:
                if item.body is not None and item.meta.body_file:
                    (http_dir / item.meta.body_file).write_bytes(item.body)
                f.write(item.meta.model_dump_json() + "\n")

    def _discard_http_bodies(self) -> None:
        for item in self._exchanges:
            item.body = None
            item.meta.body_file = None

    def _write_task_snapshot(self, task: Task) -> None:
        assert task.id is not None
        snap = TaskSnapshot(
            id=task.id,
            type=str(task.type),
            status=str(task.status),
            payload=task.payload or {},
            result=task.result,
            error=task.error,
            priority=task.priority,
            created_at=task.created_at,
            started_at=task.started_at,
            finished_at=task.finished_at,
        )
        self._write_json(self.root / "task.json", snap.model_dump(mode="json"))

    @staticmethod
    def _write_json(path: Path, data: Any) -> None:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def load_http_index(http_dir: Path) -> list[tuple[HttpExchangeMeta, bytes | None]]:
    index_path = http_dir / "index.jsonl"
    if not index_path.is_file():
        return []
    out: list[tuple[HttpExchangeMeta, bytes | None]] = []
    for line in index_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        meta = HttpExchangeMeta.model_validate_json(line)
        body: bytes | None = None
        if meta.body_file:
            body_path = http_dir / meta.body_file
            if body_path.is_file():
                body = body_path.read_bytes()
        out.append((meta, body))
    return out


def _site_from_context() -> str | None:
    try:
        ctx = structlog.contextvars.get_contextvars()
        site = ctx.get("site")
        return str(site) if site is not None else None
    except Exception:
        return None


def _guess_ext(content_type: str | None, body: bytes) -> str:
    if content_type:
        ct = content_type.split(";")[0].strip().lower()
        if "json" in ct:
            return ".json"
        if "html" in ct:
            return ".html"
        if "xml" in ct:
            return ".xml"
        if ct.startswith("text/"):
            return ".txt"
    stripped = body.lstrip()[:1]
    if stripped in (b"{", b"["):
        try:
            json.loads(body)
            return ".json"
        except Exception:
            pass
    if stripped == b"<" or b"<html" in body[:200].lower():
        return ".html"
    return ".bin"


def _worse_outcome(a: SiteOutcomeKind, b: SiteOutcomeKind) -> SiteOutcomeKind:
    order = {SiteOutcomeKind.CACHE_HIT: 0, SiteOutcomeKind.OK: 1, SiteOutcomeKind.FAILED: 2}
    return a if order[a] >= order[b] else b


# 避免 net → observability 硬依赖
bind_http_recorder_lookup(get_recorder)
