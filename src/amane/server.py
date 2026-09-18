from __future__ import annotations

import os
import sys

import uvicorn
from uvicorn.config import STARTUP_FAILURE

from .api.app import create_app

EXIT_OK = 0
EXIT_RESTART = 3
EXIT_STARTUP_FAILED = 4


def run_server(*, host: str, port: int, log_level: str = "info") -> int:
    """阻塞运行直到停机, 返回退出码 (0 正常停 / 3 请求重启).

    启动错误 (地址无法绑定、端口占用、配置非法) 不返回: uvicorn 以
    `STARTUP_FAILURE` 抛出 `SystemExit`, 由 `main` 改写为 `EXIT_STARTUP_FAILED`.
    """
    app = create_app()
    app.state.exit_code = EXIT_OK
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=host,
            port=port,
            log_level=log_level,
            timeout_graceful_shutdown=5,
        )
    )
    app.state.server = server
    server.run()
    code: int = app.state.exit_code
    return code


def main() -> None:
    if getattr(sys, "frozen", False):
        os.environ.setdefault("PYDANTIC_DISABLE_PLUGINS", "1")
    host = os.environ.get("AMANE_HOST", "0.0.0.0")
    port = int(os.environ.get("AMANE_PORT", "8000"))
    try:
        code = run_server(host=host, port=port)
    except SystemExit as exc:
        # uvicorn 的启动失败码与 EXIT_RESTART 相同, 进程外无法区分, 因此改写为独立退出码.
        sys.exit(EXIT_STARTUP_FAILED if exc.code == STARTUP_FAILURE else exc.code)
    sys.exit(code)


if __name__ == "__main__":
    main()
