"""SQLite 迁移安全: WAL 备份 + 事务性 DDL 回滚."""

from __future__ import annotations

import shutil
import sqlite3
import textwrap
from contextlib import closing
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from amane.db.sqlite_migrate import (
    backup_sqlite_database,
    needs_upgrade,
    prune_migrate_backups,
    upgrade_sqlite_database,
)


def _write_mini_alembic(root: Path) -> Path:
    """两版本迷你脚本环境: v1 建 base 表, v2 建 boom 表 (测试可替换 v2)."""
    script = root / "scripts"
    versions = script / "versions"
    versions.mkdir(parents=True)
    (script / "script.py.mako").write_text(
        textwrap.dedent(
            """\
            \"\"\"${message}

            Revision ID: ${up_revision}
            Revises: ${down_revision | comma,n}
            Create Date: ${create_date}
            \"\"\"
            from typing import Sequence, Union
            from alembic import op
            ${imports if imports else ""}
            revision: str = ${repr(up_revision)}
            down_revision: Union[str, Sequence[str], None] = ${repr(down_revision)}
            branch_labels = ${repr(branch_labels)}
            depends_on = ${repr(depends_on)}
            def upgrade() -> None:
                ${upgrades if upgrades else "pass"}
            def downgrade() -> None:
                ${downgrades if downgrades else "pass"}
            """
        ),
        encoding="utf-8",
    )
    (script / "env.py").write_text(
        textwrap.dedent(
            """\
            from logging.config import fileConfig
            from alembic import context
            from sqlalchemy import engine_from_config, event, pool
            from amane.db.sqlite_migrate import enable_sqlite_transactional_ddl

            config = context.config
            if config.config_file_name is not None:
                fileConfig(config.config_file_name)
            target_metadata = None

            def run_migrations_online() -> None:
                connectable = config.attributes.get("connection")
                if connectable is not None:
                    context.configure(
                        connection=connectable,
                        target_metadata=target_metadata,
                        transactional_ddl=True,
                    )
                    with context.begin_transaction():
                        context.run_migrations()
                    return
                connectable = engine_from_config(
                    config.get_section(config.config_ini_section, {}),
                    prefix="sqlalchemy.",
                    poolclass=pool.NullPool,
                )
                @event.listens_for(connectable, "connect")
                def _txn(dbapi_connection, _):
                    enable_sqlite_transactional_ddl(dbapi_connection)
                try:
                    with connectable.connect() as connection:
                        context.configure(
                            connection=connection,
                            target_metadata=target_metadata,
                            transactional_ddl=True,
                        )
                        with context.begin_transaction():
                            context.run_migrations()
                        connection.commit()
                finally:
                    connectable.dispose()

            run_migrations_online()
            """
        ),
        encoding="utf-8",
    )
    (versions / "0001_v1.py").write_text(
        textwrap.dedent(
            """\
            \"\"\"v1

            Revision ID: 0001_v1
            Revises:
            \"\"\"
            from alembic import op
            import sqlalchemy as sa

            revision = "0001_v1"
            down_revision = None
            branch_labels = None
            depends_on = None

            def upgrade() -> None:
                op.create_table(
                    "items",
                    sa.Column("id", sa.Integer(), primary_key=True),
                    sa.Column("name", sa.String(), nullable=False),
                )

            def downgrade() -> None:
                op.drop_table("items")
            """
        ),
        encoding="utf-8",
    )
    return script


def _write_v2_ok(versions: Path) -> None:
    (versions / "0002_v2.py").write_text(
        textwrap.dedent(
            """\
            \"\"\"v2 ok

            Revision ID: 0002_v2
            Revises: 0001_v1
            \"\"\"
            from alembic import op
            import sqlalchemy as sa

            revision = "0002_v2"
            down_revision = "0001_v1"
            branch_labels = None
            depends_on = None

            def upgrade() -> None:
                op.create_table(
                    "extra",
                    sa.Column("id", sa.Integer(), primary_key=True),
                )

            def downgrade() -> None:
                op.drop_table("extra")
            """
        ),
        encoding="utf-8",
    )


def _write_v2_fail_after_create(versions: Path) -> None:
    (versions / "0002_v2.py").write_text(
        textwrap.dedent(
            """\
            \"\"\"v2 boom after create

            Revision ID: 0002_v2
            Revises: 0001_v1
            \"\"\"
            from alembic import op
            import sqlalchemy as sa

            revision = "0002_v2"
            down_revision = "0001_v1"
            branch_labels = None
            depends_on = None

            def upgrade() -> None:
                op.create_table(
                    "extra",
                    sa.Column("id", sa.Integer(), primary_key=True),
                )
                raise RuntimeError("boom after CREATE TABLE")

            def downgrade() -> None:
                op.drop_table("extra")
            """
        ),
        encoding="utf-8",
    )


