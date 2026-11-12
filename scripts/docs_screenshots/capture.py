"""生成 docs/assets/screenshots/ 下的界面截图.

一条命令完成: 在临时目录写入合成数据集, 用该目录启动后端 (只监听 127.0.0.1, 关闭鉴权),
以 Playwright (Chromium, zh-CN) 分别按浅色与深色主题截图并转为 WebP (深色文件名带 -dark 后缀), 最后停服务并删除临时目录.
不读写 ./data, 不占用 8000 端口.

    cd web && pnpm run build && cd ..   # 后端直出 web/dist, 截图前先构建
    uv run python -m scripts.docs_screenshots.capture

依赖 Playwright 的 Chromium: 首次使用先 `uv run playwright install chromium`.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from PIL import Image
from playwright.async_api import Browser, FloatRect, Page, async_playwright

from scripts.docs_screenshots.seed import SeedResult, find_font, seed

logger = logging.getLogger("docs_screenshots.capture")

REPO_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = REPO_ROOT / "docs" / "assets" / "screenshots"
DEFAULT_PORT = 18765
DESKTOP = (1440, 900)
MOBILE = (390, 844)
DEVICE_SCALE = 2
MAX_BYTES = 400 * 1024
#: 主题与输出文件名后缀; 文档站以 #only-light / #only-dark 按站点主题切换两套图.
THEMES = {"light": "", "dark": "-dark"}


def init_script(theme: str) -> str:
    """前端 UI store (zustand persist) 与 i18n 的本地存储键; 见 web/src/stores/ui.ts 与 web/src/i18n/index.ts."""
    return (
        f"localStorage.setItem('amane-web', JSON.stringify({{state: {{theme: '{theme}', language: 'zh-CN'}}, version: 0}}));"
        "localStorage.setItem('amane-web-language', 'zh-CN');"
    )


type Action = Callable[[Page], Awaitable[None]]


@dataclass(frozen=True)
class Shot:
    name: str
    path: str
    action: Action | None = None
    viewport: tuple[int, int] = DESKTOP
    #: 截到该文本所在区块的上沿为止; 用于裁掉不宜入镜的区域.
    clip_before: str | None = None


async def _click_button(page: Page, name: str) -> None:
    await page.get_by_role("button", name=name, exact=True).first.click()
    await page.wait_for_timeout(600)


async def _open_filter(page: Page) -> None:
    await _click_button(page, "高级搜索")


async def _open_compare(page: Page) -> None:
    await _click_button(page, "对比来源")


async def _open_submit(page: Page) -> None:
    await _click_button(page, "提交任务")


async def _open_template(page: Page) -> None:
    await _click_button(page, "配置媒体库")
    # 弹窗内滚到路径模板一段, 让模板输入框与占位符徽章同屏.
    await page.get_by_text("视频路径模板", exact=True).scroll_into_view_if_needed()
    await page.get_by_role("dialog").get_by_text("可用占位符").last.scroll_into_view_if_needed()
    await page.wait_for_timeout(400)


async def _open_crop(page: Page) -> None:
    await _click_button(page, "裁切海报")


async def _open_edit(page: Page) -> None:
    await _click_button(page, "编辑")


async def _scroll_comments(page: Page) -> None:
    # 评论区块顶到视口上沿, 让整张 GFM 评论卡片同屏.
    await page.get_by_role("heading", name="评论", exact=True).evaluate(
        "el => { el.scrollIntoView({block: 'start'}); window.scrollBy(0, -96); }"
    )
    await page.wait_for_timeout(600)


async def _expand_tool_calls(page: Page) -> None:
    # 先展开工具组, 再展开其中的 sql_explore, 让 SQL 与结果同屏.
    await page.get_by_text("调用了 1 个工具").first.click()
    await page.get_by_role("button", name="sql_explore").first.click()
    await page.wait_for_timeout(600)


def _emit_logs(ids: SeedResult) -> Action:
    """日志页只显示打开后经 WebSocket 推送的条目; 发几次不改变数据的更新请求, 让后端写出真实日志."""

    async def action(page: Page) -> None:
        api, base = page.request, page.url.removesuffix("/logs") + "/api"
        await api.post(f"{base}/facets/user_tag", data={"names": ["用户标签 1"]})
        await api.patch(f"{base}/libraries/{ids.library_id}", data={"name": "媒体库 1"})
        for schedule_id, name in ((1, "定时任务 1"), (2, "定时任务 2")):
            await api.patch(f"{base}/schedules/{schedule_id}", data={"name": name})
        await page.wait_for_timeout(800)

    return action


def build_shots(ids: SeedResult) -> list[Shot]:
    movie = f"/meta/{ids.metadata_ids['ABC-005']}"
    library = f"/libraries/{ids.library_id}"
    return [
        Shot("meta-wall", "/meta"),
        Shot("meta-list", "/meta?view=list"),
        Shot("meta-filter", "/meta", _open_filter),
        Shot("movie-detail", movie),
        Shot("compare-sources", movie, _open_compare),
        Shot("poster-crop", movie, _open_crop),
        Shot("movie-edit", movie, _open_edit),
        # 评论卡片较长: 加高视口让整张卡片同屏, 截到评论输入框上方为止.
        Shot("comments", movie, _scroll_comments, viewport=(1440, 1500), clip_before="撰写"),
        Shot("actor-wall", "/actors"),
        Shot("actor", f"/actors/{ids.actor_ids['演员 A']}"),
        Shot("categories", "/catalog"),
        Shot("tasks", "/tasks"),
        Shot("submit-task", "/tasks", _open_submit),
        Shot("library-list", "/libraries"),
        Shot("library-detail", library),
        Shot("feeds", "/feeds"),
        Shot("feed-sources", "/feeds/sources"),
        Shot("saved-queries", "/saved-queries"),
        # 展开的工具参数与结果较高, 加高视口让最终回复同屏.
        Shot("agent", "/", _expand_tool_calls, viewport=(1440, 1200)),
        Shot("schedules", "/schedules"),
        Shot("logs", "/logs", _emit_logs(ids)),
        # 类型路由与字段优先级列出内置刮削站点名, 截图止于其上方.
        Shot("settings-scrape", "/settings?section=scraping", clip_before="类型路由"),
        Shot("organize-template", library, _open_template),
        Shot("mobile-wall", "/meta", viewport=MOBILE),
    ]


def save_webp(png: bytes, target: Path) -> int:
    """按质量递减编码, 直到不超过 MAX_BYTES; 返回最终字节数."""
    image = Image.open(io.BytesIO(png)).convert("RGB")
    for quality in (82, 76, 70, 62, 54, 46):
        buffer = io.BytesIO()
        image.save(buffer, format="WEBP", quality=quality, method=6)
        if buffer.tell() <= MAX_BYTES:
            break
    target.write_bytes(buffer.getvalue())
    return buffer.tell()


async def capture_shot(browser: Browser, base_url: str, shot: Shot, theme: str, out_dir: Path) -> None:
    width, height = shot.viewport
    context = await browser.new_context(
        viewport={"width": width, "height": height},
        device_scale_factor=DEVICE_SCALE,
        locale="zh-CN",
        timezone_id="Asia/Shanghai",
        color_scheme="dark" if theme == "dark" else "light",
        is_mobile=shot.viewport == MOBILE,
        has_touch=shot.viewport == MOBILE,
    )
    try:
        await context.add_init_script(init_script(theme))
        page = await context.new_page()
        await page.goto(base_url + shot.path)
        await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(800)
        if shot.action is not None:
            await shot.action(page)
            await page.wait_for_load_state("networkidle")
        # 移开鼠标, 避免悬停态与提示气泡进入画面.
        await page.mouse.move(0, height - 1)
        await page.wait_for_timeout(500)
        clip: FloatRect | None = None
        if shot.clip_before is not None:
            box = await page.get_by_text(shot.clip_before, exact=True).first.bounding_box()
            if box is None:
                raise RuntimeError(f"{shot.name}: 未找到裁切锚点 {shot.clip_before}")
            clip = FloatRect(x=0, y=0, width=width, height=box["y"] - 12)
        name = f"{shot.name}{THEMES[theme]}"
        size = save_webp(await page.screenshot(clip=clip), out_dir / f"{name}.webp")
        logger.info("%-23s %4d KB", name, size // 1024)
    finally:
        await context.close()


async def capture_all(base_url: str, shots: list[Shot], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            for theme in THEMES:
                for shot in shots:
                    await capture_shot(browser, base_url, shot, theme, out_dir)
        finally:
            await browser.close()


def start_server(data_dir: Path, media_dir: Path, port: int) -> subprocess.Popen[bytes]:
    env = {
        **os.environ,
        "AMANE_DATA_DIR": str(data_dir),
        "AMANE_LOG_DIR": str(data_dir / "logs"),
        "AMANE_SAFE_DIRS": str(media_dir),
        "AMANE_TOKEN": "off",
        "AMANE_HOST": "127.0.0.1",
        "AMANE_PORT": str(port),
    }
    log = (data_dir / "server.log").open("wb")
    return subprocess.Popen(
        [sys.executable, "-m", "amane.server"], cwd=REPO_ROOT, env=env, stdout=log, stderr=subprocess.STDOUT
    )


def wait_ready(base_url: str, server: subprocess.Popen[bytes], timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if server.poll() is not None:
            raise SystemExit(f"服务提前退出, 退出码 {server.returncode}")
        try:
            with urllib.request.urlopen(f"{base_url}/api/metadata?limit=1", timeout=2):
                return
        except urllib.error.URLError, ConnectionError:
            time.sleep(0.5)
    raise SystemExit("等待服务就绪超时")


def stop_server(server: subprocess.Popen[bytes]) -> None:
    server.terminate()
    try:
        server.wait(timeout=15)
    except subprocess.TimeoutExpired:
        server.kill()
        server.wait()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--out", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--only", nargs="*", default=None, help="只截指定名称")
    parser.add_argument("--keep", action="store_true", help="保留临时数据目录, 便于排查")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if not (REPO_ROOT / "web" / "dist" / "index.html").exists():
        raise SystemExit("缺少 web/dist, 先在 web/ 下执行 pnpm run build")

    root = Path(tempfile.mkdtemp(prefix="amane-shots-"))
    data_dir, media_dir = root / "data", root / "media"
    base_url = f"http://127.0.0.1:{args.port}"
    server: subprocess.Popen[bytes] | None = None
    try:
        # 只供 AMANE_SAFE_DIRS 收窄文件浏览范围; 种子数据的文件路径是不存在的 /media.
        media_dir.mkdir(parents=True)
        ids = asyncio.run(seed(data_dir, find_font()))
        server = start_server(data_dir, media_dir, args.port)
        wait_ready(base_url, server)
        shots = build_shots(ids)
        if args.only:
            shots = [s for s in shots if s.name in args.only]
        asyncio.run(capture_all(base_url, shots, args.out))
    finally:
        if server is not None:
            stop_server(server)
        if args.keep:
            logger.info("临时目录保留于 %s", root)
        else:
            shutil.rmtree(root, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
