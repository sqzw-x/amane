"""actor locked fields

Revision ID: fd5c8ad2c5db
Revises: e4f7dd7d4711
Create Date: 2026-10-04 15:31:33.265339
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "fd5c8ad2c5db"
down_revision: str | None = "e4f7dd7d4711"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("actors", sa.Column("locked_fields", sa.JSON(), nullable=False, server_default=sa.text("'[]'")))


def downgrade() -> None:
    with op.batch_alter_table("actors") as batch_op:
        batch_op.drop_column("locked_fields")
