"""library path template optional groups

Revision ID: c3d8f95d2198
Revises: 099436e749d6
Create Date: 2026-08-29 19:16:25.527088
"""

import re
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision: str = "c3d8f95d2198"
down_revision: str | None = "099436e749d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TEMPLATE_COLUMNS = (
    "video_template",
    "link_template",
    "thumb_template",
    "poster_template",
    "fanart_template",
    "extrafanart_template",
    "nfo_template",
    "trailer_template",
    "subtitle_template",
)


def _rename_placeholders(value: str) -> str:
    renamed = re.sub(r"\{mosaic(?!\?)\}", "{mosaic?}", value)
    return re.sub(r"\{definition(?!\?)\}", "{def?}", renamed)


def _inject_before_ext(template: str, group: str, marker: str) -> str:
    if marker in template:
        return template
    needle = ".{ext}"
    idx = template.rfind(needle)
    if idx >= 0:
        return template[:idx] + group + template[idx:]
    return template + group


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(
        text(
            "SELECT id, cd_suffix_template, video_template, link_template, thumb_template, "
            "poster_template, fanart_template, extrafanart_template, nfo_template, "
            "trailer_template, subtitle_template FROM libraries"
        )
    ).mappings()
    for row in rows:
        updates: dict[str, str | None] = {}
        video = _rename_placeholders(row["video_template"] or "")
        suffix = (row["cd_suffix_template"] or "").strip()
        if suffix:
            video = _inject_before_ext(video, f"[{suffix.replace('{cd}', '{cd?}')}]", "{cd?}")
        video = _inject_before_ext(video, "[-{sub?}]", "{sub?}")
        updates["video_template"] = video
        for column in _TEMPLATE_COLUMNS:
            if column == "video_template":
                continue
            raw = row[column]
            if raw is not None:
                updates[column] = _rename_placeholders(raw)
        assignments = ", ".join(f"{key} = :{key}" for key in updates)
        conn.execute(text(f"UPDATE libraries SET {assignments} WHERE id = :id"), {**updates, "id": row["id"]})

    with op.batch_alter_table("libraries") as batch_op:
        batch_op.drop_column("cd_suffix_template")


def downgrade() -> None:
    with op.batch_alter_table("libraries") as batch_op:
        batch_op.add_column(sa.Column("cd_suffix_template", sa.String(), server_default="-CD{cd}", nullable=False))
