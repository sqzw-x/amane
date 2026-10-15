"""MediaFile 文件相位列: 存量 path 回填."""

from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import Engine, create_engine, inspect, text
from sqlmodel import Session, col, select

from amane.db.models import MediaFile
from amane.parsing import ContentType, Mosaic
from tests.helpers import alembic_config


def test_media_file_phase_columns_backfill_from_path(tmp_path: Path) -> None:
    db_path = tmp_path / "migrate.db"
    cfg = alembic_config(db_path)

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
        assert {"content_type", "mosaic", "has_subtitle_in_name", "definition"} <= columns
        rows = {
            row.path: row
            for row in conn.execute(
                text("SELECT path, content_type, mosaic, has_subtitle_in_name, definition FROM media_files")
            ).all()
        }
        midv = rows["/media/MIDV-123-UC-4K.mp4"]
        assert midv.content_type == "CENSORED"
        assert midv.mosaic == "CRACKED"
        assert midv.has_subtitle_in_name in (1, True)
        assert midv.definition == "4K"
        heyzo = rows["/media/HEYZO-1234.mp4"]
        assert heyzo.content_type == "UNCENSORED"
        assert heyzo.mosaic == "UNCENSORED"
        assert heyzo.has_subtitle_in_name in (0, False)
        assert heyzo.definition is None

    with Session(engine) as session:
        loaded = {
            row.path: row for row in session.exec(select(MediaFile).where(col(MediaFile.path).like("/media/%"))).all()
        }
        midv_orm = loaded["/media/MIDV-123-UC-4K.mp4"]
        assert midv_orm.content_type is ContentType.CENSORED
        assert midv_orm.mosaic is Mosaic.CRACKED
        heyzo_orm = loaded["/media/HEYZO-1234.mp4"]
        assert heyzo_orm.content_type is ContentType.UNCENSORED
        assert heyzo_orm.mosaic is Mosaic.UNCENSORED

    engine.dispose()


def test_media_file_mosaic_reprojected_from_path(tmp_path: Path) -> None:
    db_path = tmp_path / "migrate.db"
    cfg = alembic_config(db_path)

    command.upgrade(cfg, "c1334030b9f1")

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
                "(path, status, library_id, created_at, updated_at, content_type, mosaic, has_subtitle) VALUES "
                "('/media/MIDV-123-U.mp4', 'PENDING', :lib, '2026-01-01 00:00:00', '2026-01-01 00:00:00', "
                "'CENSORED', 'UNCENSORED', 0), "
                "('/media/MIDV-123-无码.mp4', 'PENDING', :lib, '2026-01-01 00:00:00', '2026-01-01 00:00:00', "
                "'CENSORED', 'UNCENSORED', 0), "
                "('/media/MIDV-123-無碼破解.mp4', 'PENDING', :lib, '2026-01-01 00:00:00', '2026-01-01 00:00:00', "
                "'CENSORED', 'UNCENSORED', 0)"
            ),
            {"lib": lib_id},
        )

    command.upgrade(cfg, "head")

    with engine.connect() as conn:
        rows = {
            row.path: row
            for row in conn.execute(text("SELECT path, mosaic, has_subtitle_in_name FROM media_files")).all()
        }
        assert rows["/media/MIDV-123-U.mp4"].mosaic == "CRACKED"
        assert rows["/media/MIDV-123-无码.mp4"].mosaic == "UNCENSORED"
        assert rows["/media/MIDV-123-無碼破解.mp4"].mosaic == "CRACKED"

    engine.dispose()


def test_media_file_mosaic_filled_from_content_type(tmp_path: Path) -> None:
    db_path = tmp_path / "migrate.db"
    cfg = alembic_config(db_path)

    command.upgrade(cfg, "668e214b1a76")

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
                "(path, status, library_id, created_at, updated_at, content_type, mosaic, has_subtitle) VALUES "
                "('/media/HEYZO-1234.mp4', 'PENDING', :lib, '2026-01-01 00:00:00', '2026-01-01 00:00:00', "
                "'UNCENSORED', NULL, 0), "
                "('/media/HEYZO-1234-流出.mp4', 'PENDING', :lib, '2026-01-01 00:00:00', '2026-01-01 00:00:00', "
                "'UNCENSORED', 'LEAKED', 0), "
                "('/media/MIDV-123.mp4', 'PENDING', :lib, '2026-01-01 00:00:00', '2026-01-01 00:00:00', "
                "'CENSORED', NULL, 0), "
                "('/media/MD0165-1.mp4', 'PENDING', :lib, '2026-01-01 00:00:00', '2026-01-01 00:00:00', "
                "'CHINESE', NULL, 0)"
            ),
            {"lib": lib_id},
        )

    command.upgrade(cfg, "head")

    with engine.connect() as conn:
        rows = {row.path: row for row in conn.execute(text("SELECT path, mosaic FROM media_files")).all()}
        assert rows["/media/HEYZO-1234.mp4"].mosaic == "UNCENSORED"
        assert rows["/media/HEYZO-1234-流出.mp4"].mosaic == "LEAKED"
        assert rows["/media/MIDV-123.mp4"].mosaic == "CENSORED"
        assert rows["/media/MD0165-1.mp4"].mosaic is None

    engine.dispose()


