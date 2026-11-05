"""Library organize defaults + trailer_pattern schema migration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect, text

from tests.helpers import alembic_config


def test_library_organize_columns_backfill_existing_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "migrate.db"
    cfg = alembic_config(db_path)

    previous, target = "e08b11d79fbb", "27c1cec6341f"
    command.upgrade(cfg, previous)

    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO libraries (name, path, watch_enabled, recursive, patterns, move_mode, video_template) "
                "VALUES ('t', '/m', 1, 1, '[]', 'move', '{studio}/{number}/{number}.{ext}')"
            )
        )

    command.upgrade(cfg, target)

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
    cfg = alembic_config(db_path)

    previous, target = "27c1cec6341f", "1159ff536a74"
    command.upgrade(cfg, previous)

    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO libraries (name, path, watch_enabled, recursive, patterns, move_mode, video_template) "
                "VALUES ('t', '/m', 1, 1, 'null', 'move', '{studio}/{number}/{number}.{ext}')"
            )
        )

    command.upgrade(cfg, target)

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
    cfg = alembic_config(db_path)

    previous, target = "1159ff536a74", "02e6ea96c41f"
    command.upgrade(cfg, previous)

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

    command.upgrade(cfg, target)

    with engine.connect() as conn:
        columns = {column["name"] for column in inspect(conn).get_columns("libraries")}
        assert "automation" in columns
        assert "watch_enabled" not in columns
        rows = {
            str(row["name"]): str(row["automation"])
            for row in conn.execute(text("SELECT name, automation FROM libraries")).mappings()
        }
        # 成员名落库由后续 revision 改写, 这里仍是小写值
        assert rows == {"on": "scrape", "off": "none"}

    engine.dispose()


def test_library_path_template_optional_groups_rewrites_and_drops_cd_suffix(tmp_path: Path) -> None:
    """v0.5.0 存量: mosaic/definition 改名, 分集后缀并进 video_template, 并补 subtitle 可选组."""
    db_path = tmp_path / "migrate.db"
    cfg = alembic_config(db_path)

    previous, target = "099436e749d6", "c3d8f95d2198"
    command.upgrade(cfg, previous)

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

    command.upgrade(cfg, target)

    with engine.connect() as conn:
        columns = {column["name"] for column in inspect(conn).get_columns("libraries")}
        assert "cd_suffix_template" not in columns
        rows = conn.execute(text("SELECT name, video_template, nfo_template FROM libraries ORDER BY name")).all()
        by_name = {row.name: row for row in rows}
        assert by_name["a"].video_template == "{mosaic?}/{def?}/{number}[-Part {cd?}][-{sub?}].{ext}"
        assert by_name["a"].nfo_template == "{mosaic?}/{number}.nfo"
        assert by_name["b"].video_template == "{studio}/{number}/{number}[-{sub?}].{ext}"

    engine.dispose()


_LIBRARY_ROW: dict[str, object] = {
    "name": "t",
    "path": "/m",
    "automation": "scrape",
    "recursive": 1,
    "patterns": "[]",
    "move_mode": "move",
    "video_template": "{number}.{ext}",
    "write_nfo": 1,
    "copy_resources": '["thumb"]',
    "trailer_pattern": "(?i)trailer",
}


@pytest.mark.parametrize(
    ("previous", "target", "extra", "expected"),
    [
        pytest.param("fd00ba796b91", "cbede59bbb9e", {}, {"blacklist_patterns": "[]"}, id="blacklist_patterns"),
        pytest.param(
            "cbede59bbb9e",
            "b2ce3c344b5a",
            {"blacklist_patterns": "[]"},
            {"subtitle_extensions": '[".srt", ".ass", ".ssa", ".vtt", ".sub"]'},
            id="subtitle_extensions",
        ),
        pytest.param(
            "b2ce3c344b5a",
            "1702cd5270c9",
            {"blacklist_patterns": "[]", "subtitle_extensions": '[".srt"]'},
            # 成员名落库由后续 revision 改写, 这里仍是小写值
            {"link_template": None, "link_mode": "strm"},
            id="link_template_and_mode",
        ),
        pytest.param(
            "1702cd5270c9",
            "099436e749d6",
            {"blacklist_patterns": "[]", "subtitle_extensions": '[".srt"]'},
            {"min_file_size": 0},
            id="min_file_size",
        ),
        pytest.param(
            "54138fa1c160",
            "9d6cb1706ad1",
            {
                "automation": "SCRAPE",
                "move_mode": "MOVE",
                "blacklist_patterns": "[]",
                "subtitle_extensions": '[".srt"]',
                "min_file_size": 0,
            },
            {"strm_content_template": None},
            id="strm_content_template",
        ),
    ],
)
def test_library_added_column_backfills_existing_rows(
    tmp_path: Path, previous: str, target: str, extra: dict[str, object], expected: dict[str, object]
) -> None:
    """新增列对存量行取列默认值; 插入只给前驱 revision 已有的列."""
    db_path = tmp_path / "migrate.db"
    cfg = alembic_config(db_path)
    command.upgrade(cfg, previous)

    row = {**_LIBRARY_ROW, **extra}
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        conn.execute(
            text(f"INSERT INTO libraries ({', '.join(row)}) VALUES ({', '.join(f':{key}' for key in row)})"), row
        )

    command.upgrade(cfg, target)

    with engine.connect() as conn:
        actual = conn.execute(text(f"SELECT {', '.join(expected)} FROM libraries")).mappings().one()
    assert dict(actual) == expected

    engine.dispose()


def test_library_mosaic_placeholders_gain_censored_mapping(tmp_path: Path) -> None:
    """存量 `{mosaic?}` 补 censored 映射; 已写 censored 或无该占位符的模板不改."""
    db_path = tmp_path / "migrate.db"
    cfg = alembic_config(db_path)

    previous, target = "0980003004e2", "a2e19df6190f"
    command.upgrade(cfg, previous)

    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO libraries "
                "(name, path, automation, recursive, patterns, move_mode, link_mode, video_template, "
                "write_nfo, copy_resources, trailer_pattern, blacklist_patterns, subtitle_extensions, "
                "min_file_size, nfo_template, subtitle_template, strm_content_template) "
                "VALUES ('a', '/m', 'SCRAPE', 1, '[]', 'MOVE', 'STRM', "
                "'{mosaic?}/{number}[-{mosaic?|cracked=U}].{ext}', 1, "
                "'[]', '', '[]', '[]', 0, '{mosaic?|uncensored=无码}/{number}.nfo', "
                "'{mosaic?}/{raw_srt_name}.{ext}', '{mosaic?}/{video_relpath}')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO libraries "
                "(name, path, automation, recursive, patterns, move_mode, link_mode, video_template, "
                "write_nfo, copy_resources, trailer_pattern, blacklist_patterns, subtitle_extensions, "
                "min_file_size, nfo_template) "
                "VALUES ('b', '/n', 'SCRAPE', 1, '[]', 'MOVE', 'STRM', "
                "'{studio}/{number}/{number}.{ext}', 1, "
                "'[]', '', '[]', '[]', 0, '{mosaic?|censored=有码}/{number}.nfo')"
            )
        )

    command.upgrade(cfg, target)

    with engine.connect() as conn:
        rows = {
            row.name: row
            for row in conn.execute(
                text(
                    "SELECT name, video_template, nfo_template, subtitle_template, strm_content_template FROM libraries"
                )
            ).all()
        }
        assert rows["a"].video_template == "{mosaic?|censored=}/{number}[-{mosaic?|cracked=U,censored=}].{ext}"
        assert rows["a"].nfo_template == "{mosaic?|uncensored=无码,censored=}/{number}.nfo"
        assert rows["a"].subtitle_template == "{mosaic?|censored=}/{raw_srt_name}.{ext}"
        assert rows["a"].strm_content_template == "{mosaic?|censored=}/{video_relpath}"
        assert rows["b"].video_template == "{studio}/{number}/{number}.{ext}"
        assert rows["b"].nfo_template == "{mosaic?|censored=有码}/{number}.nfo"

    engine.dispose()
