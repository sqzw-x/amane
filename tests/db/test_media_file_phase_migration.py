"""文件相位列与条目类型列: 存量 path 回填."""

from pathlib import Path

from alembic import command
from sqlalchemy import Engine, create_engine, inspect, text
from sqlmodel import Session, col, select

from amane.db.models import MediaFile
from amane.parsing import Mosaic
from tests.helpers import alembic_config

_LIBRARY_INSERT = (
    "INSERT INTO libraries "
    "(name, path, automation, recursive, patterns, move_mode, link_mode, video_template, "
    "write_nfo, copy_resources, trailer_pattern, blacklist_patterns, subtitle_extensions, min_file_size) "
    "VALUES ('t', '/m', 'SCRAPE', 1, '[]', 'MOVE', 'STRM', '{number}.{ext}', 1, '[]', '', '[]', '[]', 0)"
)

_MEDIA_INSERT = (
    "INSERT INTO media_files (path, status, library_id, created_at, updated_at, content_type, mosaic, metadata_id) "
    "VALUES (:path, 'PENDING', :lib, '2026-01-01 00:00:00', '2026-01-01 00:00:00', :content_type, :mosaic, :metadata_id)"
)


def _seed_library(conn) -> int:
    conn.execute(text(_LIBRARY_INSERT))
    return conn.execute(text("SELECT id FROM libraries")).scalar_one()


def _seed_metadata(conn, number: str) -> int:
    conn.execute(
        text(
            "INSERT INTO metadata (number, created_at, updated_at) "
            "VALUES (:number, '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
        ),
        {"number": number},
    )
    return conn.execute(text("SELECT id FROM metadata WHERE number = :number"), {"number": number}).scalar_one()


def _columns(engine: Engine, table: str) -> set[str]:
    with engine.connect() as conn:
        return {column["name"] for column in inspect(conn).get_columns(table)}


def _file_phase(engine: Engine) -> dict[str, tuple[str | None, bool, str | None]]:
    with engine.connect() as conn:
        return {
            row.path: (row.mosaic, bool(row.has_subtitle_in_name), row.definition)
            for row in conn.execute(
                text("SELECT path, mosaic, has_subtitle_in_name, definition FROM media_files")
            ).all()
        }


def test_media_file_phase_columns_backfill_from_path(tmp_path: Path) -> None:
    db_path = tmp_path / "migrate.db"
    cfg = alembic_config(db_path)

    command.upgrade(cfg, "c3d8f95d2198")

    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        lib_id = _seed_library(conn)
        conn.execute(
            text(
                "INSERT INTO media_files (path, status, library_id, created_at, updated_at) VALUES "
                "('/media/MIDV-123-UC-4K.mp4', 'PENDING', :lib, '2026-01-01 00:00:00', '2026-01-01 00:00:00'), "
                "('/media/HEYZO-1234.mp4', 'PENDING', :lib, '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
            ),
            {"lib": lib_id},
        )

    command.upgrade(cfg, "head")

    assert {"mosaic", "has_subtitle_in_name", "definition"} <= _columns(engine, "media_files")
    assert "content_type" not in _columns(engine, "media_files")
    assert _file_phase(engine) == {
        "/media/MIDV-123-UC-4K.mp4": ("CRACKED", True, "4K"),
        "/media/HEYZO-1234.mp4": (None, False, None),
    }

    with Session(engine) as session:
        loaded = {
            row.path: row for row in session.exec(select(MediaFile).where(col(MediaFile.path).like("/media/%"))).all()
        }
        assert loaded["/media/MIDV-123-UC-4K.mp4"].mosaic is Mosaic.CRACKED
        assert loaded["/media/HEYZO-1234.mp4"].mosaic is None

    engine.dispose()


