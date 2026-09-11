"""FeedItem ignored_at schema migration."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError


@pytest.fixture
def alembic_cfg(tmp_path: Path) -> Config:
    db_path = tmp_path / "migrate.db"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


def test_feed_item_ignore_state_migration_preserves_history(alembic_cfg: Config) -> None:
    command.upgrade(alembic_cfg, "a0271ff198e5")

    url = alembic_cfg.get_main_option("sqlalchemy.url")
    assert url is not None
    engine = create_engine(url)
    with engine.begin() as conn:
        feed_id = conn.execute(
            text(
                "INSERT INTO feeds "
                "(name, url, enabled, auto_enqueue, interval_seconds, use_cache, last_enqueued) "
                "VALUES ('Feed', 'https://example.com/feed.xml', 1, 1, 3600, '[]', 0)"
            )
        ).lastrowid
        conn.execute(
            text(
                "INSERT INTO feed_items (feed_id, item_key, title, link, number, created_at) "
                "VALUES (:feed_id, 'item-1', 'Title', 'https://example.com/item-1', 'AAA-001', CURRENT_TIMESTAMP)"
            ),
            {"feed_id": feed_id},
        )

    command.upgrade(alembic_cfg, "head")

    with engine.connect() as conn:
        columns = {column["name"] for column in inspect(conn).get_columns("feed_items")}
        assert "ignored_at" in columns
        row = conn.execute(text("SELECT item_key, ignored_at FROM feed_items")).one()
        assert row == ("item-1", None)
        assert "published_at" in columns

    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO feed_items (feed_id, item_key, created_at) VALUES (:feed_id, 'item-1', CURRENT_TIMESTAMP)"
            ),
            {"feed_id": feed_id},
        )

    engine.dispose()


def test_feed_item_list_indexes_migration(alembic_cfg: Config) -> None:
    command.upgrade(alembic_cfg, "02e6ea96c41f")

    url = alembic_cfg.get_main_option("sqlalchemy.url")
    assert url is not None
    engine = create_engine(url)
    with engine.begin() as conn:
        feed_id = conn.execute(
            text(
                "INSERT INTO feeds "
                "(name, url, enabled, auto_enqueue, interval_seconds, use_cache, last_enqueued) "
                "VALUES ('Feed', 'https://example.com/list.xml', 1, 1, 3600, '[]', 0)"
            )
        ).lastrowid
        conn.execute(
            text(
                "INSERT INTO feed_items (feed_id, item_key, title, created_at) "
                "VALUES (:feed_id, 'keep-me', 'Title', CURRENT_TIMESTAMP)"
            ),
            {"feed_id": feed_id},
        )
        before = {
            row[0]
            for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='feed_items'"))
        }
        assert "ix_feed_items_ignored_at" not in before
        assert "ix_feed_items_list_order" not in before

    command.upgrade(alembic_cfg, "head")

    with engine.connect() as conn:
        after = {
            row[0]
            for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='feed_items'"))
        }
        assert "ix_feed_items_ignored_at" in after
        assert "ix_feed_items_list_order" in after
        sql = conn.execute(text("SELECT sql FROM sqlite_master WHERE name='ix_feed_items_list_order'")).scalar_one()
        assert sql is not None
        assert "coalesce(published_at, created_at)" in sql
        assert conn.execute(text("SELECT item_key FROM feed_items")).scalar_one() == "keep-me"

    command.downgrade(alembic_cfg, "02e6ea96c41f")
    with engine.connect() as conn:
        reverted = {
            row[0]
            for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='feed_items'"))
        }
        assert "ix_feed_items_ignored_at" not in reverted
        assert "ix_feed_items_list_order" not in reverted

    engine.dispose()


def test_feed_item_read_at_migration_preserves_history(alembic_cfg: Config) -> None:
    command.upgrade(alembic_cfg, "a2e19df6190f")

    url = alembic_cfg.get_main_option("sqlalchemy.url")
    assert url is not None
    engine = create_engine(url)
    with engine.begin() as conn:
        feed_id = conn.execute(
            text(
                "INSERT INTO feeds "
                "(name, url, enabled, auto_enqueue, interval_seconds, use_cache, last_enqueued) "
                "VALUES ('Feed', 'https://example.com/read.xml', 1, 1, 3600, '[]', 0)"
            )
        ).lastrowid
        conn.execute(
            text(
                "INSERT INTO feed_items (feed_id, item_key, title, created_at) "
                "VALUES (:feed_id, 'keep-me', 'Title', CURRENT_TIMESTAMP)"
            ),
            {"feed_id": feed_id},
        )
        before = {column["name"] for column in inspect(conn).get_columns("feed_items")}
        assert "read_at" not in before

    command.upgrade(alembic_cfg, "head")

    with engine.connect() as conn:
        columns = {column["name"] for column in inspect(conn).get_columns("feed_items")}
        assert "read_at" in columns
        row = conn.execute(text("SELECT item_key, read_at FROM feed_items")).one()
        assert row == ("keep-me", None)
        indexes = {
            row[0]
            for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='feed_items'"))
        }
        assert "ix_feed_items_read_at" in indexes

    command.downgrade(alembic_cfg, "a2e19df6190f")
    with engine.connect() as conn:
        reverted = {column["name"] for column in inspect(conn).get_columns("feed_items")}
        assert "read_at" not in reverted

    engine.dispose()
