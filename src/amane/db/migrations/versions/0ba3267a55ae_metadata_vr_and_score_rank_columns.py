"""metadata vr and score rank columns

Revision ID: 0ba3267a55ae
Revises: 1f050b272f7d
Create Date: 2026-10-10 10:16:38.018011

``vr`` 与 ``score_rank`` 都是既有列的投影, 因此建列之后必须按现有数据回填一次:
存量影片重算这两个判定不需要重新刮削. 回填逻辑与写入路径共用 `is_vr` 与 ``Metadata.score`` 的口径.

建列与回填合在一个事务里 (契约见 migrations/env.py): 中途失败时库回到迁移前状态, 不留半成品, 重跑即可.

down_revision 取建列时的 main head. #259 (ws-subtitle-detect) 也在 1f050b272f7d 上新增 revision,
两条链合并后是 2 个 head; 合入顺序为 #259 在前, 本 PR 在合并前把本文件最早一条 revision 的
down_revision 改成 d9dec3f83da7, 不手写 revision id.
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

from amane.parsing import is_vr

# revision identifiers, used by Alembic.
revision: str = "0ba3267a55ae"
down_revision: str | None = "1f050b272f7d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BATCH = 2000


def upgrade() -> None:
    # 不用 batch_alter_table: 它会按模型重建表, 丢掉 ix_metadata_number 的 COLLATE NOCASE 表达式.
    op.add_column("metadata", sa.Column("score", sa.Float(), nullable=True))
    # 非空列必须带 server_default, 否则存量行无法满足约束.
    op.add_column("metadata", sa.Column("vr", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_index(op.f("ix_metadata_score"), "metadata", ["score"], unique=False)
    op.create_index(op.f("ix_metadata_vr"), "metadata", ["vr"], unique=False)

    _backfill()


def _decode(raw: object) -> object:
    """JSON 列经 text() 读出是字符串; 空列读出 NULL. 解码失败按空值处理: 迁移不应因单行数据中断."""
    if not raw:
        return None
    try:
        return json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return None


def _stored_tags(raw: object) -> list[str]:
    decoded = _decode(raw)
    if not isinstance(decoded, list):
        return []
    return [tag for tag in decoded if isinstance(tag, str)]


def _stored_score(raw: object) -> float | None:
    """与 ``Metadata.score`` 同口径: 字典首值; 非数值的站内评分跳过."""
    decoded = _decode(raw)
    if not isinstance(decoded, dict):
        return None
    for value in decoded.values():
        if isinstance(value, int | float):
            return float(value)
    return None


def _backfill() -> None:
    """按现有 ``number`` / ``tags`` / ``scores`` 重算两列.

    禁止中途提交: 本 revision 的 DDL 与回填必须在同一个事务里, 否则失败后留下「列已建 + 部分行已回填 +
    版本号未推进」的半成品, 重跑 upgrade 因 ``duplicate column name`` 失败 (契约见 migrations/env.py).
    分批只为限制单条语句的绑定参数个数, 不改变事务边界.
    """
    conn = op.get_bind()
    rows = conn.execute(text("SELECT id, number, tags, scores FROM metadata ORDER BY id")).all()
    for start in range(0, len(rows), _BATCH):
        conn.execute(
            text("UPDATE metadata SET vr = :vr, score = :score WHERE id = :id"),
            [
                {
                    "vr": is_vr(number, _stored_tags(raw_tags)),
                    "score": _stored_score(raw_scores),
                    "id": row_id,
                }
                for row_id, number, raw_tags, raw_scores in rows[start : start + _BATCH]
            ],
        )


def downgrade() -> None:
    op.drop_index(op.f("ix_metadata_vr"), table_name="metadata")
    op.drop_index(op.f("ix_metadata_score"), table_name="metadata")
    op.drop_column("metadata", "vr")
    op.drop_column("metadata", "score")
