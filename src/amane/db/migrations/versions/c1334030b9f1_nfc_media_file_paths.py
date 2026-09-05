"""nfc media file paths

Revision ID: c1334030b9f1
Revises: 9d6cb1706ad1
Create Date: 2026-09-05 07:58:00.681029

MediaFile.path 以 NFC 存库. 同一 NFC 路径的重复行只保留一行: 有 Metadata 优先, 其次 scraped, 再取较小 id.
"""

from collections import defaultdict
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from amane.utils.path import nfc_path

# revision identifiers, used by Alembic.
revision: str = "c1334030b9f1"
down_revision: str | None = "9d6cb1706ad1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _keeper(group: list[tuple[int, str, int | None, str]]) -> tuple[int, str, int | None, str]:
    def rank(row: tuple[int, str, int | None, str]) -> tuple[int, int, int]:
        has_meta = 0 if row[2] is not None else 1
        scraped = 0 if row[3].casefold() == "scraped" else 1
        return (has_meta, scraped, row[0])

    return min(group, key=rank)


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, path, metadata_id, status FROM media_files")).fetchall()
    groups: dict[str, list[tuple[int, str, int | None, str]]] = defaultdict(list)
    for row in rows:
        groups[nfc_path(str(row[1]))].append((row[0], row[1], row[2], row[3]))

    for canonical, group in groups.items():
        keep_id, keep_path, _meta, _status = _keeper(group)
        for extra_id, _path, _meta, _status in group:
            if extra_id == keep_id:
                continue
            conn.execute(sa.text("DELETE FROM media_files WHERE id = :id"), {"id": extra_id})
        if keep_path != canonical:
            conn.execute(
                sa.text("UPDATE media_files SET path = :path WHERE id = :id"),
                {"path": canonical, "id": keep_id},
            )


def downgrade() -> None:
    pass
