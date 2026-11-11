"""merge definition tiers

Revision ID: e06def65f72a
Revises: e377abaad72f
Create Date: 2026-11-11 21:13:31.338244

SD 并入 480p, HD 并入 720p: 相位列按当前 parser 复投影, 库路径模板里 {def?} 的映射键同步改名.
改名后与已有键重复的那一对丢弃 (旧键让位于规范键), 否则模板会因键重复而无法解析.
"""

import logging
import re
from collections.abc import Sequence

from alembic import op
from sqlalchemy import Connection, text

from amane.parsing import parse_file_info

# revision identifiers, used by Alembic.
revision: str = "e06def65f72a"
down_revision: str | None = "e377abaad72f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")

_TEMPLATE_COLUMNS = (
    "video_template",
    "link_template",
    "strm_content_template",
    "thumb_template",
    "poster_template",
    "fanart_template",
    "extrafanart_template",
    "extrafanart_name_template",
    "nfo_template",
    "trailer_template",
    "subtitle_template",
)

#: 本 revision 落库时的改名口径; 不引用代码里的映射, 避免后续档位再改动时改写历史.
_RENAMED_KEYS = {"SD": "480p", "HD": "720p"}

_DEF_MAPPING = re.compile(r"\{def\?\|([^}]*)\}")


def _rename_mapping_keys(template: str, dropped: list[str]) -> str:
    """只改 {def?} 的映射键; 键按解析器口径去空白, 与既有键撞名的一对记入 dropped 并丢弃."""

    def rewrite(match: re.Match[str]) -> str:
        items = match.group(1).split(",")
        occupied = {item.partition("=")[0].strip() for item in items}
        pairs: list[str] = []
        for item in items:
            key, sep, value = item.partition("=")
            original = key.strip()
            renamed = _RENAMED_KEYS.get(original, original)
            if renamed != original and renamed in occupied:
                dropped.append(f"{original}->{renamed}")
                continue
            occupied.add(renamed)
            pairs.append(f"{renamed}{sep}{value}")
        return "{def?|" + ",".join(pairs) + "}"

    return _DEF_MAPPING.sub(rewrite, template)


def _warn_saved_query_literals(conn: Connection) -> None:
    for value in _RENAMED_KEYS:
        ids: list[int] = []
        for pattern in (f"%'{value}'%", f'%"{value}"%'):
            ids.extend(
                row[0] for row in conn.execute(text("SELECT id FROM saved_queries WHERE sql LIKE :p"), {"p": pattern})
            )
        if ids:
            logger.warning("saved query compares legacy definition literal: value=%s ids=%s", value, sorted(set(ids)))


def upgrade() -> None:
    conn = op.get_bind()
    scanned = changed = 0
    for row_id, path, current in conn.execute(text("SELECT id, path, definition FROM media_files")).all():
        scanned += 1
        definition = parse_file_info(path).definition
        if definition == current:
            continue
        conn.execute(
            text("UPDATE media_files SET definition = :definition WHERE id = :id"),
            {"definition": definition, "id": row_id},
        )
        changed += 1
    logger.info("definition reprojected: scanned=%s changed=%s", scanned, changed)

    for row in conn.execute(text(f"SELECT id, {', '.join(_TEMPLATE_COLUMNS)} FROM libraries")).mappings():
        updates: dict[str, str] = {}
        for column in _TEMPLATE_COLUMNS:
            if row[column] is None or "{def?|" not in row[column]:
                continue
            dropped: list[str] = []
            updates[column] = _rename_mapping_keys(row[column], dropped)
            if dropped:
                logger.warning(
                    "definition mapping keys dropped: library=%s column=%s pairs=%s", row["id"], column, dropped
                )
        if not updates:
            continue
        assignments = ", ".join(f"{column} = :{column}" for column in updates)
        conn.execute(text(f"UPDATE libraries SET {assignments} WHERE id = :id"), {**updates, "id": row["id"]})

    _warn_saved_query_literals(conn)


def downgrade() -> None:
    """合并不可逆: 无法从 480p / 720p 反推文件原先写的是 SD 还是数字写法."""
