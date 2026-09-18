"""服务进程入口的退出码契约: 启动失败与请求重启必须可区分.

桌面壳按退出码决定是否立刻重启服务, 因此该契约是跨进程边界.
"""

from __future__ import annotations

import pytest
from uvicorn.config import STARTUP_FAILURE

from amane.server import EXIT_OK, EXIT_RESTART, EXIT_STARTUP_FAILED, main


@pytest.mark.parametrize(
    ("raised", "returned", "expected"),
    [
        pytest.param(STARTUP_FAILURE, None, EXIT_STARTUP_FAILED, id="uvicorn-启动失败改写"),
        pytest.param(1, None, 1, id="其它异常码原样透出"),
        pytest.param(None, EXIT_RESTART, EXIT_RESTART, id="请求重启保持原码"),
        pytest.param(None, EXIT_OK, EXIT_OK, id="正常停机保持原码"),
    ],
)
def test_main_exit_code(
    monkeypatch: pytest.MonkeyPatch, raised: int | None, returned: int | None, expected: int
) -> None:
    def fake_run_server(**_: object) -> int:
        if raised is not None:
            raise SystemExit(raised)
        assert returned is not None
        return returned

    monkeypatch.delenv("AMANE_HOST", raising=False)
    monkeypatch.delenv("AMANE_PORT", raising=False)
    monkeypatch.setattr("amane.server.run_server", fake_run_server)

    with pytest.raises(SystemExit) as exc:
        main()

    assert exc.value.code == expected
