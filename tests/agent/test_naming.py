"""会话标题生成: 只依据首条输入, 失败回退.

模型经 ``FunctionModel`` 或替换的 ``model_request`` 驱动, 不触网. 覆盖:
- 清洗 (取首行 / 去引号标点 / 截断) 与回退截断
- 未配置模型、结果为空、请求失败、超时四条回退路径
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence

import pytest
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    UserPromptPart,
)
from pydantic_ai.models import Model
from pydantic_ai.models.function import AgentInfo, FunctionModel

from amane.agent import naming as naming_module
from amane.agent.naming import (
    FALLBACK_MAX_CHARS,
    TITLE_MAX_CHARS,
    clean_title,
    fallback_title,
    generate_title,
)
from amane.db.models import DEFAULT_SESSION_TITLE

_PROMPT = "找出全部 4K 影片"
"""回退用例共用的首条输入: 长度在 ``FALLBACK_MAX_CHARS`` 之内, 回退即原文."""

_RequestCall = Callable[..., Awaitable[ModelResponse]]


def _text_model(result: str) -> FunctionModel:
    def respond(_messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart(result)])

    return FunctionModel(respond)


async def _boom(*_args: object, **_kwargs: object) -> ModelResponse:
    raise RuntimeError("upstream down")


async def _hang(*_args: object, **_kwargs: object) -> ModelResponse:
    await asyncio.sleep(5)
    return ModelResponse(parts=[TextPart("x")])


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
        ("", DEFAULT_SESSION_TITLE),
        ("   ", DEFAULT_SESSION_TITLE),
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


@pytest.mark.parametrize(
    ("model", "request_fn", "timeout_s"),
    [
        (None, None, None),
        (_text_model("  \n "), None, None),
        (_text_model("x"), _boom, None),
        (_text_model("x"), _hang, 0.01),
    ],
    ids=["未配置模型", "结果为空", "请求失败", "超时"],
)
@pytest.mark.asyncio
async def test_generate_title_falls_back(
    model: Model | None,
    request_fn: _RequestCall | None,
    timeout_s: float | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """四条失败路径都回退首条输入截断; 其中超时不悬挂, 请求失败不上抛."""
    if request_fn is not None:
        monkeypatch.setattr(naming_module, "model_request", request_fn)
    if timeout_s is not None:
        monkeypatch.setattr(naming_module, "TIMEOUT_S", timeout_s)
    assert await generate_title(model, _PROMPT) == _PROMPT
