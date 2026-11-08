"""structlog 兼容 stdlib logging; contextvars 注入 task_id / request_id. 进程启动时调用 setup_logging()."""

import asyncio
import logging
import sys
import time
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from typing import TYPE_CHECKING

import structlog

from ..events import EventType

if TYPE_CHECKING:
    from pathlib import Path

    from ..events import EventBus


MB = 1024 * 1024


def _record_timestamp(created: float) -> str:
    return datetime.fromtimestamp(created, UTC).isoformat().replace("+00:00", "Z")


def _timestamp(
    _logger: logging.Logger, _method_name: str, event_dict: structlog.typing.EventDict
) -> structlog.typing.EventDict:
    """stdlib 记录用 LogRecord 的创建时间, 与 WebSocket 事件共用同一来源."""
    record = event_dict.get("_record")
    created = record.created if isinstance(record, logging.LogRecord) else time.time()
    event_dict["timestamp"] = _record_timestamp(created)
    return event_dict


# 共享处理器链 (渲染前注入上下文). app.log / task.log 的 foreign_pre_chain 也复用这份,
# 否则 stdlib 记录落盘时没有 level / logger / timestamp, 与 WS 事件不同形.
SHARED_PROCESSORS: tuple[structlog.types.Processor, ...] = (
    structlog.contextvars.merge_contextvars,
    structlog.stdlib.add_logger_name,
    structlog.stdlib.add_log_level,
    _timestamp,
    structlog.processors.StackInfoRenderer(),
    structlog.processors.ExceptionRenderer(),
    structlog.stdlib.ExtraAdder(),
)


class WSEventLogHandler(logging.Handler):
    """转发为 WebSocket LOG 事件, 含 contextvars 与 structlog extra."""

    _SKIP_ATTRS = frozenset(
        {
            "name",
            "msg",
            "args",
            "levelname",
            "levelno",
            "pathname",
            "filename",
            "module",
            "exc_info",
            "exc_text",
            "stack_info",
            "lineno",
            "funcName",
            "created",
            "msecs",
            "relativeCreated",
            "thread",
            "threadName",
            "processName",
            "process",
            "message",
            "taskName",
            "color_message",
        }
    )

    def __init__(self, event_bus: EventBus) -> None:
        super().__init__()
        self._event_bus = event_bus

    def emit(self, record: logging.LogRecord) -> None:
        try:
            source = record.name.rsplit(".", 1)[-1] if "." in record.name else record.name

            # structlog 经 wrap_for_formatter 把 event_dict 写入 record.msg; stdlib 则为普通字符串
            if isinstance(record.msg, dict):
                event_dict: dict = record.msg
                message = str(event_dict.get("event", ""))
                data: dict = {
                    "level": record.levelname,
                    "source": source,
                    "message": message,
                }
                # "timestamp" 不在此集合中, 必须转发原始日志时间戳
                _INTERNAL_KEYS = {"event", "level", "logger", "_record", "_from_decorator"}
                for key, value in event_dict.items():
                    if key in _INTERNAL_KEYS:
                        continue
                    if isinstance(value, (str, int, float, bool, list, dict, type(None))):
                        data[key] = value
            else:
                message = record.getMessage()
                data = {
                    **structlog.contextvars.get_contextvars(),
                    "level": record.levelname,
                    "source": source,
                    "message": message,
                }
                for key, value in record.__dict__.items():
                    if key.startswith("_") or key in self._SKIP_ATTRS:
                        continue
                    if isinstance(value, (str, int, float, bool, list, dict, type(None))):
                        data[key] = value

            # stdlib 记录没有 structlog 的 TimeStamper, 用 LogRecord 的创建时间补齐.
            data.setdefault("timestamp", _record_timestamp(record.created))

            try:
                loop = asyncio.get_running_loop()
                loop.call_soon_threadsafe(asyncio.ensure_future, self._event_bus.emit(EventType.LOG, data))
            except RuntimeError:
                pass  # 无运行中的事件循环则跳过
        except Exception:
            # handler 自身不能抛出异常
            pass


def setup_logging(level: str = "INFO", event_bus: EventBus | None = None, log_dir: Path | None = None) -> None:
    """幂等. 已有 handler 时直接返回."""
    root = logging.getLogger("amane")
    if root.handlers:
        return

    structlog.configure(
        processors=[
            *SHARED_PROCESSORS,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    root.setLevel(level.upper())

    console_formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty()),
        ],
        foreign_pre_chain=SHARED_PROCESSORS,
    )
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(console_formatter)
    root.addHandler(stderr_handler)

    if log_dir:
        log_dir.mkdir(parents=True, exist_ok=True)

        json_formatter = structlog.stdlib.ProcessorFormatter(
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.processors.JSONRenderer(),
            ],
            foreign_pre_chain=SHARED_PROCESSORS,
        )
        file_handler = RotatingFileHandler(
            log_dir / "app.log",
            maxBytes=10 * MB,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(json_formatter)
        root.addHandler(file_handler)

        # request 独立文件, 不向 root 传播
        request_logger = logging.getLogger("amane.request")
        request_logger.propagate = False
        req_console_formatter = structlog.stdlib.ProcessorFormatter(
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty()),
            ],
            foreign_pre_chain=SHARED_PROCESSORS,
        )
        req_json_formatter = structlog.stdlib.ProcessorFormatter(
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.processors.JSONRenderer(),
            ],
            foreign_pre_chain=SHARED_PROCESSORS,
        )

        req_file_handler = RotatingFileHandler(
            log_dir / "request.log",
            maxBytes=10 * MB,
            backupCount=5,
            encoding="utf-8",
        )
        req_file_handler.setFormatter(req_json_formatter)
        request_logger.addHandler(req_file_handler)

        req_stderr_handler = logging.StreamHandler(sys.stderr)
        req_stderr_handler.setFormatter(req_console_formatter)
        request_logger.addHandler(req_stderr_handler)
    else:
        # 无 log_dir 时 request 仍输出到 stderr
        request_logger = logging.getLogger("amane.request")
        request_logger.propagate = False
        req_console_formatter = structlog.stdlib.ProcessorFormatter(
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty()),
            ],
            foreign_pre_chain=SHARED_PROCESSORS,
        )
        req_stderr_handler = logging.StreamHandler(sys.stderr)
        req_stderr_handler.setFormatter(req_console_formatter)
        request_logger.addHandler(req_stderr_handler)

    if event_bus:
        ws_handler = WSEventLogHandler(event_bus)
        ws_handler.setLevel(logging.DEBUG)
        root.addHandler(ws_handler)

    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("watchdog").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