def _type_and_mosaic(engine: Engine) -> dict[str, tuple[str, str | None]]:
    with engine.connect() as conn:
        return {
            row.path: (row.content_type, row.mosaic)
            for row in conn.execute(text("SELECT path, content_type, mosaic FROM media_files")).all()
        }


def _seed_rows(db_path: Path, rows: list[tuple[str, _Phase]]) -> None:
    """建库并写入旧 schema 的媒体文件行, 只给相位两列, 其余列取默认."""
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
        for path, (content_type, mosaic) in rows:
            conn.execute(
                text(
                    "INSERT INTO media_files "
                    "(path, status, library_id, created_at, updated_at, content_type, mosaic) "
                    "VALUES (:path, 'PENDING', :lib, '2026-01-01 00:00:00', '2026-01-01 00:00:00', "
                    ":content_type, :mosaic)"
                ),
                {"path": path, "lib": lib_id, "content_type": content_type, "mosaic": mosaic},
            )
    engine.dispose()


#: 相位: 内容类型与马赛克标记.
_Phase = tuple[str, str | None]

#: 父 revision, 与每个文件行的 (路径, 升级前写入的相位, 升级后应有的相位).
#: 两处相位相同表示该行不在谓词内, 重算应当原样跳过.
_ReprojectCase = tuple[str, list[tuple[str, _Phase, _Phase]]]

REPROJECT_CASES: list[_ReprojectCase] = [
    (
        "4f36abe6b903",
        [
            ("/media/KIN8-0236.mp4", ("UNCENSORED", "UNCENSORED"), ("WESTERN", None)),
            ("/media/KIN8TENGOKU-4121-U.mp4", ("UNCENSORED", "LEAKED"), ("WESTERN", "CRACKED")),
            ("/media/MIDV-123.mp4", ("UNCENSORED", "CRACKED"), ("UNCENSORED", "CRACKED")),
        ],
    ),
    (
        "8dbd0fa0905e",
        [
            ("/media/SMBD-005.mp4", ("CENSORED", "CENSORED"), ("UNCENSORED", "UNCENSORED")),
            ("/media/GACHIG-256.mp4", ("CENSORED", "CENSORED"), ("UNCENSORED", "UNCENSORED")),
            ("/media/K1363.mp4", ("WESTERN", None), ("UNCENSORED", "UNCENSORED")),
            ("/media/k1393/clip.mp4", ("WESTERN", None), ("UNCENSORED", "UNCENSORED")),
            ("/media/n1234.mp4", ("CENSORED", None), ("CENSORED", None)),
            ("/media/MIDV-123.mp4", ("UNCENSORED", "CRACKED"), ("UNCENSORED", "CRACKED")),
        ],
    ),
    (
        "ea2ffb776110",
        [
            ("/media/DRGBD-045.mp4", ("CENSORED", "CENSORED"), ("UNCENSORED", "UNCENSORED")),
            ("/media/DRGBD046.mp4", ("CENSORED", None), ("UNCENSORED", "UNCENSORED")),
            ("/media/drgbd-047/clip.mp4", ("CENSORED", "CENSORED"), ("UNCENSORED", "UNCENSORED")),
            ("/media/SMDV-028.mp4", ("CENSORED", "CENSORED"), ("UNCENSORED", "UNCENSORED")),
            ("/media/smdv029.mp4", ("CENSORED", None), ("UNCENSORED", "UNCENSORED")),
            ("/media/DRG-123.mp4", ("UNCENSORED", "UNCENSORED"), ("UNCENSORED", "UNCENSORED")),
            ("/media/MIDV-123.mp4", ("UNCENSORED", "CRACKED"), ("UNCENSORED", "CRACKED")),
        ],
    ),
]


@pytest.mark.parametrize(
    ("parent", "rows"),
    REPROJECT_CASES,
    ids=["kin8", "uncensored-prefix-and-tokyo-hot", "drgbd-and-smdv"],
)
def test_media_file_reprojected_from_path(parent: str, rows: list[tuple[str, _Phase, _Phase]], tmp_path: Path) -> None:
    """存量行按当前规则重算相位; 谓词之外的行保持原值, 重跑结果相同."""
    db_path = tmp_path / "migrate.db"
    cfg = alembic_config(db_path)

    command.upgrade(cfg, parent)
    _seed_rows(db_path, [(path, before) for path, before, _ in rows])
    command.upgrade(cfg, "head")

    engine = create_engine(f"sqlite:///{db_path}")
    assert _type_and_mosaic(engine) == {path: after for path, _, after in rows}

    # downgrade 留空 (数据迁移不可逆), 再次升级不改变已写入的值
    command.downgrade(cfg, parent)
    command.upgrade(cfg, "head")
    assert _type_and_mosaic(engine) == {path: after for path, _, after in rows}

    engine.dispose()
