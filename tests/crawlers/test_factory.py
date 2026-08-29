"""爬虫工厂测试"""

from typing import TYPE_CHECKING, cast

import pytest

from amane.crawlers.base import Crawler, CrawlerProfile
from amane.crawlers.factory import CrawlerFactory
from amane.crawlers.registry import registry

if TYPE_CHECKING:
    from amane.enums import SiteName


class _FakeCrawler(Crawler):
    """用于测试的假爬虫 - 只记录构造次数, 不做实际抓取."""

    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(name=cast("SiteName", "fake_test_site"), base_url="https://fake.example.com")

    def __init__(self, client, config=None):
        super().__init__(client, config=config)
        self._constructed = True

    async def _search(self, query, options=None):
        return None

    async def _scrape(self, url, options=None):
        return None


@pytest.fixture(autouse=True)
def _register_fake_crawler():
    """注册假爬虫并在测试后清理."""
    registry.register(_FakeCrawler)
    yield
    registry._crawlers.pop("fake_test_site", None)


class TestCrawlerFactoryGet:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_creates_instance_on_first_call(self, http_client):
        factory = CrawlerFactory(http_client)
        crawler = await factory.get("fake_test_site")
        assert crawler is not None
        assert isinstance(crawler, _FakeCrawler)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_returns_cached_instance_on_second_call(self, http_client):
        factory = CrawlerFactory(http_client)
        c1 = await factory.get("fake_test_site")
        c2 = await factory.get("fake_test_site")
        assert c1 is c2

    @pytest.mark.asyncio(loop_scope="function")
    async def test_returns_none_for_unregistered_name(self, http_client):
        factory = CrawlerFactory(http_client)
        result = await factory.get("nonexistent_site")
        assert result is None


class TestCrawlerFactoryGetCrawlers:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_returns_only_registered_crawlers(self, http_client):
        factory = CrawlerFactory(http_client)
        result = await factory.get_crawlers(["fake_test_site", "nonexistent"])
        assert len(result) == 1
        assert "fake_test_site" in result
        assert "nonexistent" not in result

    @pytest.mark.asyncio(loop_scope="function")
    async def test_empty_list(self, http_client):
        factory = CrawlerFactory(http_client)
        result = await factory.get_crawlers([])
        assert result == {}

    @pytest.mark.asyncio(loop_scope="function")
    async def test_all_registered(self, http_client):
        factory = CrawlerFactory(http_client)
        result = await factory.get_crawlers(["fake_test_site"])
        assert len(result) == 1


class TestCrawlerFactoryActiveCrawlers:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_empty_initial(self, http_client):
        factory = CrawlerFactory(http_client)
        assert factory.active_crawlers == {}

    @pytest.mark.asyncio(loop_scope="function")
    async def test_reflects_instantiated_crawlers(self, http_client):
        factory = CrawlerFactory(http_client)
        await factory.get("fake_test_site")
        active = factory.active_crawlers
        assert "fake_test_site" in active
        assert isinstance(active["fake_test_site"], _FakeCrawler)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_returns_copy_not_reference(self, http_client):
        factory = CrawlerFactory(http_client)
        await factory.get("fake_test_site")
        active = factory.active_crawlers
        active["new_key"] = None  # type: ignore[assignment]
        assert "new_key" not in factory.active_crawlers


@pytest.mark.asyncio(loop_scope="function")
async def test_javdb_film_and_actor_are_distinct_instances(http_client):
    from amane.crawlers.actor.sites.javdb import JavDBActorCrawler
    from amane.crawlers.sites.javdb import JavDBCrawler

    factory = CrawlerFactory(http_client)
    film = await factory.get("javdb")
    actor = await factory.get_actor("javdb")
    assert isinstance(film, JavDBCrawler)
    assert isinstance(actor, JavDBActorCrawler)
    assert film is not actor


@pytest.mark.asyncio(loop_scope="function")
async def test_theporndb_film_and_actor_are_distinct_instances(http_client):
    from amane.crawlers.actor.sites.theporndb import ThePornDBActorCrawler
    from amane.crawlers.sites.theporndb import ThePornDBCrawler

    factory = CrawlerFactory(http_client)
    film = await factory.get("theporndb")
    actor = await factory.get_actor("theporndb")
    assert isinstance(film, ThePornDBCrawler)
    assert isinstance(actor, ThePornDBActorCrawler)
    assert film is not actor
