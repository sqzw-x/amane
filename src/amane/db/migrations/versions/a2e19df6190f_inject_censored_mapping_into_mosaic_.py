"""inject censored mapping into mosaic placeholders

Revision ID: a2e19df6190f
Revises: 0980003004e2
Create Date: 2026-09-11 03:45:24.684582

存量 `{mosaic?}` 补 `censored=` 空映射, 有码号仍按空值输出. 已写 `|censored=` / `,censored=` 的不改.
"""

import re
from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

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
_MOSAIC_PLACEHOLDER = re.compile(r"\{mosaic\?(?:\|[^}]*)?\}")
# 必须紧挨分隔符, 否则 `uncensored=` 含子串 `censored=`.
_HAS_CENSORED_KEY = re.compile(r"[|,]censored=")


def _inject_censored_empty(template: str) -> str:
    def repl(match: re.Match[str]) -> str:
        token = match.group(0)
        if _HAS_CENSORED_KEY.search(token):
            return token
        if "|" in token:
            return f"{token[:-1]},censored=}}"
        return "{mosaic?|censored=}"

    return _MOSAIC_PLACEHOLDER.sub(repl, template)


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
            rewritten = _inject_censored_empty(raw)
            if rewritten != raw:
                updates[column] = rewritten
        if not updates:
            continue
        assignments = ", ".join(f"{key} = :{key}" for key in updates)
        conn.execute(text(f"UPDATE libraries SET {assignments} WHERE id = :id"), {**updates, "id": row["id"]})


def downgrade() -> None:
    pass