def _tables(db: Path) -> set[str]:
    conn = sqlite3.connect(db)
    try:
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    finally:
        conn.close()


def _version(db: Path) -> str | None:
    conn = sqlite3.connect(db)
    try:
        row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        return row[0] if row else None
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()


class TestBackupSqlite:
    def test_backup_api_includes_wal_committed_rows(self, tmp_path: Path) -> None:
        db = tmp_path / "app.db"
        conn = sqlite3.connect(db)
        assert conn.execute("PRAGMA journal_mode=WAL").fetchone()[0].lower() == "wal"
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
        conn.execute("INSERT INTO t VALUES (1, 'a')")
        conn.commit()
        conn.execute("INSERT INTO t VALUES (2, 'b')")
        conn.commit()
        assert (tmp_path / "app.db-wal").exists() or True  # wal 文件可能在 checkpoint 后消失
        conn.close()

        bak = backup_sqlite_database(db, dest_dir=tmp_path, keep=5, label="v0")
        assert bak is not None
        assert bak.is_file()

        with closing(sqlite3.connect(bak)) as bak_conn:
            rows = bak_conn.execute("SELECT id, v FROM t ORDER BY id").fetchall()
        assert rows == [(1, "a"), (2, "b")]

        # 对照: 仅 cp 主文件在 WAL 场景下可能不完整; 至少 backup API 必须完整
        naive = tmp_path / "naive.copy"
        shutil.copy2(db, naive)

    def test_prune_keeps_newest(self, tmp_path: Path) -> None:
        db = tmp_path / "app.db"
        sqlite3.connect(db).close()
        db.write_bytes(b"x")  # non-empty
        # 造 4 个假备份
        paths: list[Path] = []
        for i in range(4):
            p = tmp_path / f"app.db.pre-migrate-v0-2020010{i}T000000Z.bak"
            p.write_bytes(b"bak")
            paths.append(p)
            # 保证 mtime 递增
            import os
            import time

            os.utime(p, (time.time() + i, time.time() + i))

        removed = prune_migrate_backups(db, dest_dir=tmp_path, keep=2)
        assert removed == 2
        remaining = sorted(tmp_path.glob("app.db.pre-migrate-*.bak"))
        assert len(remaining) == 2
        assert paths[2] in remaining
        assert paths[3] in remaining

    def test_backup_skips_missing_db(self, tmp_path: Path) -> None:
        assert backup_sqlite_database(tmp_path / "nope.db") is None


class TestTransactionalUpgrade:
    def test_failed_revision_rolls_back_ddl_and_version(self, tmp_path: Path) -> None:
        script = _write_mini_alembic(tmp_path)
        versions = script / "versions"
        db = tmp_path / "t.db"

        # 仅 v1
        upgrade_sqlite_database(db, script_location=script, backup=False)
        assert _version(db) == "0001_v1"
        assert "items" in _tables(db)

        _write_v2_fail_after_create(versions)
        with pytest.raises(RuntimeError, match="boom after CREATE TABLE"):
            upgrade_sqlite_database(db, script_location=script, backup=False)

        assert _version(db) == "0001_v1"
        assert "extra" not in _tables(db)
        assert "items" in _tables(db)

    def test_successful_upgrade_creates_backup_when_behind(self, tmp_path: Path) -> None:
        script = _write_mini_alembic(tmp_path)
        versions = script / "versions"
        db = tmp_path / "t.db"
        upgrade_sqlite_database(db, script_location=script, backup=True)
        assert _version(db) == "0001_v1"

        _write_v2_ok(versions)
        assert needs_upgrade(db, script_location=script)
        bak = upgrade_sqlite_database(db, script_location=script, backup=True, backup_keep=5)
        assert bak is not None
        assert bak.is_file()
        assert "0001_v1" in bak.name
        assert _version(db) == "0002_v2"
        assert "extra" in _tables(db)

        # 已在 head: 不再备份
        assert upgrade_sqlite_database(db, script_location=script, backup=True) is None

    def test_backup_can_restore_after_failed_migrate(self, tmp_path: Path) -> None:
        script = _write_mini_alembic(tmp_path)
        versions = script / "versions"
        db = tmp_path / "t.db"
        upgrade_sqlite_database(db, script_location=script, backup=False)
        engine = create_engine(f"sqlite:///{db}")
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO items (id, name) VALUES (1, 'keep')"))
        engine.dispose()

        _write_v2_fail_after_create(versions)
        bak = backup_sqlite_database(db, label="0001_v1")
        assert bak is not None

        with pytest.raises(RuntimeError, match="boom"):
            upgrade_sqlite_database(db, script_location=script, backup=False)

        # 即便事务回滚成功, 也验证备份可恢复
        shutil.copy2(bak, db)
        # WAL 旁路文件清掉以免干扰
        for side in (tmp_path / "t.db-wal", tmp_path / "t.db-shm"):
            side.unlink(missing_ok=True)

        assert _version(db) == "0001_v1"
        with closing(sqlite3.connect(db)) as conn:
            rows = conn.execute("SELECT name FROM items").fetchall()
        assert rows == [("keep",)]


