"""metadata watermark override

Revision ID: afe769953b2a
Revises: 9054ebacea29
Create Date: 2026-11-01 00:43:49.734024

条目级封面角标覆盖: `watermark_auto` 默认 true 走自动规则; `watermark_kinds` 与 `watermark_definition`
只在手动模式下生效 (档位为空表示跟随这一份文件). 存量行全部按自动规则, 与升级前行为一致, 无需回填.
自动生成的索引 / 枚举改动已剔除 (见 docs/dev/database.md).

直接 ADD COLUMN: `batch_alter_table` 会重建表, 而表达式索引 `ix_metadata_number` (number COLLATE NOCASE)
无法被反射, 重建后大小写不敏感的约束随之丢失.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "afe769953b2a"
down_revision: str | None = "9054ebacea29"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("metadata", sa.Column("watermark_auto", sa.Boolean(), nullable=False, server_default=sa.text("1")))
    op.add_column("metadata", sa.Column("watermark_kinds", sa.JSON(), nullable=False, server_default=sa.text("'[]'")))
    op.add_column("metadata", sa.Column("watermark_definition", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("metadata", "watermark_definition")
    op.drop_column("metadata", "watermark_kinds")
    op.drop_column("metadata", "watermark_auto")
