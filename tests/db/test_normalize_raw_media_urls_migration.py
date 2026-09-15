"""迁移: Metadata.raw 中 poster_url/thumb_url/trailer_url → 列表字段."""

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


def test_normalize_metadata_raw_media_url_keys(alembic_cfg: Config) -> None:
    """旧 raw 单数字段在 upgrade 后变为 *_urls 列表; 已是列表的站点不动."""
    command.upgrade(alembic_cfg, "6d6ec00ff658")

    url = alembic_cfg.get_main_option("sqlalchemy.url")
    assert url is not None
    engine = create_engine(url)

    legacy_raw = {
        "javdb": {
            "number": "MIG-RAW-1",
            "title": "Legacy",
            "thumb_url": "https://example.com/thumb.jpg",
            "poster_url": None,
            "trailer_url": "",
            "extrafanart": ["https://example.com/1.jpg"],
        },
        "dmm": {
            "number": "MIG-RAW-1",
            "poster_urls": ["https://example.com/already.jpg"],
            "thumb_urls": [],
            "trailer_urls": [],
        },
    }

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
                "number": "MIG-RAW-1",
                "title": "Raw Migrate",
                "actors": "[]",
                "directors": "[]",
                "tags": "[]",
                "studio": None,
                "publisher": None,
                "series": None,
                "poster_urls": "[]",
                "thumb_urls": "[]",
                "trailer_urls": "[]",
                "extrafanart_urls": "{}",
                "scores": "{}",
                "external_ids": "{}",
                "source_urls": "{}",
                "field_sources": "{}",
                "raw": json.dumps(legacy_raw),
            },
        )

    command.upgrade(alembic_cfg, "head")

    with engine.connect() as conn:
        raw_s = conn.execute(text("SELECT raw FROM metadata WHERE number = 'MIG-RAW-1'")).scalar()
    assert raw_s is not None
    raw = json.loads(raw_s) if isinstance(raw_s, str) else raw_s

    javdb = raw["javdb"]
    assert "thumb_url" not in javdb
    assert "poster_url" not in javdb
    assert "trailer_url" not in javdb
    assert javdb["thumb_urls"] == ["https://example.com/thumb.jpg"]
    assert javdb["poster_urls"] == []
    assert javdb["trailer_urls"] == []
    assert javdb["extrafanart"] == ["https://example.com/1.jpg"]

    dmm = raw["dmm"]
    assert dmm["poster_urls"] == ["https://example.com/already.jpg"]
    assert dmm["thumb_urls"] == []
    assert "thumb_url" not in dmm

    engine.dispose()
