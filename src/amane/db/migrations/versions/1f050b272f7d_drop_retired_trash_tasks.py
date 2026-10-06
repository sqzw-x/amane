"""drop retired trash tasks

Revision ID: 1f050b272f7d
Revises: fd5c8ad2c5db
Create Date: 2026-10-06 19:36:43.828043
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1f050b272f7d"
down_revision: str | None = "fd5c8ad2c5db"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """清掉回收任务的历史行: 枚举取值删掉之后, 读取 ``type='trash'`` 会在枚举校验处失败.

    链上的边先删, 免得留下指向已删任务的悬空行 (外键开启的库会拒绝删除被引用的行).
    """
    op.execute(sa.text("DELETE FROM task_links WHERE parent_task_id IN (SELECT id FROM tasks WHERE type = 'trash')"))
    op.execute(sa.text("DELETE FROM task_links WHERE child_task_id IN (SELECT id FROM tasks WHERE type = 'trash')"))
    op.execute(sa.text("DELETE FROM tasks WHERE type = 'trash'"))


def downgrade() -> None:
    """删除的历史行不可恢复, 枚举取值由应用层决定, 这里没有可回退的结构变更."""
