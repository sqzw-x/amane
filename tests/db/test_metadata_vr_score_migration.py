"""VR 与评分排序列: 建列后按现有 number / tags / scores 回填, 存量影片不重新刮削.

基线取本 revision 自己的 down_revision; 它与 #259 (ws-subtitle-detect) 的 revision 合并后指向
d9dec3f83da7, 用例随之覆盖合并后的真实链条, 不需要改这里.
"""

import sqlite3
from collections.abc import Iterable
from contextlib import closing
from pathlib import Path

import pytest
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

# 跨过迁移的第一批 (_BATCH = 2000): 只有第二批失败才能暴露中间提交留下的半成品.
_BATCH_ROWS = 2500
_BATCH_FAIL_AT = 2100


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


def _batch_rows(count: int) -> list[dict[str, str]]:
    """奇数行是 VR 番号, 偶数行不是; 每 3 行一个评分."""
    return [
        {
            "number": f"VR-{i:04d}" if i % 2 else f"IPX-{i:04d}",
            "tags": '["VR専用"]' if i % 2 else '["巨乳"]',
            "scores": '{"dmm": 7.5}' if i % 3 == 0 else "{}",
        }
        for i in range(count)
    ]


def _columns(db_path: Path) -> set[str]:
    with closing(sqlite3.connect(db_path)) as conn:
        return {row[1] for row in conn.execute("PRAGMA table_info(metadata)")}


def _version(db_path: Path) -> str | None:
    with closing(sqlite3.connect(db_path)) as conn:
        row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        return row[0] if row else None


def _backfilled(db_path: Path) -> tuple[int, int]:
    """(vr 为真的行数, 有评分的行数)."""
    with closing(sqlite3.connect(db_path)) as conn:
        vr_true, scored = conn.execute("SELECT sum(vr), count(score) FROM metadata").fetchone()
        return int(vr_true), int(scored)


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


def test_backfill_failure_rolls_back_columns_and_version(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """回填中途失败时整份 revision 回滚: 不留半成品, 重跑 upgrade 能干净成功.

    分批只改绑定参数个数, 不改变事务边界; 中途提交会让失败后的库停在「列已建 + 部分行已回填 +
    alembic_version 未推进」, 重跑直接撞 duplicate column (契约见 migrations/env.py).
    """
    db_path = tmp_path / "migrate.db"
    cfg, base, url = _cfg(tmp_path)
    command.upgrade(cfg, base)

    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text(_INSERT), _batch_rows(_BATCH_ROWS))
    engine.dispose()
    before_columns = _columns(db_path)

    import amane.parsing as parsing

    real_is_vr = parsing.is_vr
    calls = 0

    def failing_is_vr(number: str | None, tags: Iterable[str] = ()) -> bool:
        nonlocal calls
        calls += 1
        if calls >= _BATCH_FAIL_AT:
            raise RuntimeError("injected failure")
        return real_is_vr(number, tags)

    with monkeypatch.context() as patch:
        patch.setattr(parsing, "is_vr", failing_is_vr)
        with pytest.raises(RuntimeError, match="injected failure"):
            command.upgrade(cfg, "head")

    assert calls == _BATCH_FAIL_AT
    assert _version(db_path) == base
    assert _columns(db_path) == before_columns

    # 重跑: 库仍是迁移前状态, 因此应当干净成功并完成全量回填.
    command.upgrade(cfg, "head")
    assert _version(db_path) == _REVISION
    assert {"vr", "score"} <= _columns(db_path)
    assert _backfilled(db_path) == (
        sum(1 for i in range(_BATCH_ROWS) if i % 2),
        sum(1 for i in range(_BATCH_ROWS) if i % 3 == 0),
    )
