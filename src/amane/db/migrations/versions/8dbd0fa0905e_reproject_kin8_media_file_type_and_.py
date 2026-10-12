"""reproject kin8 media file type and mosaic

Revision ID: 8dbd0fa0905e
Revises: 4f36abe6b903
Create Date: 2026-10-12 20:02:44.800793

KIN8 番号改归欧美链后按当前 parse_file_info 重算类型与马赛克标记. 谓词只看番号前缀,
因此其它来源的行保持原值; 该来源没有自动标记无码的规则, 这类行的马赛克标记只在路径
词表命中时非空.
"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

from amane.parsing import parse_file_info

# revision identifiers, used by Alembic.
revision: str = "8dbd0fa0905e"
down_revision: str | None = "4f36abe6b903"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_KIN8_PREFIX = "KIN8"


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(text("SELECT id, path FROM media_files")).all()
    for row_id, path in rows:
        info = parse_file_info(path)
        if not info.number.startswith(_KIN8_PREFIX):
            continue
        conn.execute(
            text("UPDATE media_files SET content_type = :content_type, mosaic = :mosaic WHERE id = :id"),
            {
                "content_type": info.content_type.name,
                "mosaic": info.mosaic.name if info.mosaic is not None else None,
                "id": row_id,
            },
        )


def downgrade() -> None:
    pass
