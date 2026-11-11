"""merge definition tiers

Revision ID: e06def65f72a
Revises: e377abaad72f
Create Date: 2026-11-11 21:13:31.338244

SD 并入 480p, HD 并入 720p: 相位列按当前 parser 复投影, 库路径模板里 {def?} 的映射键同步改名.
"""

import re
from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

from amane.parsing import parse_file_info

# revision identifiers, used by Alembic.
revision: str = "e06def65f72a"
down_revision: str | None = "e377abaad72f"
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
    "extrafanart_name_template",
    "nfo_template",
    "trailer_template",
    "subtitle_template",
)

_RENAMED_KEYS = {"SD": "480p", "HD": "720p"}

_DEF_MAPPING = re.compile(r"\{def\?\|([^}]*)\}")


def _rename_mapping_keys(template: str) -> str:
    """只改 {def?} 的映射键, 其余占位符与字面量保持原样; 键按解析器口径去空白."""

    def rewrite(match: re.Match[str]) -> str:
        pairs: list[str] = []
        for item in match.group(1).split(","):
            key, sep, value = item.partition("=")
            pairs.append(f"{_RENAMED_KEYS.get(key.strip(), key.strip())}{sep}{value}")
        return "{def?|" + ",".join(pairs) + "}"

    return _DEF_MAPPING.sub(rewrite, template)


def upgrade() -> None:
    conn = op.get_bind()
    for row_id, path in conn.execute(text("SELECT id, path FROM media_files")).all():
        conn.execute(
            text("UPDATE media_files SET definition = :definition WHERE id = :id"),
            {"definition": parse_file_info(path).definition, "id": row_id},
        )

    for row in conn.execute(text(f"SELECT id, {', '.join(_TEMPLATE_COLUMNS)} FROM libraries")).mappings():
        updates = {
            column: _rename_mapping_keys(row[column])
            for column in _TEMPLATE_COLUMNS
            if row[column] is not None and "{def?|" in row[column]
        }
        if not updates:
            continue
        assignments = ", ".join(f"{column} = :{column}" for column in updates)
        conn.execute(text(f"UPDATE libraries SET {assignments} WHERE id = :id"), {**updates, "id": row["id"]})


def downgrade() -> None:
    """合并不可逆: 无法从 480p / 720p 反推文件原先写的是 SD 还是数字写法."""