def test_media_file_mosaic_type_fill_cleared(tmp_path: Path) -> None:
    """按类型回填的有码 / 无码在升级后清掉, 来路标记保留."""
    db_path = tmp_path / "migrate.db"
    cfg = alembic_config(db_path)

    command.upgrade(cfg, "668e214b1a76")

    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        lib_id = _seed_library(conn)
        for path, content_type, mosaic in [
            ("/media/HEYZO-1234.mp4", "UNCENSORED", None),
            ("/media/HEYZO-1234-流出.mp4", "UNCENSORED", "LEAKED"),
            ("/media/MIDV-123.mp4", "CENSORED", None),
            ("/media/MIDV-123-无码.mp4", "CENSORED", "UNCENSORED"),
            ("/media/MD0165-1.mp4", "CHINESE", None),
        ]:
            conn.execute(
                text(_MEDIA_INSERT),
                {
                    "path": path,
                    "lib": lib_id,
                    "content_type": content_type,
                    "mosaic": mosaic,
                    "metadata_id": None,
                },
            )

    command.upgrade(cfg, "head")

    assert _file_phase(engine) == {
        "/media/HEYZO-1234.mp4": (None, False, None),
        "/media/HEYZO-1234-流出.mp4": ("LEAKED", False, None),
        "/media/MIDV-123.mp4": (None, False, None),
        "/media/MIDV-123-无码.mp4": ("UNCENSORED", False, None),
        "/media/MD0165-1.mp4": (None, False, None),
    }

    engine.dispose()


def _entry_types(engine: Engine) -> dict[str, str]:
    with engine.connect() as conn:
        return {
            row.number: row.content_type
            for row in conn.execute(text("SELECT number, content_type FROM metadata")).all()
        }


def _file_types(engine: Engine) -> dict[str, str]:
    with engine.connect() as conn:
        return {
            row.path: row.content_type for row in conn.execute(text("SELECT path, content_type FROM media_files")).all()
        }


def test_metadata_content_type_backfilled_from_files_then_number(tmp_path: Path) -> None:
    """条目类型优先取首个挂载文件的路径, 没有文件时取番号; 回填可重跑."""
    db_path = tmp_path / "migrate.db"
    cfg = alembic_config(db_path)

    command.upgrade(cfg, "1fdf4d41aac1")

    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        lib_id = _seed_library(conn)
        by_file = _seed_metadata(conn, "東京熱 k1393")
        _seed_metadata(conn, "K1363")
        _seed_metadata(conn, "MIDV-123")
        _seed_metadata(conn, "PLAIN-1")
        # 路径能解析出无码番号: 条目类型跟随路径, 不跟随这个识别不出的番号
        conn.execute(
            text(_MEDIA_INSERT),
            {
                "path": "/media/HEYZO-1234.mp4",
                "lib": lib_id,
                "content_type": "WESTERN",
                "mosaic": None,
                "metadata_id": by_file,
            },
        )
        # 未绑定条目的文件: downgrade 时按路径重解析
        conn.execute(
            text(_MEDIA_INSERT),
            {
                "path": "/media/MD0165-1.mp4",
                "lib": lib_id,
                "content_type": "CENSORED",
                "mosaic": None,
                "metadata_id": None,
            },
        )
        # 只取 id 最小的那一份: 后挂的欧美路径不参与回填
        conn.execute(
            text(_MEDIA_INSERT),
            {
                "path": "/media/欧美/HEYZO-1234.mp4",
                "lib": lib_id,
                "content_type": "WESTERN",
                "mosaic": None,
                "metadata_id": by_file,
            },
        )

    command.upgrade(cfg, "head")

    assert _entry_types(engine) == {
        "東京熱 k1393": "UNCENSORED",
        "K1363": "UNCENSORED",
        "MIDV-123": "CENSORED",
        "PLAIN-1": "WESTERN",
    }
    assert "content_type" not in _columns(engine, "media_files")

    # downgrade 把条目类型放回文件列: 绑定文件取所属条目
    command.downgrade(cfg, "1fdf4d41aac1")
    assert _file_types(engine) == {
        "/media/HEYZO-1234.mp4": "UNCENSORED",
        "/media/欧美/HEYZO-1234.mp4": "UNCENSORED",
        "/media/MD0165-1.mp4": "CHINESE",
    }

    # 回填可重跑
    command.upgrade(cfg, "head")
    assert _entry_types(engine) == {
        "東京熱 k1393": "UNCENSORED",
        "K1363": "UNCENSORED",
        "MIDV-123": "CENSORED",
        "PLAIN-1": "WESTERN",
    }

    engine.dispose()
