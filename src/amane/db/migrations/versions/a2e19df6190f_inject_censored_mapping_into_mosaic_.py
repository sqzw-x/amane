"""inject censored mapping into mosaic placeholders

Revision ID: a2e19df6190f
Revises: 0980003004e2
Create Date: 2026-09-11 03:45:24.684582

存量 `{mosaic?}` 补 censored 映射, 有码号仍按空值输出. 已有空 key 时 censored 使用同一缺省.
"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

from amane.organize.template import inject_censored_mosaic_mapping

# revision identifiers, used by Alembic.
revision: str = "a2e19df6190f"
down_revision: str | None = "0980003004e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TEMPLATE_COLUMNS = (
    "video_template",
    "link_template",
    "strm_content_template",
    "thumb_template",
    "poster_template",
    "fanart_template",
    "extrafanart_template",
    "nfo_template",
    "trailer_template",
    "subtitle_template",
)


def upgrade() -> None:
    conn = op.get_bind()
    columns = ", ".join(("id", *_TEMPLATE_COLUMNS))
    rows = conn.execute(text(f"SELECT {columns} FROM libraries")).mappings()
    for row in rows:
        updates: dict[str, str] = {}
        for column in _TEMPLATE_COLUMNS:
            raw = row[column]
            if not raw:
                continue
            rewritten = inject_censored_mosaic_mapping(raw)
            if rewritten != raw:
                updates[column] = rewritten
        if not updates:
            continue
        assignments = ", ".join(f"{key} = :{key}" for key in updates)
        conn.execute(text(f"UPDATE libraries SET {assignments} WHERE id = :id"), {**updates, "id": row["id"]})


def downgrade() -> None:
    pass
