"""metadata locked fields

Revision ID: e4f7dd7d4711
Revises: 0c4de00792e3
Create Date: 2026-10-04 09:42:23.361778
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e4f7dd7d4711"
down_revision: str | None = "0c4de00792e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 直接 ADD COLUMN: batch 重建会反射并重写表达式索引 ix_metadata_number (COLLATE NOCASE), 丢失大小写不敏感唯一.
    op.add_column("metadata", sa.Column("locked_fields", sa.JSON(), nullable=False, server_default=sa.text("'[]'")))


def downgrade() -> None:
    # batch 删列会重建表并丢失表达式索引, 重建后再按原定义恢复.
    with op.batch_alter_table("metadata") as batch_op:
        batch_op.drop_column("locked_fields")
    op.drop_index("ix_metadata_number", table_name="metadata")
    op.create_index("ix_metadata_number", "metadata", [sa.text("number COLLATE NOCASE")], unique=True)
