"""MediaFile 文件相位列: 存量 path 回填."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlmodel import Session, col, select

from amane.db.models import MediaFile
from amane.parsing import ContentType, Mosaic


def test_media_file_phase_columns_backfill_from_path(tmp_path: Path) -> None:
    db_path = tmp_path / "migrate.db"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")

    command.upgrade(cfg, "c3d8f95d2198")

    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO libraries "
                "(name, path, automation, recursive, patterns, move_mode, video_template, write_nfo, "
                "copy_resources, trailer_pattern, blacklist_patterns, subtitle_extensions, min_file_size) "
                "VALUES ('t', '/m', 'scrape', 1, '[]', 'move', '{studio}/{number}/{number}.{ext}', 1, "
                "'[\"thumb\"]', '(?i)trailer', '[]', '[\".srt\"]', 0)"
            )
        )
        lib_id = conn.execute(text("SELECT id FROM libraries")).scalar_one()
        conn.execute(
            text(
                "INSERT INTO media_files (path, status, library_id, created_at, updated_at) VALUES "
                "('/media/MIDV-123-UC-4K.mp4', 'PENDING', :lib, '2026-01-01 00:00:00', '2026-01-01 00:00:00'), "
                "('/media/HEYZO-1234.mp4', 'PENDING', :lib, '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
            ),
            {"lib": lib_id},
        )

    command.upgrade(cfg, "head")

    with engine.connect() as conn:
        columns = {column["name"] for column in inspect(conn).get_columns("media_files")}
        assert {"content_type", "mosaic", "has_subtitle", "definition"} <= columns
        rows = {
            row.path: row
            for row in conn.execute(
                text("SELECT path, content_type, mosaic, has_subtitle, definition FROM media_files")
            ).all()
        }
        midv = rows["/media/MIDV-123-UC-4K.mp4"]
        assert midv.content_type == "censored"
        assert midv.mosaic == "uncensored"
        assert midv.has_subtitle in (1, True)
        assert midv.definition == "4K"
        heyzo = rows["/media/HEYZO-1234.mp4"]
        assert heyzo.content_type == "uncensored"
        assert heyzo.mosaic is None
        assert heyzo.has_subtitle in (0, False)
        assert heyzo.definition is None

    with Session(engine) as session:
        loaded = {
            row.path: row for row in session.exec(select(MediaFile).where(col(MediaFile.path).like("/media/%"))).all()
        }
        midv_orm = loaded["/media/MIDV-123-UC-4K.mp4"]
        assert midv_orm.content_type == ContentType.CENSORED
        assert midv_orm.mosaic == Mosaic.UNCENSORED
        heyzo_orm = loaded["/media/HEYZO-1234.mp4"]
        assert heyzo_orm.content_type == ContentType.UNCENSORED
        assert heyzo_orm.mosaic is None

    engine.dispose()
