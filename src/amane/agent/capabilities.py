"""内置助理与外部派发共用的写操作工具组.

两边装配同一份清单: 分组只在一处登记, 新增或移除工具组不会漏改一侧.
只读探查工具 (``tools.py::build_explore_toolset``) 不在此列, 由调用方单独装配.
"""

from __future__ import annotations

from pydantic_ai.capabilities import Capability

from .actor_ops import build_actor_ops_capability
from .comment_ops import build_comment_ops_capability
from .facet_identity import build_facet_identity_capability
from .feed_ops import build_feed_ops_capability
from .library_ops import build_library_ops_capability
from .metadata_ops import build_metadata_ops_capability
from .saved_query_ops import build_saved_query_ops_capability
from .schedule_ops import build_schedule_ops_capability
from .task_ops import build_task_ops_capability
from .tools import AgentDeps


def build_capabilities() -> list[Capability[AgentDeps]]:
    return [
        build_metadata_ops_capability(),
        build_actor_ops_capability(),
        build_facet_identity_capability(),
        build_comment_ops_capability(),
        build_saved_query_ops_capability(),
        build_library_ops_capability(),
        build_feed_ops_capability(),
        build_schedule_ops_capability(),
        build_task_ops_capability(),
    ]
