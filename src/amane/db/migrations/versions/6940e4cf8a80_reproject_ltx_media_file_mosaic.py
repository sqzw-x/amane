"""reproject ltx media file mosaic

Revision ID: 6940e4cf8a80
Revises: 9054ebacea29
Create Date: 2026-11-02 22:33:38.389631

文件名 `-LTX` 是破解. 按当前 parse_file_info 重算这类行的马赛克标记; 谓词只看文件名,
其它行保持原值.
"""

import re
from collections.abc import Sequence
from pathlib import Path

from alembic import op
from sqlalchemy import text

from amane.parsing import parse_file_info

# revision identifiers, used by Alembic.
revision: str = "6940e4cf8a80"
down_revision: str | None = "9054ebacea29"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LTX_MARKER = re.compile(r"-LTX(?![A-Z0-9])")


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(text("SELECT id, path FROM media_files")).all()
    for row_id, path in rows:
        if _LTX_MARKER.search(Path(path).stem.upper()) is None:
            continue
        info = parse_file_info(path)
        conn.execute(
            text("UPDATE media_files SET mosaic = :mosaic WHERE id = :id"),
            {"mosaic": info.mosaic.name if info.mosaic is not None else None, "id": row_id},
        )


def downgrade() -> None:
    pass
