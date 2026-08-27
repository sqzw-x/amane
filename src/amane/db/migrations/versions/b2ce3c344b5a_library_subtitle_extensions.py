"""library subtitle extensions

Revision ID: b2ce3c344b5a
Revises: cbede59bbb9e
Create Date: 2026-08-27 16:53:11.079990
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b2ce3c344b5a"
down_revision: str | None = "cbede59bbb9e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("libraries") as batch_op:
        batch_op.add_column(
            sa.Column(
                "subtitle_extensions",
                sa.JSON(),
                nullable=False,
                server_default='[".srt", ".ass", ".ssa", ".vtt", ".sub"]',
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("libraries") as batch_op:
        batch_op.drop_column("subtitle_extensions")
