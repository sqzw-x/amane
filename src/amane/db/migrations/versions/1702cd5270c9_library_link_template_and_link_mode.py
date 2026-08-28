"""library link_template and link_mode

Revision ID: 1702cd5270c9
Revises: b2ce3c344b5a
Create Date: 2026-08-28 21:22:07.328840
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1702cd5270c9"
down_revision: str | None = "b2ce3c344b5a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("libraries") as batch_op:
        batch_op.add_column(sa.Column("link_template", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("link_mode", sa.String(), nullable=False, server_default="strm"))


def downgrade() -> None:
    with op.batch_alter_table("libraries") as batch_op:
        batch_op.drop_column("link_mode")
        batch_op.drop_column("link_template")
