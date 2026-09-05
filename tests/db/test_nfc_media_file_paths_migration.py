"""迁移: MediaFile.path 改为 NFC, 同一 NFC 路径只留一行."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text


def test_nfc_media_file_paths_rewrites_and_merges(tmp_path: Path) -> None:
    db_path = tmp_path / "migrate.db"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")

    command.upgrade(cfg, "9d6cb1706ad1")

    nfc = "/video/\u3058.mp4"
    nfd = "/video/\u3057\u3099.mp4"
    other_nfd = "/other/\u3057\u3099.mp4"
    other_nfc = "/other/\u3058.mp4"
    assert nfc != nfd

    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO libraries "
                "(name, path, automation, recursive, patterns, move_mode, link_mode, video_template, "
                "write_nfo, copy_resources, trailer_pattern, blacklist_patterns, subtitle_extensions, "
                "min_file_size) "
                "VALUES ('t', '/m', 'SCRAPE', 1, '[]', 'MOVE', 'STRM', '{number}.{ext}', 1, "
                "'[]', '', '[]', '[]', 0)"
            )
        )
        lib_id = conn.execute(text("SELECT id FROM libraries")).scalar_one()
        conn.execute(
            text(
                "INSERT INTO media_files "
                "(path, status, library_id, created_at, updated_at, content_type, has_subtitle) VALUES "
                "(:nfd, 'PENDING', :lib, '2026-01-01 00:00:00', '2026-01-01 00:00:00', 'WESTERN', 0), "
                "(:nfc, 'SCRAPED', :lib, '2026-01-01 00:00:00', '2026-01-01 00:00:00', 'WESTERN', 0), "
                "(:other, 'PENDING', :lib, '2026-01-01 00:00:00', '2026-01-01 00:00:00', 'WESTERN', 0)"
            ),
            {"nfd": nfd, "nfc": nfc, "other": other_nfd, "lib": lib_id},
        )

    command.upgrade(cfg, "head")

    with engine.connect() as conn:
        rows = conn.execute(text("SELECT path, status FROM media_files ORDER BY path")).all()

    assert {(row.path, row.status) for row in rows} == {(nfc, "SCRAPED"), (other_nfc, "PENDING")}

    engine.dispose()
