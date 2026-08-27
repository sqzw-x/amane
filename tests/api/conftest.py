"""服务端 API 测试的共享 fixtures

使用 tmp_path 下的文件数据库.
"""

import os
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
import tomli_w
from httpx2 import ASGITransport, AsyncClient

from amane.api.app import create_app
from amane.api.routes import API_PREFIX
from amane.config import HotSettings
from amane.net.errors import FailureKind, RequestError, RequestFailure
from tests.schema_template import copy_schema

if TYPE_CHECKING:
    from pathlib import Path

    from fastapi import FastAPI

    from amane.db.repository import Repository


# API 测试写入的 worker.poll_interval. 生产默认 2s, 空队列认领会把任务测试钉在 2s+ 上.
TEST_WORKER_POLL_INTERVAL = 0.1


def hot_for_tests(hot: HotSettings | None = None) -> HotSettings:
    """API 夹具用 HotSettings: 保留调用方覆盖, 只把 poll_interval 压到测试下限."""
    base = hot if hot is not None else HotSettings()
    return base.model_copy(
        update={"worker": base.worker.model_copy(update={"poll_interval": TEST_WORKER_POLL_INTERVAL})}
    )


def make_app(hot: HotSettings, data_dir: Path, log_dir: Path, files_dir: Path, *, supervised: bool = False) -> FastAPI:
    os.environ["AMANE_DATA_DIR"] = str(data_dir)
    os.environ["AMANE_SAFE_DIRS"] = str(files_dir)
    os.environ["AMANE_LOG_DIR"] = str(log_dir)
    os.environ["AMANE_TOKEN"] = "off"
    os.environ["AMANE_SUPERVISED"] = "1" if supervised else "0"

    data_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    files_dir.mkdir(parents=True, exist_ok=True)
    hot = hot_for_tests(hot)
    config_path = data_dir / "config.toml"
    config_path.write_text(tomli_w.dumps(hot.model_dump(mode="json", exclude_none=True)))
    copy_schema(data_dir / "amane.db")

    return create_app()


class _SilentFeedHttp:
    """API 测试不打真网: 创建源时的即时拉取立刻失败返回."""

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        cookies: dict[str, str] | None = None,
        data: object | None = None,
        json: object | None = None,
        use_proxy: bool = True,
        timeout: float | None = None,
        allow_redirects: bool = True,
        ok_statuses: frozenset[int] | None = None,
    ) -> None:
        raise RequestError(url, RequestFailure(kind=FailureKind.CURL, message="test stub"))


@pytest.fixture
def app(tmp_path: Path):
    data_dir = tmp_path / "data"
    log_dir = tmp_path / "logs"
    files_dir = tmp_path / "files"
    return make_app(HotSettings(), data_dir, log_dir, files_dir)


@pytest.fixture
def safe_path(tmp_path: Path) -> Path:
    """
    干净的文件安全目录, 供文件浏览测试使用.

    独立于数据目录 (data/), 不受 lifespan 产生的 config.toml / amane.db 等文件干扰.
    现有测试中用作 scan/organize/watch path 可继续使用 tmp_path 或 safe_path.
    """
    return tmp_path / "files"


@pytest_asyncio.fixture
async def client(app: FastAPI):
    """httpx.AsyncClient, base_url 已包含 /api/ 前缀."""
    ctx = app.router.lifespan_context(app)
    await ctx.__aenter__()
    feed_service = app.state.runtime.feed_service
    if feed_service is not None:
        feed_service.set_web_client(_SilentFeedHttp())  # pyright: ignore[reportArgumentType]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url=f"http://test{API_PREFIX}/") as c:
        yield c

    await ctx.__aexit__(None, None, None)


@pytest_asyncio.fixture
async def repo(app: FastAPI, client: AsyncClient) -> Repository:
    """
    获取 lifespan 创建的 Repository (文件数据库).

    和 worker 共享同一数据库. ``client`` 参数仅用于确保 lifespan 已进入.
    """
    return app.state.runtime.repo


@pytest_asyncio.fixture
async def seed_library(repo: Repository) -> Repository:
    """播种一个 id=1 的默认 Library 并返回 repo.

    服务端测试库 (文件 DB) 启用了 FK 约束, 直接以 library_id=1 创建 MediaFile 的测试
    需要该归属库存在. 需要时显式请求此 fixture (取代直接用 repo)."""
    if await repo.get_library(1) is None:
        await repo.create_library(name="default", path="/")
    return repo


@pytest_asyncio.fixture
async def stop_worker(app: FastAPI, client: AsyncClient) -> None:
    """停掉后台 worker (lifespan 已由 ``client`` 进入).

    用于直接操纵任务状态 (claim / 改状态) 的测试: 后台 worker 会并发认领排队
    任务并改写状态, 与"任务状态只由测试驱动"的断言竞态. 仅停 worker, 不改项目代码.
    """
    runtime = app.state.runtime
    await runtime.worker.stop()
