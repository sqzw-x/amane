"""library strm_content_template

Revision ID: 9d6cb1706ad1
Revises: 54138fa1c160
Create Date: 2026-09-03 02:31:57.362464
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9d6cb1706ad1"
down_revision: str | None = "54138fa1c160"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("libraries") as batch_op:
        batch_op.add_column(sa.Column("strm_content_template", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("libraries") as batch_op:
        batch_op.drop_column("strm_content_template")
