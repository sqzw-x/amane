"""reproject media file mosaic from path

Revision ID: 4e95bf93b0c6
Revises: c1334030b9f1
Create Date: 2026-09-06 17:02:38.124211

按当前 parse_file_info 回填 MediaFile 相位列. 文件名 `-U` / `-UC` 是破解.
"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

from amane.parsing import parse_file_info

# revision identifiers, used by Alembic.
revision: str = "4e95bf93b0c6"
down_revision: str | None = "c1334030b9f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(text("SELECT id, path FROM media_files")).all()
    for row_id, path in rows:
        info = parse_file_info(path)
        conn.execute(
            text(
                "UPDATE media_files SET content_type = :content_type, mosaic = :mosaic, "
                "has_subtitle = :has_subtitle, definition = :definition WHERE id = :id"
            ),
            {
                "content_type": info.content_type.name,
                "mosaic": info.mosaic.name if info.mosaic is not None else None,
                "has_subtitle": info.has_subtitle,
                "definition": info.definition,
                "id": row_id,
            },
        )


def downgrade() -> None:
    pass
