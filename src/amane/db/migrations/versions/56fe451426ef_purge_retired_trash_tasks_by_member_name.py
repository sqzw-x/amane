"""purge retired trash tasks

Revision ID: 56fe451426ef
Revises: 6940e4cf8a80
Create Date: 2026-11-05 17:31:20.432447
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "56fe451426ef"
down_revision: str | None = "6940e4cf8a80"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """清掉枚举退役后读不出来的回收任务行: 枚举列落库的是成员名, 条件按 ``TRASH`` 写.

    链上的边先删, 免得留下指向已删任务的悬空行 (外键开启的库会拒绝删除被引用的行).
    """
    op.execute(sa.text("DELETE FROM task_links WHERE parent_task_id IN (SELECT id FROM tasks WHERE type = 'TRASH')"))
    op.execute(sa.text("DELETE FROM task_links WHERE child_task_id IN (SELECT id FROM tasks WHERE type = 'TRASH')"))
    op.execute(sa.text("DELETE FROM tasks WHERE type = 'TRASH'"))


def downgrade() -> None:
    """删除的历史行不可恢复, 枚举取值由应用层决定, 这里没有可回退的结构变更."""
