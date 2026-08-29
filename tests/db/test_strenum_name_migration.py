"""存量 StrEnum 列: value → 成员名, ORM 读出真实枚举."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlmodel import Session, select

from amane.db.models import (
    Actor,
    AgentSession,
    AgentSessionStatus,
    FacetKind,
    FacetRule,
    FacetRuleAction,
    Feed,
    Library,
    SavedQuery,
    SavedQueryEntity,
)
from amane.enums import ActorGender, LibraryAutomation, LinkMode, MoveMode
from amane.parsing import ContentType


def test_strenum_columns_value_to_name(tmp_path: Path) -> None:
    db_path = tmp_path / "migrate.db"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")

    command.upgrade(cfg, "1ed95d44b077")

    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO libraries "
                "(name, path, automation, recursive, patterns, move_mode, link_mode, video_template, "
                "write_nfo, copy_resources, trailer_pattern, blacklist_patterns, subtitle_extensions, "
                "min_file_size) "
                "VALUES ('t', '/m', 'scrape', 1, '[]', 'move', 'strm', '{number}.{ext}', 1, "
                "'[]', '', '[]', '[]', 0)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO actors (name, gender, image_urls, provider_ids, source_urls, "
                "field_sources, raw, created_at, updated_at) "
                "VALUES ('A', 'female', '[]', '{}', '{}', '{}', '{}', "
                "'2026-01-01 00:00:00', '2026-01-01 00:00:00')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO facet_rules (kind, source_name, action, created_at, updated_at) "
                "VALUES ('actor', 'Old', 'alias', '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
            )
        )
        conn.execute(text("UPDATE facet_rules SET target_name = 'New' WHERE source_name = 'Old'"))
        conn.execute(
            text(
                'INSERT INTO feeds (name, url, "group", enabled, auto_enqueue, interval_seconds, '
                "content_type, use_cache, last_enqueued) "
                "VALUES ('f', 'https://example.test/rss', '', 1, 1, 3600, 'western', "
                "'[\"metadata\"]', 0)"
            )
        )
        conn.execute(
            text(
                'INSERT INTO feeds (name, url, "group", enabled, auto_enqueue, interval_seconds, '
                "content_type, use_cache, last_enqueued) "
                "VALUES ('bad', 'https://example.test/bad', '', 1, 1, 3600, 'not-a-type', "
                "'[]', 0)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO agent_sessions (title, status, created_at, updated_at) "
                "VALUES ('s', 'active', '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO saved_queries (name, sql, entity, persisted, created_at, updated_at) "
                "VALUES ('q', 'SELECT 1', 'metadata', 0, '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
            )
        )

    command.upgrade(cfg, "head")

    with engine.connect() as conn:
        lib = conn.execute(text("SELECT automation, move_mode, link_mode FROM libraries")).one()
        assert lib == ("SCRAPE", "MOVE", "STRM")
        assert conn.execute(text("SELECT gender FROM actors")).scalar_one() == "FEMALE"
        rule = conn.execute(text("SELECT kind, action FROM facet_rules")).one()
        assert rule == ("ACTOR", "ALIAS")
        types = {row.name: row.content_type for row in conn.execute(text("SELECT name, content_type FROM feeds")).all()}
        assert types["f"] == "WESTERN"
        assert types["bad"] is None
        assert conn.execute(text("SELECT status FROM agent_sessions")).scalar_one() == "ACTIVE"
        assert conn.execute(text("SELECT entity FROM saved_queries")).scalar_one() == "METADATA"

    with Session(engine) as session:
        library = session.exec(select(Library)).one()
        assert library.automation is LibraryAutomation.SCRAPE
        assert library.move_mode is MoveMode.MOVE
        assert library.link_mode is LinkMode.STRM
        actor = session.exec(select(Actor)).one()
        assert actor.gender is ActorGender.FEMALE
        rule_orm = session.exec(select(FacetRule)).one()
        assert rule_orm.kind is FacetKind.ACTOR
        assert rule_orm.action is FacetRuleAction.ALIAS
        feeds = {row.name: row for row in session.exec(select(Feed)).all()}
        assert feeds["f"].content_type is ContentType.WESTERN
        assert feeds["bad"].content_type is None
        session_row = session.exec(select(AgentSession)).one()
        assert session_row.status is AgentSessionStatus.ACTIVE
        query = session.exec(select(SavedQuery)).one()
        assert query.entity is SavedQueryEntity.METADATA

    engine.dispose()
