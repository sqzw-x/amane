"""存量 StrEnum 列: value → 成员名, ORM 读出真实枚举."""

from pathlib import Path

from alembic import command
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
from tests.helpers import alembic_config

_PREVIOUS = "1ed95d44b077"
_TARGET = "05ee7dd4c06d"


def test_strenum_columns_value_to_name(tmp_path: Path) -> None:
    db_path = tmp_path / "migrate.db"
    cfg = alembic_config(db_path)

    command.upgrade(cfg, _PREVIOUS)

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

    command.upgrade(cfg, _TARGET)

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

    # 只选枚举列: 整行映射会带上后续 revision 才加的列
    with Session(engine) as session:
        automation, move_mode, link_mode = session.exec(
            select(Library.automation, Library.move_mode, Library.link_mode)
        ).one()
        assert automation is LibraryAutomation.SCRAPE
        assert move_mode is MoveMode.MOVE
        assert link_mode is LinkMode.STRM
        assert session.exec(select(Actor.gender)).one() is ActorGender.FEMALE
        kind, action = session.exec(select(FacetRule.kind, FacetRule.action)).one()
        assert kind is FacetKind.ACTOR
        assert action is FacetRuleAction.ALIAS
        feeds = dict(session.exec(select(Feed.name, Feed.content_type)).all())
        assert feeds["f"] is ContentType.WESTERN
        assert feeds["bad"] is None
        assert session.exec(select(AgentSession.status)).one() is AgentSessionStatus.ACTIVE
        assert session.exec(select(SavedQuery.entity)).one() is SavedQueryEntity.METADATA

    engine.dispose()
