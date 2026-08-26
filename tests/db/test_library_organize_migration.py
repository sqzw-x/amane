"""Library organize defaults + trailer_pattern schema migration."""

from __future__ import annotations

import json
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


def test_library_organize_columns_backfill_existing_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "migrate.db"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")

    command.upgrade(cfg, "e08b11d79fbb")

    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO libraries (name, path, watch_enabled, recursive, patterns, move_mode, video_template) "
                "VALUES ('t', '/m', 1, 1, '[]', 'move', '{studio}/{number}/{number}.{ext}')"
            )
        )

    command.upgrade(cfg, "head")

    with engine.connect() as conn:
        columns = {column["name"] for column in inspect(conn).get_columns("libraries")}
        assert {"write_nfo", "copy_resources", "trailer_pattern"} <= columns
        row = conn.execute(text("SELECT write_nfo, copy_resources, trailer_pattern FROM libraries")).one()
        assert row.write_nfo in (1, True)
        assert row.trailer_pattern == "(?i)trailer"
        resources = json.loads(row.copy_resources) if isinstance(row.copy_resources, str) else row.copy_resources
        assert set(resources) == {"thumb", "poster", "extrafanart", "trailer"}

    engine.dispose()


def test_library_patterns_json_null_backfilled(tmp_path: Path) -> None:
    db_path = tmp_path / "migrate.db"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")

    command.upgrade(cfg, "27c1cec6341f")

    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO libraries (name, path, watch_enabled, recursive, patterns, move_mode, video_template) "
                "VALUES ('t', '/m', 1, 1, 'null', 'move', '{studio}/{number}/{number}.{ext}')"
            )
        )

    command.upgrade(cfg, "head")

    with engine.connect() as conn:
        nullable = {column["name"]: column["nullable"] for column in inspect(conn).get_columns("libraries")}
        assert nullable["patterns"] is False
        row = conn.execute(text("SELECT patterns FROM libraries")).one()
        patterns = json.loads(row.patterns) if isinstance(row.patterns, str) else row.patterns
        assert patterns == []

    engine.dispose()


def test_library_automation_backfills_from_watch_enabled(tmp_path: Path) -> None:
    """watch_enabled True→scrape, False→none; 新列非空, 旧列删除."""
    db_path = tmp_path / "migrate.db"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")

    command.upgrade(cfg, "1159ff536a74")

    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO libraries (name, path, watch_enabled, recursive, patterns, move_mode, video_template) "
                "VALUES "
                "('on', '/a', 1, 1, '[]', 'move', '{number}.{ext}'), "
                "('off', '/b', 0, 1, '[]', 'move', '{number}.{ext}')"
            )
        )

    command.upgrade(cfg, "head")

    with engine.connect() as conn:
        columns = {column["name"] for column in inspect(conn).get_columns("libraries")}
        assert "automation" in columns
        assert "watch_enabled" not in columns
        rows = {
            str(row["name"]): str(row["automation"])
            for row in conn.execute(text("SELECT name, automation FROM libraries")).mappings()
        }
        assert rows == {"on": "scrape", "off": "none"}

    engine.dispose()


def test_library_blacklist_patterns_backfilled_for_existing_rows(tmp_path: Path) -> None:
    """存量行迁移后 blacklist_patterns 非空且为 [] (关闭状态)."""
    db_path = tmp_path / "migrate.db"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")

    command.upgrade(cfg, "5abbb79b1ae6")

    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO libraries "
                "(name, path, automation, recursive, patterns, move_mode, video_template, write_nfo, "
                "copy_resources, trailer_pattern) "
                "VALUES ('t', '/m', 'scrape', 1, '[]', 'move', '{number}.{ext}', 1, '[\"thumb\"]', '(?i)trailer')"
            )
        )

    command.upgrade(cfg, "head")

    with engine.connect() as conn:
        columns = {column["name"] for column in inspect(conn).get_columns("libraries")}
        assert "blacklist_patterns" in columns
        row = conn.execute(text("SELECT blacklist_patterns FROM libraries")).one()
        patterns = (
            json.loads(row.blacklist_patterns) if isinstance(row.blacklist_patterns, str) else row.blacklist_patterns
        )
        assert patterns == []

    engine.dispose()
