"""迁移回填: 从 initial schema 升级后重建分类投影."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from tests.helpers import alembic_config


@pytest.fixture
def alembic_cfg(tmp_path: Path) -> Config:
    return alembic_config(tmp_path / "migrate.db")


def test_facet_backfill_from_metadata_json(alembic_cfg: Config, tmp_path: Path) -> None:
    """旧库仅有 metadata JSON 列时, upgrade head 后应生成 actors/tags 等投影."""
    command.upgrade(alembic_cfg, "3a0088a88ce7")

    url = alembic_cfg.get_main_option("sqlalchemy.url")
    assert url is not None
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO metadata (number, title, actors, directors, tags, studio, publisher, series, "
                "poster_urls, thumb_urls, trailer_urls, extrafanart_urls, scores, external_ids, source_urls, "
                "field_sources, raw, created_at, updated_at) "
                "VALUES (:number, :title, :actors, :directors, :tags, :studio, :publisher, :series, "
                " :poster_urls, :thumb_urls, :trailer_urls, :extrafanart_urls, :scores, :external_ids, "
                " :source_urls, :field_sources, :raw, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {
                "number": "MIG-001",
                "title": "Migrate Me",
                "actors": json.dumps(["Alice", "Bob"]),
                "directors": json.dumps(["Dir"]),
                "tags": json.dumps(["tagA"]),
                "studio": "StudioM",
                "publisher": "PubM",
                "series": "SeriesM",
                "poster_urls": "[]",
                "thumb_urls": "[]",
                "trailer_urls": "[]",
                "extrafanart_urls": "{}",
                "scores": "{}",
                "external_ids": "{}",
                "source_urls": "{}",
                "field_sources": "{}",
                "raw": "{}",
            },
        )

    command.upgrade(alembic_cfg, "head")

    with engine.connect() as conn:
        actors = conn.execute(text("SELECT name FROM actors ORDER BY name")).fetchall()
        assert [r[0] for r in actors] == ["Alice", "Bob"]
        links = conn.execute(text("SELECT COUNT(*) FROM metadata_actors")).scalar()
        assert links == 2
        tags = conn.execute(text("SELECT name FROM tags")).fetchall()
        assert [r[0] for r in tags] == ["tagA"]
        studios = conn.execute(text("SELECT name FROM studios")).fetchall()
        assert [r[0] for r in studios] == ["StudioM"]
        # 用户注解表存在且为空
        assert conn.execute(text("SELECT COUNT(*) FROM user_tags")).scalar() == 0
        assert conn.execute(text("SELECT COUNT(*) FROM actor_user_tags")).scalar() == 0
        assert conn.execute(text("SELECT COUNT(*) FROM comments")).scalar() == 0

    engine.dispose()
