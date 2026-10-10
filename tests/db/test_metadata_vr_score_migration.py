"""VR 与评分排序列: 建列后按现有 number / tags / scores 回填, 存量影片不重新刮削.

基线取本 revision 自己的 down_revision; 它与 #259 (ws-subtitle-detect) 的 revision 合并后指向
d9dec3f83da7, 用例随之覆盖合并后的真实链条, 不需要改这里.
"""

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

from tests.helpers import alembic_config, migrations_dir

_REVISION = "0ba3267a55ae"

_INSERT = (
    "INSERT INTO metadata (number, tags, scores, locked_fields, created_at, updated_at) "
    "VALUES (:number, :tags, :scores, '[]', '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
)

_ROWS = [
    # (number, tags JSON, scores JSON, 期望 vr, 期望 score)
    ("HUNVR-211", '["VR専用", "8KVR"]', '{"dmm": 8.5, "javdb": 9.0}', True, 8.5),
    ("VR-001", "[]", "{}", True, None),
    ("8KVR-012", '["単体作品"]', '{"dmm": 7.25}', True, 7.25),
    ("IPX-123", '["VR"]', "{}", True, None),
    ("ABP-123", '["巨乳"]', '{"dmm": 0}', False, 0.0),
    ("MIDV-001", "[]", "null", False, None),
    ("MIDV-002", '"not-a-list"', '{"javdb": null}', False, None),
]


def _base_revision() -> str:
    """本 revision 的下一条; 基线随 down_revision 一起对齐, 不在这里写死."""
    script = ScriptDirectory(str(migrations_dir()))
    base = script.get_revision(_REVISION).down_revision
    assert isinstance(base, str)
    return base


def _insert_rows(conn) -> None:
    for number, tags, scores, _, _ in _ROWS:
        conn.execute(text(_INSERT), {"number": number, "tags": tags, "scores": scores})


def _cfg(tmp_path: Path) -> tuple[Config, str, str]:
    """(alembic 配置, 下一条 revision, 引擎 URL)."""
    db_path = tmp_path / "migrate.db"
    return alembic_config(db_path), _base_revision(), f"sqlite:///{db_path}"


def test_metadata_vr_and_score_backfill(tmp_path: Path) -> None:
    cfg, base, url = _cfg(tmp_path)
    command.upgrade(cfg, base)

    engine = create_engine(url)
    with engine.begin() as conn:
        _insert_rows(conn)

    command.upgrade(cfg, "head")

    with engine.connect() as conn:
        columns = {column["name"] for column in inspect(conn).get_columns("metadata")}
        assert {"vr", "score"} <= columns
        index_names = {index["name"] for index in inspect(conn).get_indexes("metadata")}
        assert {"ix_metadata_vr", "ix_metadata_score"} <= index_names
        rows = {row.number: row for row in conn.execute(text("SELECT number, vr, score FROM metadata")).all()}

    for number, _, _, expected_vr, expected_score in _ROWS:
        row = rows[number]
        assert bool(row.vr) is expected_vr, number
        assert row.score == expected_score, number


def test_metadata_vr_and_score_downgrade(tmp_path: Path) -> None:
    cfg, base, url = _cfg(tmp_path)
    command.upgrade(cfg, "head")

    command.downgrade(cfg, base)

    engine = create_engine(url)
    with engine.connect() as conn:
        columns = {column["name"] for column in inspect(conn).get_columns("metadata")}
    assert "vr" not in columns
    assert "score" not in columns
