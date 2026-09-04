"""JavDB 演員栏按名字后的 ♀ / ♂ 标记拆性别."""

from parsel import Selector

from amane.crawlers.models import FilmActor
from amane.crawlers.sites.javdb import _parse_actors
from amane.enums import ActorGender

_HTML = """
<div class="panel-block">
  <strong>演員:</strong>
  <span class="value">
    <a href="/actors/a">女优A</a><strong class="symbol female">♀</strong>
    <a href="/actors/b">男优B</a><strong class="symbol male">♂</strong>
    <a href="/actors/c">未标记C</a>
  </span>
</div>
"""


def test_parse_actors_pairs_trailing_gender_marker() -> None:
    assert _parse_actors(Selector(text=_HTML)) == [
        FilmActor(name="女优A", gender=ActorGender.FEMALE),
        FilmActor(name="男优B", gender=ActorGender.MALE),
        FilmActor(name="未标记C", gender=ActorGender.UNKNOWN),
    ]


def test_parse_actors_missing_row() -> None:
    assert _parse_actors(Selector(text="<div></div>")) == []
