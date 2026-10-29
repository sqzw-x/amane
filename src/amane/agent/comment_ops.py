"""评论: 挂在单个条目下的用户笔记.

正文按 Markdown 原文存储, 服务端不解析, 因此工具只负责首尾空白与长度.
``updated_at`` 晚于 ``created_at`` 表示正文被编辑过, 由 repository 维护.
"""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.capabilities import Capability

from ..api.models.comments import COMMENT_BODY_MAX
from .tools import TOOL_OK, AgentDeps, require_approval


def _clean_body(body: str) -> str:
    """与 ``CommentBody`` 同一组约束 (先 strip 再判空与上界); 上界取自同一常量."""
    cleaned = body.strip()
    if not cleaned:
        raise ValueError("评论正文不能为空")
    if len(cleaned) > COMMENT_BODY_MAX:
        raise ValueError(f"评论正文最多 {COMMENT_BODY_MAX} 个字符")
    return cleaned


def build_comment_ops_capability() -> Capability[AgentDeps]:
    """删除须批准."""
    cap: Capability[AgentDeps] = Capability(id="comment-ops")

    @cap.tool
    async def create_comment(ctx: RunContext[AgentDeps], metadata_id: int, body: str) -> dict[str, Any]:
        """Add a comment to one metadata row. Body is Markdown; it is not parsed server-side."""
        try:
            cleaned = _clean_body(body)
        except ValueError as exc:
            return {"error": str(exc)}
        comment = await ctx.deps.repo.create_comment(metadata_id, cleaned)
        if comment is None:
            return {"error": f"metadata {metadata_id} 不存在"}
        assert comment.id is not None
        return {"comment_id": comment.id}

    @cap.tool
    async def update_comment(ctx: RunContext[AgentDeps], comment_id: int, body: str) -> str | dict[str, Any]:
        """Replace a comment's body. Ids come from sql_explore on `comments`."""
        try:
            cleaned = _clean_body(body)
        except ValueError as exc:
            return {"error": str(exc)}
        if await ctx.deps.repo.update_comment(comment_id, body=cleaned) is None:
            return {"error": f"评论 {comment_id} 不存在"}
        return TOOL_OK

    @cap.tool
    async def delete_comment(ctx: RunContext[AgentDeps], comment_id: int) -> str | dict[str, Any]:
        """Delete one comment. Not undoable."""
        require_approval(
            ctx,
            sql=f"删除评论 id={comment_id}",
            tool="delete_comment",
            extra={"comment_id": comment_id},
        )
        if not await ctx.deps.repo.delete_comment(comment_id):
            return {"error": f"评论 {comment_id} 不存在"}
        return TOOL_OK

    return cap
