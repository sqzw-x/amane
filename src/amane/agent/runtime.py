from __future__ import annotations

from typing import Any

from pydantic_ai import Agent, DeferredToolRequests
from pydantic_ai.models import Model
from pydantic_ai.settings import ModelSettings, ThinkingLevel
from pydantic_ai.usage import UsageLimits

from ..config import AgentConfig, AgentThinkingMode
from ..llm import build_model as build_llm_model
from .actor_ops import build_actor_ops_capability
from .facet_identity import build_facet_identity_capability
from .feed_ops import build_feed_ops_capability
from .library_ops import build_library_ops_capability
from .metadata_ops import build_metadata_ops_capability
from .schedule_ops import build_schedule_ops_capability
from .schema_docs import build_schema_docs
from .task_ops import build_task_ops_capability
from .tool_names import ToolNameAlias
from .tools import AgentDeps, build_explore_toolset

# 工具 / 输出校验重试: 框架默认 1, 此处拉高避免早停.
_AGENT_RETRIES = 10_000
# 关闭 request / tool_calls / token 等 UsageLimits (框架默认 request_limit=50).
UNLIMITED_USAGE = UsageLimits(request_limit=None)

_SYSTEM = """You are Amane's database exploration and library management assistant.
You help users explore the media metadata SQLite database with read-only SQL, and may
perform carefully scoped domain operations through the write tools.

Rules:
1. Use sql_explore for intermediate investigation.
   - Default: sample rows only (no saved query).
   - For large intermediate sets that you need to page through, set create_view=true; then use
     inspect_result(saved_query_id, offset, limit). Do NOT hand-write LIMIT/OFFSET probes for that
     purpose. Views are just row arrays — no entity or id column required. Explore views are not
     shown as UI browse chips.
2. Use sql_deliver only when the user should browse or reuse the result set in the UI.
   - entity=metadata|actor: the SQL MUST return a column named `id`; the preset deep-links to
     /meta or /actors and can be used as a filter there.
   - Omit entity (or use entity=data): any read-only result; the preset is rendered as a standalone
     data table and cannot be used as a /meta or /actors filter.
3. Use inspect_result to peek at rows of a delivered saved_query or explore view without dumping
   everything into chat.
4. Never attempt INSERT/UPDATE/DELETE/DDL via SQL. Writes only via the write tools.
   Ids always come from sql_explore / sql_deliver results; do not guess one.
   Destructive operations (delete / merge) require user approval: the UI presents the approval
   prompt, so do not ask the user to confirm them in text first.
   A tool that rejects a request returns {"error": ...}: report the reason, and change the call
   instead of repeating it unchanged. A creation whose follow-up fetch failed still returns the new
   id, with `poll_error` carrying the failure: report the fetch as failed, not the creation.
5. The submission payloads of submit_task / create_schedule are not declared in the tool schema:
   call get_task_submission_schema / get_routine_submission_schema first and compose the body
   from the returned schema.
6. Prefer concise Chinese replies unless the user writes in another language.

Database schema:
"""


def parse_session_thinking(raw: Any) -> AgentThinkingMode | None:
    """缺省/非法 → None (继承全局默认)."""
    if raw is None:
        return None
    if isinstance(raw, AgentThinkingMode):
        return raw
    if isinstance(raw, str):
        try:
            return AgentThinkingMode(raw)
        except ValueError:
            return None
    return None


def thinking_to_level(mode: AgentThinkingMode) -> ThinkingLevel:
    match mode:
        case AgentThinkingMode.OFF:
            return False
        case AgentThinkingMode.MINIMAL:
            return "minimal"
        case AgentThinkingMode.LOW:
            return "low"
        case AgentThinkingMode.MEDIUM:
            return "medium"
        case AgentThinkingMode.HIGH:
            return "high"
        case AgentThinkingMode.XHIGH:
            return "xhigh"


def resolve_model_settings(config: AgentConfig, *, session_thinking: AgentThinkingMode | None = None) -> ModelSettings:
    """始终注入 ``config.max_tokens`` (避免提供商默认过小导致 length 截断). ``session_thinking`` 为 None 表示继承 ``config.thinking``; 有效值仍为 None 时不传 thinking."""
    settings: ModelSettings = {"max_tokens": config.max_tokens}
    effective = config.thinking if session_thinking is None else session_thinking
    if effective is not None:
        settings["thinking"] = thinking_to_level(effective)
    return settings


def build_model(config: AgentConfig) -> Model:
    """不检查 api_key. 不传 ``http_client``: 传输客户端由 pydantic-ai 构造并托管, 与翻译共用协议映射."""
    return build_llm_model(config.api_type, base_url=config.base_url, api_key=config.api_key, model=config.model)


def build_agent(config: AgentConfig) -> Agent[AgentDeps, str | DeferredToolRequests] | None:
    """thinking / max_tokens 经每回合 model_settings 注入."""
    if not config.api_key:
        return None
    return Agent[AgentDeps, str | DeferredToolRequests](
        build_model(config),
        deps_type=AgentDeps,
        output_type=[str, DeferredToolRequests],
        # 走 instructions 而非 system_prompt: Responses 协议把 agent instructions 放 API 顶层
        # instructions 字段, 服务端将其插在 input 之前; system_prompt 会变成 input 里的 system
        # 消息, 落到 capability 指令之后 —— 身份定位出现在各域注意事项之后.
        instructions=_SYSTEM + build_schema_docs(),
        retries=_AGENT_RETRIES,
        toolsets=[build_explore_toolset()],
        capabilities=[
            ToolNameAlias(),
            build_metadata_ops_capability(),
            build_actor_ops_capability(),
            build_facet_identity_capability(),
            build_library_ops_capability(),
            build_feed_ops_capability(),
            build_schedule_ops_capability(),
            build_task_ops_capability(),
        ],
    )
