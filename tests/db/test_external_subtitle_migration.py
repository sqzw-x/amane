"""外挂字幕列: 拆分文件名标记与新增列, 存量行不回填.

回填要逐目录读取磁盘, 媒体库位于网盘挂载点时整条迁移链会被阻塞数分钟以上且无法分批续跑,
因此该列为空值由库扫描补齐 — 本测试固定这一契约.
"""

from pathlib import Path

from alembic import command
from sqlalchemy import create_engine, inspect, text

from tests.helpers import alembic_config

_INSERT_LIBRARY = (
    "INSERT INTO libraries "
    "(name, path, automation, recursive, patterns, move_mode, video_template, write_nfo, "
    "copy_resources, trailer_pattern, blacklist_patterns, subtitle_extensions, min_file_size) "
    "VALUES (:name, :path, 'SCRAPE', 1, '[]', 'MOVE', '{number}.{ext}', 1, "
    "'[]', '', '[]', :extensions, 0)"
)


def _insert_library(conn, name: str, path: Path, extensions: str) -> int:
    conn.execute(text(_INSERT_LIBRARY), {"name": name, "path": str(path), "extensions": extensions})
    return conn.execute(text("SELECT id FROM libraries ORDER BY id DESC LIMIT 1")).scalar_one()


def _insert_media_files(conn, library_id: int, rows: list[tuple[str, int]]) -> None:
    for path, has_subtitle in rows:
        conn.execute(
            text(
                "INSERT INTO media_files "
                "(path, status, library_id, created_at, updated_at, content_type, mosaic, has_subtitle) VALUES "
                "(:path, 'PENDING', :lib, '2026-01-01 00:00:00', '2026-01-01 00:00:00', 'CENSORED', 'CENSORED', :sub)"
            ),
            {"path": path, "lib": library_id, "sub": has_subtitle},
        )


def test_external_subtitle_no_backfill(tmp_path: Path) -> None:
    db_path = tmp_path / "migrate.db"
    cfg = alembic_config(db_path)

    library_dir = tmp_path / "library"
    library_dir.mkdir()
    external = library_dir / "MIDV-001.mp4"
    external.touch()
    # 同目录确有字幕: 迁移仍不读取磁盘, 该行保持假值.
    (library_dir / "MIDV-001.chs.srt").touch()
    named = library_dir / "MIDV-002-C.mp4"
    named.touch()
    plain_dir = tmp_path / "other"
    plain_dir.mkdir()
    plain = plain_dir / "MIDV-003-C.mp4"
    plain.touch()

    command.upgrade(cfg, "1f050b272f7d")

    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        library_id = _insert_library(conn, "open", library_dir, '[".srt"]')
        _insert_media_files(conn, library_id, [(str(external), 0), (str(named), 1), (str(plain), 1)])

    command.upgrade(cfg, "head")

    with engine.connect() as conn:
        columns = {column["name"] for column in inspect(conn).get_columns("media_files")}
        assert {"has_subtitle_in_name", "has_external_subtitle"} <= columns
        assert "has_subtitle" not in columns
        rows = {
            row.path: row
            for row in conn.execute(
                text("SELECT path, has_subtitle_in_name, has_external_subtitle FROM media_files")
            ).all()
        }
        # 文件名标记仍是 path 的投影, 逐行沿用改名前的值.
        assert rows[str(external)].has_subtitle_in_name in (0, False)
        assert rows[str(named)].has_subtitle_in_name in (1, True)
        assert rows[str(plain)].has_subtitle_in_name in (1, True)
        assert not any(row.has_external_subtitle for row in rows.values())

    command.downgrade(cfg, "1f050b272f7d")

    with engine.connect() as conn:
        columns = {column["name"] for column in inspect(conn).get_columns("media_files")}
        assert "has_subtitle" in columns
        assert "has_subtitle_in_name" not in columns
        assert "has_external_subtitle" not in columns
        rows = {row.path: row for row in conn.execute(text("SELECT path, has_subtitle FROM media_files")).all()}
        assert rows[str(named)].has_subtitle in (1, True)
        assert rows[str(external)].has_subtitle in (0, False)

    engine.dispose()
