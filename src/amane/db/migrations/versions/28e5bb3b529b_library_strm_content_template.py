"""library strm content template

Revision ID: 28e5bb3b529b
Revises: 099436e749d6
Create Date: 2026-08-29 19:38:49.980207
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "28e5bb3b529b"
down_revision: str | None = "099436e749d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 存量行留 NULL: 与 link_template 同语义, 即「写视频绝对路径」的原行为.
    with op.batch_alter_table("libraries") as batch_op:
        batch_op.add_column(sa.Column("strm_content_template", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("libraries") as batch_op:
        batch_op.drop_column("strm_content_template")