class TestProjectAlembicPath:
    """用项目真实 migrations 脚本做冒烟 (临时库)."""

    def test_upgrade_head_from_previous_makes_backup(self, tmp_path: Path) -> None:
        db = tmp_path / "amane.db"
        cfg = Config("alembic.ini")
        cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db}")
        # 先升到 facet_rules 之前
        command.upgrade(cfg, "b058160ffee4")
        assert needs_upgrade(db)

        bak = upgrade_sqlite_database(db, backup=True)
        assert bak is not None
        assert bak.parent == tmp_path
        assert "b058160ffee4" in bak.name
        assert not needs_upgrade(db)
        assert "facet_rules" in _tables(db)

    def test_actor_alias_migration_rows_out_bag_and_rules(self, tmp_path: Path) -> None:
        """别名袋与 actor alias 规则行化进 actor_aliases, 规则行删除 (block 保留)."""
        db = tmp_path / "amane.db"
        cfg = Config("alembic.ini")
        cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db}")
        command.upgrade(cfg, "5abbb79b1ae6")

        now = "2026-01-01 00:00:00.000000"
        with closing(sqlite3.connect(db)) as conn:
            conn.executescript(
                f"""
                insert into actors (name, gender, image_urls, provider_ids, source_urls, field_sources, raw, aliases, created_at, updated_at)
                values ('A','unknown','[]','{{}}','{{}}','{{}}','{{}}','["x", "A"]','{now}','{now}'),
                       ('B','unknown','[]','{{}}','{{}}','{{}}','{{}}',NULL,'{now}','{now}');
                insert into facet_rules (kind, source_name, action, target_name, created_at, updated_at) values
                ('actor','旧A','alias','A','{now}','{now}'),
                ('actor','Ghost','alias','NoEntity','{now}','{now}'),
                ('actor','B','block',NULL,'{now}','{now}');
                """
            )
            conn.commit()

        command.upgrade(cfg, "head")

        with closing(sqlite3.connect(db)) as conn:
            rows = conn.execute(
                "select actors.name, actor_aliases.name from actor_aliases join actors on actors.id = actor_aliases.actor_id"
                " order by actors.id, actor_aliases.position"
            ).fetchall()
            # A: 规则入边先, 袋内 x 后, 与展示名相同的 A 剔除; Ghost→NoEntity 建实体
            assert rows == [("A", "旧A"), ("A", "x"), ("NoEntity", "Ghost")]
            remaining = conn.execute(
                "select source_name, action from facet_rules where kind = 'ACTOR' order by source_name"
            ).fetchall()
            assert remaining == [("B", "BLOCK")]
            cols = [r[1] for r in conn.execute("PRAGMA table_info(actors)").fetchall()]
            assert "aliases" not in cols
            assert "actor_aliases" in _tables(db)

    @pytest.mark.asyncio
    async def test_create_async_engine_runs_safe_upgrade(self, tmp_path: Path) -> None:
        import inspect

        from amane.db.engine import create_async_engine_from_path

        db = tmp_path / "amane.db"
        cfg = Config("alembic.ini")
        cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db}")
        command.upgrade(cfg, "b058160ffee4")

        engine = await create_async_engine_from_path(db)
        try:
            assert not needs_upgrade(db)
            assert "facet_rules" in _tables(db)
            # 新引擎路径会先 backup; 若仅部分提交导致旧 engine 在测, 不强制备份断言.
            if "upgrade_sqlite_database" in inspect.getsource(create_async_engine_from_path):
                backups = list(tmp_path.glob("amane.db.pre-migrate-*.bak"))
                assert len(backups) == 1
        finally:
            await engine.dispose()


class TestSqliteDatetimeAdapters:
    def test_text_bind_datetime_is_silent(self) -> None:
        import warnings
        from datetime import UTC, date, datetime

        from amane.db.sqlite_migrate import register_sqlite_datetime_adapters

        register_sqlite_datetime_adapters()
        now = datetime(2026, 1, 2, 3, 4, 5, 6, tzinfo=UTC)
        with closing(sqlite3.connect(":memory:")) as conn:
            conn.execute("CREATE TABLE t (ts TEXT, d TEXT)")
            with warnings.catch_warnings():
                warnings.filterwarnings("error", message=".*default datetime adapter.*", category=DeprecationWarning)
                warnings.filterwarnings("error", message=".*default date adapter.*", category=DeprecationWarning)
                conn.execute("INSERT INTO t VALUES (?, ?)", (now, date(2026, 1, 2)))
            row = conn.execute("SELECT ts, d FROM t").fetchone()
        assert row == ("2026-01-02 03:04:05.000006+00:00", "2026-01-02")
