"""会话标题生成: 只依据首条用户输入, 与主 Agent 回合并行执行.

标题只供列表识别, 不进模型上下文. 未配置模型、请求失败、超时或结果为空时回退首条输入截断.
"""

from __future__ import annotations

import asyncio

import structlog
from pydantic_ai.direct import model_request
from pydantic_ai.messages import ModelMessage, ModelRequest, SystemPromptPart, UserPromptPart
from pydantic_ai.models import Model
from pydantic_ai.settings import ModelSettings

logger = structlog.get_logger()

TITLE_MAX_CHARS = 30
"""模型返回标题的字符上限."""

FALLBACK_MAX_CHARS = 16
"""回退标题的字符上限."""

TIMEOUT_S = 15.0
"""标题请求超时 (秒). 标题迟到即失去意义, 超时后回退."""

MAX_TOKENS = 1024
"""输出预算. 部分提供商的模型先写思维链再输出正文, 预算过小时正文为空 (回退截断)."""

DEFAULT_TITLE = "新会话"
"""与 ``AgentSession.title`` 的建表默认一致."""

_SYSTEM = (
    "你是会话标题生成器. 依据用户的第一条提问概括会话主题, "
    f"不超过 {TITLE_MAX_CHARS} 个字符, 不加引号与句末标点, 只输出标题本身."
)

_EDGE_CHARS = "\"'“”‘’「」『』《》。.．!！?？,，、;；:："
"""正文两端常见的修饰: 引号与句末标点."""


async def generate_title(model: Model | None, prompt: str) -> str:
    """生成标题; 任何失败均回退 ``fallback_title``, 不抛异常."""
    fallback = fallback_title(prompt)
    if model is None:
        return fallback
    messages: list[ModelMessage] = [
        ModelRequest(parts=[SystemPromptPart(content=_SYSTEM), UserPromptPart(content=prompt)])
    ]
    settings: ModelSettings = {"max_tokens": MAX_TOKENS}
    try:
        async with asyncio.timeout(TIMEOUT_S):
            response = await model_request(model, messages, model_settings=settings)
    except Exception as exc:
        logger.warning("session title request failed", error=str(exc))
        return fallback
    return clean_title(response.text) or fallback


def clean_title(raw: str | None) -> str:
    """取首个非空行, 去两端引号与标点, 压到上限; 无内容返回空串."""
    for line in (raw or "").splitlines():
        title = line.strip().strip(_EDGE_CHARS).strip()
        if title:
            return title[:TITLE_MAX_CHARS]
    return ""


def fallback_title(prompt: str) -> str:
    """首条输入压缩空白后截断."""
    text = " ".join(prompt.split())
    if not text:
        return DEFAULT_TITLE
    return text if len(text) <= FALLBACK_MAX_CHARS else f"{text[:FALLBACK_MAX_CHARS]}…"
