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
        assert {"write_nfo", "copy_resources", "trailer_pattern", "link_template", "link_mode"} <= columns
        row = conn.execute(
            text("SELECT write_nfo, copy_resources, trailer_pattern, link_template, link_mode FROM libraries")
        ).one()
        assert row.write_nfo in (1, True)
        assert row.trailer_pattern == "(?i)trailer"
        assert row.link_template is None
        assert row.link_mode == "STRM"
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
        assert rows == {"on": "SCRAPE", "off": "NONE"}

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


def test_library_subtitle_extensions_backfilled_for_existing_rows(tmp_path: Path) -> None:
    """存量行迁移后 subtitle_extensions 为默认扩展名列表."""
    db_path = tmp_path / "migrate.db"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")

    command.upgrade(cfg, "cbede59bbb9e")

    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO libraries "
                "(name, path, automation, recursive, patterns, move_mode, video_template, write_nfo, "
                "copy_resources, trailer_pattern, blacklist_patterns) "
                "VALUES ('t', '/m', 'scrape', 1, '[]', 'move', '{number}.{ext}', 1, '[\"thumb\"]', '(?i)trailer', '[]')"
            )
        )

    command.upgrade(cfg, "head")

    with engine.connect() as conn:
        columns = {column["name"] for column in inspect(conn).get_columns("libraries")}
        assert "subtitle_extensions" in columns
        row = conn.execute(text("SELECT subtitle_extensions FROM libraries")).one()
        extensions = (
            json.loads(row.subtitle_extensions) if isinstance(row.subtitle_extensions, str) else row.subtitle_extensions
        )
        assert extensions == [".srt", ".ass", ".ssa", ".vtt", ".sub"]

    engine.dispose()


def test_library_min_file_size_backfilled_for_existing_rows(tmp_path: Path) -> None:
    """存量行迁移后 min_file_size 为 0 (关闭)."""
    db_path = tmp_path / "migrate.db"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")

    command.upgrade(cfg, "1702cd5270c9")

    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO libraries "
                "(name, path, automation, recursive, patterns, move_mode, video_template, write_nfo, "
                "copy_resources, trailer_pattern, blacklist_patterns, subtitle_extensions) "
                "VALUES ('t', '/m', 'scrape', 1, '[]', 'move', '{number}.{ext}', 1, '[\"thumb\"]', "
                "'(?i)trailer', '[]', '[\".srt\"]')"
            )
        )

    command.upgrade(cfg, "head")

    with engine.connect() as conn:
        columns = {column["name"] for column in inspect(conn).get_columns("libraries")}
        assert "min_file_size" in columns
        row = conn.execute(text("SELECT min_file_size FROM libraries")).one()
        assert row.min_file_size == 0

    engine.dispose()


def test_library_path_template_optional_groups_rewrites_and_drops_cd_suffix(tmp_path: Path) -> None:
    """v0.5.0 存量: mosaic/definition 改名, 分集后缀并进 video_template, 并补 subtitle 可选组."""
    db_path = tmp_path / "migrate.db"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")

    command.upgrade(cfg, "099436e749d6")

    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO libraries "
                "(name, path, automation, recursive, patterns, move_mode, video_template, write_nfo, "
                "copy_resources, trailer_pattern, blacklist_patterns, subtitle_extensions, min_file_size, "
                "cd_suffix_template, nfo_template) "
                "VALUES ('a', '/m', 'scrape', 1, '[]', 'move', '{mosaic}/{definition}/{number}.{ext}', 1, "
                "'[\"thumb\"]', '(?i)trailer', '[]', '[\".srt\"]', 0, '-Part {cd}', '{mosaic}/{number}.nfo')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO libraries "
                "(name, path, automation, recursive, patterns, move_mode, video_template, write_nfo, "
                "copy_resources, trailer_pattern, blacklist_patterns, subtitle_extensions, min_file_size, "
                "cd_suffix_template) "
                "VALUES ('b', '/n', 'scrape', 1, '[]', 'move', '{studio}/{number}/{number}.{ext}', 1, "
                "'[\"thumb\"]', '(?i)trailer', '[]', '[\".srt\"]', 0, '')"
            )
        )

    command.upgrade(cfg, "head")

    with engine.connect() as conn:
        columns = {column["name"] for column in inspect(conn).get_columns("libraries")}
        assert "cd_suffix_template" not in columns
        rows = conn.execute(text("SELECT name, video_template, nfo_template FROM libraries ORDER BY name")).all()
        by_name = {row.name: row for row in rows}
        assert by_name["a"].video_template == "{mosaic?}/{def?}/{number}[-Part {cd?}][-{sub?}].{ext}"
        assert by_name["a"].nfo_template == "{mosaic?}/{number}.nfo"
        assert by_name["b"].video_template == "{studio}/{number}/{number}[-{sub?}].{ext}"

    engine.dispose()


def test_library_strm_content_template_backfilled_for_existing_rows(tmp_path: Path) -> None:
    """存量行迁移后 strm_content_template 为 NULL (写绝对路径)."""
    db_path = tmp_path / "migrate.db"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")

    command.upgrade(cfg, "54138fa1c160")

    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO libraries "
                "(name, path, automation, recursive, patterns, move_mode, video_template, write_nfo, "
                "copy_resources, trailer_pattern, blacklist_patterns, subtitle_extensions, min_file_size) "
                "VALUES ('t', '/m', 'SCRAPE', 1, '[]', 'MOVE', '{number}.{ext}', 1, "
                "'[\"thumb\"]', '(?i)trailer', '[]', '[\".srt\"]', 0)"
            )
        )

    command.upgrade(cfg, "head")

    with engine.connect() as conn:
        columns = {column["name"] for column in inspect(conn).get_columns("libraries")}
        assert "strm_content_template" in columns
        row = conn.execute(text("SELECT strm_content_template FROM libraries")).one()
        assert row.strm_content_template is None

    engine.dispose()
