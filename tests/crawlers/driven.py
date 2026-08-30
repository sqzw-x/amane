"""TOML 数据驱动测试的共用发现 / mock / 断言."""

from __future__ import annotations

import json
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from amane.crawlers.http import HttpClient
from amane.crawlers.registry import registry
from amane.net.errors import RequestError

CASES_DIR = Path(__file__).parent / "cases"


def _case_id(toml_file: Path) -> str:
    return toml_file.relative_to(CASES_DIR).with_suffix("").as_posix()


def _toml_site(toml_file: Path) -> str | None:
    site = tomllib.loads(toml_file.read_text(encoding="utf-8")).get("site")
    return site if isinstance(site, str) else None


def discover_film_cases(is_site: Callable[[str], bool]) -> list[tuple[str, Path]]:
    """站点根目录的 TOML; 忽略 ``{site}/actor/`` (留给演员 runner)."""
    cases: list[tuple[str, Path]] = []
    if not CASES_DIR.exists():
        return cases
    for toml_file in sorted(CASES_DIR.rglob("*.toml")):
        if "actor" in toml_file.relative_to(CASES_DIR).parts:
            continue
        site = _toml_site(toml_file)
        if site is None or not is_site(site):
            continue
        cases.append((_case_id(toml_file), toml_file))
    return cases


def discover_actor_cases(is_site: Callable[[str], bool]) -> list[tuple[str, Path]]:
    """纯演员站读 ``{site}/``; 双料站只读 ``{site}/actor/``.

    双料 = 同名也在影片 registry. 没有 actor/ 时不能回退到根, 否则影片 TOML 会被演员 runner 吃掉.
    """
    cases: list[tuple[str, Path]] = []
    if not CASES_DIR.exists():
        return cases
    for site_dir in sorted(p for p in CASES_DIR.iterdir() if p.is_dir() and not p.name.startswith(".")):
        if registry.get(site_dir.name) is not None:
            scoped = site_dir / "actor"
            if not scoped.is_dir():
                continue
        else:
            scoped = site_dir
        for toml_file in sorted(scoped.rglob("*.toml")):
            site = _toml_site(toml_file)
            if site is None or not is_site(site):
                continue
            cases.append((_case_id(toml_file), toml_file))
    return cases


def load_toml(path: Path) -> dict[str, Any]:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def load_response_file(case_dir: Path, filename: str) -> str | dict[str, Any]:
    """加载响应文件 - .json 解析为 dict, 其他返回 str. 文件不存在则 skip."""
    path = case_dir / filename
    if not path.is_file():
        pytest.skip("no test cases found")
    text = path.read_text(encoding="utf-8")
    if filename.endswith(".json"):
        data: dict[str, Any] = json.loads(text)
        return data
    return text


def build_mock(mock_web: AsyncMock, case_dir: Path, responses: list[dict[str, Any]]) -> None:
    """配置 mock_web 的 side_effects, 按 URL 模式路由."""
    get_text_map: dict[str, str | dict[str, Any]] = {}
    get_json_map: dict[str, str | dict[str, Any]] = {}
    post_json_routes: list[tuple[str, str | None, str | dict[str, Any]]] = []

    for resp in responses:
        data = load_response_file(case_dir, resp["file"])
        pattern = resp["url_contains"]
        method = resp.get("method", "get_text")
        if method == "post_json":
            body = resp.get("body_contains")
            post_json_routes.append((pattern, body if isinstance(body, str) else None, data))
        elif method == "get_json":
            get_json_map[pattern] = data
        else:
            get_text_map[pattern] = data

    if get_text_map:

        async def get_text_side_effect(url: str, **kwargs: object) -> str:
            for pattern, html in get_text_map.items():
                if pattern in url:
                    return str(html)
            return "<html></html>"

        mock_web.get_text.side_effect = get_text_side_effect

    if get_json_map:

        async def get_json_side_effect(url: str, **kwargs: object) -> object:
            for pattern, payload in get_json_map.items():
                if pattern in url:
                    return payload
            raise RequestError(url, "no mock matched")

        mock_web.get_json.side_effect = get_json_side_effect

    if post_json_routes:

        async def post_json_side_effect(url: str, **kwargs: object) -> object:
            raw = kwargs.get("json")
            blob = json.dumps(raw, ensure_ascii=False) if isinstance(raw, (dict, list)) else str(raw or "")
            for pattern, needle, payload in post_json_routes:
                if pattern not in url:
                    continue
                if needle and needle not in blob:
                    continue
                return payload
            raise RequestError(url, "no mock matched")

        mock_web.post_json.side_effect = post_json_side_effect


def http_client(mock_web: AsyncMock) -> HttpClient:
    return HttpClient(web=mock_web, browser=None)


def assert_expected(result: object, expected: dict[str, Any]) -> None:
    """将 expected 字典中的断言规则应用于 result 对象."""
    for key, value in expected.items():
        if key.endswith("_contains"):
            field = key.removesuffix("_contains")
            actual = getattr(result, field)
            if isinstance(value, list):
                for item in value:
                    if isinstance(actual, str):
                        assert item in actual, f"{field}: expected {item!r} in {actual!r}"
                    else:
                        assert any(item in elem for elem in actual), (
                            f"{field}: expected {item!r} in any element of {actual!r}"
                        )
            elif isinstance(actual, str):
                assert value in actual, f"{field}: expected {value!r} in {actual!r}"
            else:
                assert any(value in elem for elem in actual), (
                    f"{field}: expected {value!r} in any element of {actual!r}"
                )
        elif key.endswith("_count"):
            field = key.removesuffix("_count")
            actual = getattr(result, field)
            assert len(actual) == value, f"{field}: expected len={value}, got len={len(actual)}"
        elif key.endswith("_not_empty"):
            field = key.removesuffix("_not_empty")
            actual = getattr(result, field)
            assert actual, f"{field}: expected non-empty, got {actual!r}"
        elif key.endswith("_is_none"):
            field = key.removesuffix("_is_none")
            actual = getattr(result, field)
            assert actual is None, f"{field}: expected None, got {actual!r}"
        else:
            actual = getattr(result, key)
            assert actual == value, f"{key}: expected {value!r}, got {actual!r}"
