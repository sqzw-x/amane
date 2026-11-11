"""media file video size

Revision ID: 39c66640fbfe
Revises: 56fe451426ef
Create Date: 2026-11-04 01:50:55.904014
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "39c66640fbfe"
down_revision: str | None = "56fe451426ef"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 存量行留空: 迁移内不允许读取磁盘, 画面尺寸由刮削 / 整理补读, 读出前分辨率仍依据文件名.
    op.add_column("media_files", sa.Column("width", sa.Integer(), nullable=True))
    op.add_column("media_files", sa.Column("height", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("media_files") as batch_op:
        batch_op.drop_column("height")
        batch_op.drop_column("width")
