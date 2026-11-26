"""reproject underscore ltx media file mosaic

Revision ID: 140a67ab3526
Revises: dc04a8ae624e
Create Date: 2026-11-28 14:24:01.930225
"""

import re
from collections.abc import Sequence
from pathlib import Path

from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision: str = "140a67ab3526"
down_revision: str | None = "dc04a8ae624e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_LTX_MARKER = re.compile(r"_LTX(?![A-Z0-9])")


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(text("SELECT id, path FROM media_files WHERE instr(upper(path), '_LTX') > 0")).all()
    ids = [{"id": row.id} for row in rows if _LTX_MARKER.search(Path(row.path).stem.upper())]
    if ids:
        conn.execute(text("UPDATE media_files SET mosaic = 'CRACKED' WHERE id = :id"), ids)


def downgrade() -> None:
    # 回填前的标记未保存.
    pass
