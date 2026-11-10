"""SPA 回退的路径校验: Windows 盘符 / UNC / NUL 在触盘前拒绝, 遍历仍返回 400.

Windows 实机上的 SMB 外连无法在本机复现, 这里只覆盖判定函数的词法结果与 ASGI 行为;
``is_file()`` 是否真的发起 SMB 连接、静态文件读取是否抛 404 未覆盖.
"""

from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx2 import ASGITransport, AsyncClient

from amane.api.spa import _is_safe_dist_path, mount_spa

_INDEX_HTML = "<!doctype html><div id=root></div>"


@pytest.mark.parametrize(
    ("path", "expected", "desc"),
    [
        ("/", True, "根路径"),
        ("/index.html", True, "入口文档"),
        ("/assets/app-abc123.js", True, "Vite 带 hash 产物"),
        ("/meta/42", True, "前端路由"),
        ("/条目/详情", True, "unicode"),
        ("/a//b", True, "重复斜杠"),
        ("", False, "空路径"),
        ("index.html", False, "缺少前导斜杠"),
        ("/../secret", False, "父目录遍历"),
        ("/meta/../secret", False, "路径中段遍历"),
        ("/foo..bar", False, "含 .. 的文件名沿用原规则"),
        ("/C:/Windows/win.ini", False, "Windows 盘符"),
        ("/c:Windows/win.ini", False, "盘符相对路径"),
        ("\\\\attacker\\share\\x", False, "UNC 无前导斜杠"),
        ("/\\\\attacker\\share\\x", False, "UNC 带前导斜杠"),
        ("/index\x00.html", False, "NUL"),
    ],
)
def test_is_safe_dist_path(path: str, expected: bool, desc: str):
    assert _is_safe_dist_path(path) is expected, desc


@pytest.mark.asyncio(loop_scope="function")
async def test_spa_fallback_over_asgi(tmp_path: Path):
    """不安全路径经 ASGI 仍 400; 未知前端路由回退 index.html; GET / HEAD 与产物不受影响."""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(_INDEX_HTML, encoding="utf-8")
    (dist / "assets" / "app-abc123.js").write_text("console.log(1)", encoding="utf-8")

    app = FastAPI()
    mount_spa(app, dist)

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 百分号编码由 httpx 解码后进入 ASGI scope, 与真实服务器看到的一致.
        for raw_path in (
            "/%2e%2e/secret",
            "/C:/Windows/win.ini",
            "/%5C%5Cattacker%5Cshare%5Cx",
            "/index%00.html",
        ):
            response = await client.get(raw_path)
            assert response.status_code == 400, raw_path

        fallback = await client.get("/meta/42")
        assert fallback.status_code == 200
        assert fallback.text == _INDEX_HTML

        head = await client.head("/meta/42")
        assert head.status_code == 200
        assert head.content == b""

        asset = await client.get("/assets/app-abc123.js")
        assert asset.status_code == 200
        assert asset.text == "console.log(1)"
