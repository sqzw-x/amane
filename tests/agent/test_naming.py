"""会话标题生成: 只依据首条输入, 失败回退.

模型经 ``FunctionModel`` 或替换的 ``model_request`` 驱动, 不触网. 覆盖:
- 清洗 (取首行 / 去引号标点 / 截断) 与回退截断
- 未配置模型、请求失败、超时 三条回退路径
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

import pytest
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from amane.agent import naming as naming_module
from amane.agent.naming import (
    DEFAULT_TITLE,
    FALLBACK_MAX_CHARS,
    TITLE_MAX_CHARS,
    clean_title,
    fallback_title,
    generate_title,
)


def _text_model(result: str) -> FunctionModel:
    def respond(_messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart(result)])

    return FunctionModel(respond)


def _prompts(messages: Sequence[ModelMessage]) -> tuple[str, str]:
    """取出最后一次请求的 system 与 user 文本."""
    request = messages[-1]
    assert isinstance(request, ModelRequest)
    system = "".join(part.content for part in request.parts if isinstance(part, SystemPromptPart))
    user = "".join(
        part.content for part in request.parts if isinstance(part, UserPromptPart) and isinstance(part.content, str)
    )
    return system, user


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, ""),
        ("", ""),
        ("   ", ""),
        ("\n\n", ""),
        ("影片筛选", "影片筛选"),
        ("  影片筛选  ", "影片筛选"),
        ("「影片筛选」。", "影片筛选"),
        ('"Top rated movies"', "Top rated movies"),
        ("无标点约束,", "无标点约束"),
        ("第一行标题\n第二行", "第一行标题"),
        ("\n  \n续行", "续行"),
        ("x" * 100, "x" * TITLE_MAX_CHARS),
    ],
)
def test_clean_title(raw: str | None, expected: str) -> None:
    assert clean_title(raw) == expected


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("帮我查 4K 影片", "帮我查 4K 影片"),
        ("  多行\n输入  ", "多行 输入"),
        ("x" * FALLBACK_MAX_CHARS, "x" * FALLBACK_MAX_CHARS),
        ("x" * (FALLBACK_MAX_CHARS + 1), "x" * FALLBACK_MAX_CHARS + "…"),
        ("", DEFAULT_TITLE),
        ("   ", DEFAULT_TITLE),
    ],
)
def test_fallback_title(prompt: str, expected: str) -> None:
    assert fallback_title(prompt) == expected


@pytest.mark.asyncio
async def test_generate_title_passes_first_prompt_only() -> None:
    """请求只带首条输入, 输出格式由 system 约束."""
    calls: list[tuple[str, str]] = []

    def respond(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        calls.append(_prompts(messages))
        return ModelResponse(parts=[TextPart("「片库筛选」")])

    assert await generate_title(FunctionModel(respond), "帮我找出全部 4K 影片") == "片库筛选"
    system, user = calls[0]
    assert user == "帮我找出全部 4K 影片"
    assert str(TITLE_MAX_CHARS) in system


@pytest.mark.asyncio
async def test_generate_title_without_model_falls_back() -> None:
    assert await generate_title(None, "找出全部 4K 影片") == "找出全部 4K 影片"


@pytest.mark.asyncio
async def test_generate_title_blank_result_falls_back() -> None:
    assert await generate_title(_text_model("  \n "), "找出全部 4K 影片") == "找出全部 4K 影片"


@pytest.mark.asyncio
async def test_generate_title_request_failure_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(*_args: object, **_kwargs: object) -> ModelResponse:
        raise RuntimeError("upstream down")

    monkeypatch.setattr(naming_module, "model_request", boom)
    assert await generate_title(_text_model("x"), "找出全部 4K 影片") == "找出全部 4K 影片"


@pytest.mark.asyncio
async def test_generate_title_timeout_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """标题迟到即失去意义: 超时后回退, 不让请求悬挂."""

    async def hang(*_args: object, **_kwargs: object) -> ModelResponse:
        await asyncio.sleep(5)
        return ModelResponse(parts=[TextPart("x")])

    monkeypatch.setattr(naming_module, "TIMEOUT_S", 0.01)
    monkeypatch.setattr(naming_module, "model_request", hang)
    assert await generate_title(_text_model("x"), "找出全部 4K 影片") == "找出全部 4K 影片"
