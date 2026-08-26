"""Agent 运行时装配: 模型工厂 / 思考设置 / 系统提示.

包含上游模型工厂、会话思考覆盖解析与 Agent 工厂 (toolsets + capabilities).
"""

from __future__ import annotations

from typing import Any

from pydantic_ai import Agent, DeferredToolRequests
from pydantic_ai.models import Model
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.settings import ModelSettings, ThinkingLevel
from pydantic_ai.usage import UsageLimits

from amane.config import AgentApiType, AgentConfig, AgentThinkingMode

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

# 单次回复 / 思考预算: 避免撞上提供商默认 max_tokens (过小会 length 截断且无正文).
_AGENT_MAX_TOKENS = 128_000
# 工具 / 输出校验重试: 框架默认 1, 此处拉高避免早停.
_AGENT_RETRIES = 10_000
# 关闭 request / tool_calls / token 等 UsageLimits (框架默认 request_limit=50).
UNLIMITED_USAGE = UsageLimits(request_limit=None)

_SYSTEM = """You are Amane's database exploration and library management assistant.
You help users explore the media metadata SQLite database with read-only SQL, and may
load write capabilities for carefully scoped domain operations.

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
4. Never attempt INSERT/UPDATE/DELETE/DDL via SQL. Writes only via loaded capabilities:
   - metadata-ops: metadata fields, user tags, merge, scrape enqueue, delete
   - actor-ops: actor person fields, alias rows (list/resolve/add/remove), display-name switch,
     actor scrape enqueue
   - facet-identity: rename / merge / delete facets and scrape-side rules
   - library-ops: library CRUD and refresh/scan enqueue
   - feed-ops: RSS/Atom feed sources and feed item history
   - schedule-ops: routine schedule CRUD and trigger
   - task-ops: unified submit / cancel / retry
   Call load_capability('<id>') before using that domain's tools. Destructive ops need user approval.
   Feed polling may enqueue SCRAPE tasks but does not run scraping inline.
   Schedule triggering only makes the schedule due; CronScheduler creates the task on its next tick.
5. Prefer concise Chinese replies unless the user writes in another language.

Database schema:
"""


def parse_session_thinking(raw: Any) -> AgentThinkingMode | None:
    """从会话 meta 解析思考覆盖; 缺省/非法 → None (继承全局默认)."""
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
    """组装本回合 ModelSettings (始终带高 max_tokens).

    ``session_thinking`` 为 None 表示继承 ``config.thinking``;
    有效值仍为 None 时不传 thinking (跟模型默认).
    """
    settings: ModelSettings = {"max_tokens": _AGENT_MAX_TOKENS}
    effective = config.thinking if session_thinking is None else session_thinking
    if effective is not None:
        settings["thinking"] = thinking_to_level(effective)
    return settings


def build_model(config: AgentConfig) -> Model:
    """按 ``api_type`` 组装上游模型 (不检查 api_key)."""
    match config.api_type:
        case AgentApiType.CHAT:
            return OpenAIChatModel(
                config.model, provider=OpenAIProvider(base_url=config.base_url, api_key=config.api_key)
            )
        case AgentApiType.RESPONSE:
            return OpenAIResponsesModel(
                config.model, provider=OpenAIProvider(base_url=config.base_url, api_key=config.api_key)
            )
        case AgentApiType.ANTHROPIC:
            return AnthropicModel(
                config.model, provider=AnthropicProvider(api_key=config.api_key, base_url=config.base_url)
            )


def build_agent(config: AgentConfig) -> Agent[AgentDeps, str | DeferredToolRequests] | None:
    """thinking / max_tokens 在每回合经 model_settings 注入."""
    if not config.api_key:
        return None
    return Agent[AgentDeps, str | DeferredToolRequests](
        build_model(config),
        deps_type=AgentDeps,
        output_type=[str, DeferredToolRequests],
        system_prompt=_SYSTEM + build_schema_docs(),
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
