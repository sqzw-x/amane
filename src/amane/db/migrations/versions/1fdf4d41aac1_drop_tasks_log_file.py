"""drop tasks.log_file

Revision ID: 1fdf4d41aac1
Revises: 136fd7914700
Create Date: 2026-10-15 22:51:47.315553

任务日志路径恒为 ``{log_dir}/tasks/task-{id}/task.log``, 由 id 派生, 不存列.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1fdf4d41aac1"
down_revision: str | None = "136fd7914700"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("tasks") as batch_op:
        batch_op.drop_column("log_file")


def downgrade() -> None:
    with op.batch_alter_table("tasks") as batch_op:
        batch_op.add_column(sa.Column("log_file", sa.String(), nullable=True))
