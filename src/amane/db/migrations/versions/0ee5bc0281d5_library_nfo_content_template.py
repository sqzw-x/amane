"""library nfo content template

Revision ID: 0ee5bc0281d5
Revises: 1f050b272f7d
Create Date: 2026-10-10 09:52:12.431145
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0ee5bc0281d5"
down_revision: str | None = "1f050b272f7d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("libraries") as batch_op:
        batch_op.add_column(sa.Column("nfo_content_template", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("libraries") as batch_op:
        batch_op.drop_column("nfo_content_template")
