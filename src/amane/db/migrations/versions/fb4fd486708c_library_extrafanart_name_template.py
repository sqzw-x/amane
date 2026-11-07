"""library extrafanart name template

Revision ID: fb4fd486708c
Revises: 56fe451426ef
Create Date: 2026-11-08 01:10:53.661774
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "fb4fd486708c"
down_revision: str | None = "56fe451426ef"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """剧照文件名模板; 空值走默认 ``{index}.jpg``."""
    op.add_column(
        "libraries", sa.Column("extrafanart_name_template", sqlmodel.sql.sqltypes.AutoString(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("libraries", "extrafanart_name_template")
