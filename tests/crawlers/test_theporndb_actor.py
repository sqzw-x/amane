"""theporndb 演员爬虫单元测试: 应答投影与别名清洗.

输入取自 TPDB 上的真实字段值. 本站的别名与主名都有数据缺陷: 把同一个人此前的名字拼接为一条
(``Himari Kinoshita Himari Hanazawa``), 或附带批量导入的编号 (``Satin Sat008`` / ``Mina ID1927``).
清洗只丢弃这几类可判定的缺陷, 日文姓名顺序的另一种写法 (``Sasaki Aki``) 与无法切分的长别名照旧保留.
"""

from __future__ import annotations

from typing import Any

import pytest

from amane.crawlers.actor.sites.theporndb import performer_to_metadata

# (id, 应答, 期望别名)
ALIAS_CASES: list[tuple[str, dict[str, Any], list[str]]] = [
    # 木下ひまり (4d9d80aa): 后两条是 name 与 Himari Hanazawa 两个名字的拼接
    (
        "名字拼接项丢弃",
        {
            "id": "4d9d80aa-0db7-4ea2-ab58-b6f0bd10a612",
            "name": "Himari Kinoshita",
            "aliases": [
                "Himari Hanazawa",
                "Himari Kinoshita Himari Hanazawa",
                "Kinoshita Himari Hanazawa Himari",
            ],
        },
        ["Himari Hanazawa"],
    ),
    # 森沢かな (22fd7572): 一条是 name 与 Kanako Ioka 的拼接, 另一条只是换序写法;
    # 两个片段都在已知名字里时拼接才判得出来, 这里两份词序写法都保留
    (
        "换序写法各留一条",
        {
            "id": "22fd7572-e898-4f58-a0a4-225561d7326b",
            "name": "Kana Morisawa",
            "aliases": ["Kana Morisawa Kanako Ioka", "Kanako Iioka", "Iioka Kanako", "Kanako Ioka"],
        },
        ["Kana Morisawa Kanako Ioka", "Kanako Iioka", "Iioka Kanako", "Kanako Ioka"],
    ),
    # 日文姓名顺序的另一种写法是两个站之间实际用来匹配的名字, 不能当作重复丢弃
    (
        "别名与主名全等丢弃",
        {"id": "x", "name": "Sakura Kurumi", "aliases": ["Sakura Kurumi"]},
        [],
    ),
    (
        "两种词序都保留",
        {"id": "x", "name": "Rin Oka", "aliases": ["Rin Natsuki", "Natsuki Rin"]},
        ["Rin Natsuki", "Natsuki Rin"],
    ),
    ("姓名换序别名保留-日文", {"id": "x", "name": "Aki Sasaki", "aliases": ["Sasaki Aki"]}, ["Sasaki Aki"]),
    (
        "姓名换序别名保留-单字姓",
        {"id": "x", "name": "Shi Reeves", "aliases": ["Reeves Shi"]},
        ["Reeves Shi"],
    ),
    # 换序写法与重复拼接并存时, 只丢拼接项
    (
        "换序保留而重复拼接丢弃",
        {"id": "x", "name": "Rei Mizuna", "aliases": ["Mizuna Rei", "Rei Mizuna Rei Mizuna"]},
        ["Mizuna Rei"],
    ),
    # 主名自己就是重复词 (TPDB 上存在 ``AIKA AIKA`` / ``Himari Himari`` 这类条目)
    (
        "主名重复词只影响别名",
        {"id": "x", "name": "Himari Himari", "aliases": []},
        [],
    ),
    ("重复主名作为别名丢弃", {"id": "x", "name": "Hitomi Tanaka", "aliases": ["Hitomi Hitomi Tanaka"]}, []),
    (
        "法语介词不算重复",
        {"id": "x", "name": "Cruz De La Vega", "aliases": ["Cruz De La Vega De Paris"]},
        ["Cruz De La Vega De Paris"],
    ),
    (
        "主名重复拼接丢弃",
        {"id": "x", "name": "Ai Li", "aliases": ["Ai Li Ai Li"]},
        [],
    ),
    # 批量导入的编号后缀: 底名与主名一致时删除编号, 编号来自其他记录时整条丢弃
    ("导入编号底名一致", {"id": "x", "name": "Satin", "aliases": ["Satin Sat008"]}, []),
    ("导入编号底名是别人", {"id": "x", "name": "Mina Luxx", "aliases": ["Mina ID1927"]}, []),
    ("导入编号无 ID 前缀", {"id": "x", "name": "Nicole", "aliases": ["Nicole Nic053"]}, []),
    # 真用户名里的数字不构成编号形态, 一律保留
    (
        "数字用户名保留",
        {"id": "x", "name": "dazey2003", "aliases": ["Dreamy2003", "Dazeydreamer2003"]},
        ["Dreamy2003", "Dazeydreamer2003"],
    ),
    ("leet 写法保留", {"id": "x", "name": "Rosebud", "aliases": ["Ros3bl00d"]}, ["Ros3bl00d"]),
    # 无法切分的长别名可能是真名或头衔, 保留
    (
        "法语姓氏长别名保留",
        {"id": "x", "name": "Ava White", "aliases": ["Ava White De Paris"]},
        ["Ava White De Paris"],
    ),
    (
        "头衔前缀别名保留",
        {"id": "x", "name": "Valerie August", "aliases": ["Ms Valerie August"]},
        ["Ms Valerie August"],
    ),
    ("无别名", {"id": "x", "name": "Ai Uehara", "aliases": []}, []),
    ("别名与主名全等不入表", {"id": "x", "name": "Ai Uehara", "aliases": ["Ai Uehara"]}, []),
    ("别名表结构不符", {"id": "x", "name": "Ai Uehara", "aliases": None}, []),
]
ALIAS_IDS = [case[0] for case in ALIAS_CASES]


@pytest.mark.parametrize(("case_id", "perf", "expected"), ALIAS_CASES, ids=ALIAS_IDS)
def test_alias_projection(case_id: str, perf: dict[str, Any], expected: list[str]) -> None:
    metadata = performer_to_metadata(perf)
    assert metadata is not None
    assert metadata.aliases == expected


@pytest.mark.parametrize("perf", [{"id": "x", "name": ""}, {"id": "x", "name": None}, {"id": "x"}])
def test_missing_name_rejected(perf: dict[str, Any]) -> None:
    assert performer_to_metadata(perf) is None
