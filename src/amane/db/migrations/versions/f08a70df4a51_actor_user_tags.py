"""actor user tags

Revision ID: f08a70df4a51
Revises: 022f8eb0d22f
Create Date: 2026-09-26 02:46:55.392335
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f08a70df4a51"
down_revision: str | None = "022f8eb0d22f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # SQLite DDL 失败会留下半成品表, 用 inspect 保持幂等以便重跑.
    if "actor_user_tags" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "actor_user_tags",
        sa.Column("actor_id", sa.Integer(), nullable=False),
        sa.Column("user_tag_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["actor_id"], ["actors.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_tag_id"], ["user_tags.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("actor_id", "user_tag_id"),
    )


def downgrade() -> None:
    if "actor_user_tags" not in sa.inspect(op.get_bind()).get_table_names():
        return
    op.drop_table("actor_user_tags")
