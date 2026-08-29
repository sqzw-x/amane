"""更新请求模型 (req) · repo 入参 (TypedDict) · DB 模型 三者的兼容性保证.

设计背景见 docs/dev/data-model.md "可写面与兼容性". 安全性由三层共同保证, 本文件分别验证:

1. ``create_partial_model`` 的正确性 (``TestCreatePartialModel``)
   -- req model 全部由它派生, 故只要它正确, req↔DB 的字段/类型兼容性即由构造保证.
2. 字段纪律 (``TestFieldDiscipline``)
   -- req 字段 ⊆ repo TypedDict 字段 ⊆ DB 列; 只读/内部字段不出现在外部可写面.
   手写响应子集 ``@subset_of(..., covariant=)`` 导入时校验.
3. 序列化保真 (``TestRepoRoundTrip``)
   -- repo update 去反射后, 显式赋值的类型兼容性由静态检查保证; 此处用 fuzzy + DB 往返
      验证 JSON 列序列化, enum, datetime/tz 在真实数据库往返中保真 (静态检查覆盖不到的部分).
"""

import random
from collections.abc import AsyncGenerator, Callable
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, get_type_hints

import pytest
import pytest_asyncio
from pydantic import AfterValidator, BaseModel, Field, ValidationError
from pydantic.config import JsonDict
from random_generator import generate_random_value_for_type
from sqlalchemy import event
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import Field as SQLField
from sqlmodel import SQLModel

from amane.api.models import (
    FeedUpdateRequest,
    LibraryUpdateRequest,
    MediaFileUpdateRequest,
    OptionalPathTemplateDefaults,
    PartialMetadata,
    ScheduleUpdateRequest,
)
from amane.db import Feed, Library, MediaFile, Metadata, Repository, Schedule
from amane.db.models import RoutineType
from amane.db.repo_types import FeedUpdates, LibraryUpdates, MediaFileUpdates, MetadataFields, ScheduleUpdates
from amane.utils.model import assert_model_subset, create_partial_model
from tests.schema_template import copy_schema

# ============================================================================
# 1. create_partial_model 正确性
# ============================================================================


class _Plain(BaseModel):
    """普通 (非 table) BaseModel, 用于验证基础 partial 行为."""

    name: str
    count: int = 7
    tags: list[str] = Field(default_factory=list)


class _PlainTable(SQLModel, table=True):
    """table=True 模型, 用于验证 ignore_fields (依赖断开字段继承)."""

    __tablename__ = "_plain_table_for_partial_test"  # type: ignore[assignment]

    id: int | None = SQLField(default=None, primary_key=True)
    title: str
    score: int = 0
    note: str | None = None


def _partial_table(
    fields: tuple[str, ...] = (),
    ignore_fields: tuple[str, ...] = (),
    json_schema_extras: dict[str, JsonDict | Callable[[JsonDict], None]] | None = None,
) -> Any:
    return create_partial_model(_PlainTable, fields, ignore_fields=ignore_fields, json_schema_extras=json_schema_extras)


class TestCreatePartialModel:
    def test_all_fields_optional_with_none_default(self):
        """所有字段变为 Optional 且默认 None -- 这是 '部分更新' 语义的根基."""
        partial = create_partial_model(_Plain)
        hints = get_type_hints(partial)
        for name in _Plain.model_fields:
            assert name in hints, f"{name} 丢失"
            assert type(None) in (hints[name].__args__), f"{name} 未变为 Optional: {hints[name]}"
        inst = partial()
        for name in _Plain.model_fields:
            assert getattr(inst, name) is None, f"{name} 默认值应为 None"

    def test_partial_does_not_mutate_base(self):
        """派生不得污染源模型 (源字段的必填性/默认值保持原样)."""
        create_partial_model(_Plain)
        assert _Plain.model_fields["name"].is_required()
        assert _Plain.model_fields["count"].default == 7

    def test_lossless_roundtrip(self):
        """含全部字段的合法 dict 经 partial 校验后, dump 不丢字段且值不变.

        这是 req model 的核心契约: 前端传来的可写字段必须无损抵达 repo.
        """
        partial = create_partial_model(_Plain)
        data = {"name": "x", "count": 3, "tags": ["a", "b"]}
        dumped = partial.model_validate(data).model_dump(exclude_unset=True)
        assert dumped == data

    def test_partial_validation_still_enforced(self):
        """显式提供的字段仍需通过类型校验 (Optional 只是放宽 '可缺省', 非 '可乱填')."""
        partial = create_partial_model(_Plain)
        with pytest.raises(ValidationError):
            partial.model_validate({"count": "not-an-int"})

    def test_explicit_null_rejected_for_non_optional_source(self):
        """源列非 Optional 时, JSON 显式 null 不是 '不更新', 应 422."""
        partial = create_partial_model(_Plain)
        with pytest.raises(ValidationError):
            partial.model_validate({"name": None})
        with pytest.raises(ValidationError):
            partial.model_validate({"tags": None})
        omitted = partial.model_validate({})
        assert omitted.model_dump(exclude_unset=True) == {}

    def test_partial_keeps_public_schema_name(self):
        """生成模型名必须是公开名; 不能泄漏内部 mixin/locals, 否则 OpenAPI $ref 会漂."""
        partial = create_partial_model(_Plain, partial_cls_name="PlainUpdate")
        assert partial.__name__ == "PlainUpdate"
        schema = partial.model_json_schema()
        assert schema.get("title") == "PlainUpdate"
        assert "Guarded" not in str(schema)
        assert "RejectExplicitNull" not in str(schema)

    def test_explicit_null_allowed_for_optional_source(self):
        """源列本就可空时, 显式 null 表示清空."""
        partial = _partial_table(ignore_fields=("id",))
        inst = partial.model_validate({"note": None})
        assert inst.model_dump(exclude_unset=True) == {"note": None}

    def test_partial_preserves_after_validator(self):
        """源字段 Annotated AfterValidator 在 partial 上仍生效; 缺省 None 不跑校验."""

        def _must_ok(value: str) -> str:
            if value != "ok":
                raise ValueError("bad")
            return value

        class _Validated(SQLModel, table=True):
            __tablename__ = "_validated_for_partial"  # type: ignore[assignment]
            id: int | None = SQLField(default=None, primary_key=True)
            code: Annotated[str, AfterValidator(_must_ok)] = "ok"

        partial = create_partial_model(_Validated, ignore_fields=("id",))
        assert partial().code is None
        assert partial(code="ok").code == "ok"
        with pytest.raises(ValidationError):
            partial(code="nope")

    def test_partial_omitted_field_not_in_unset_dump(self):
        """未提供的字段不出现在 exclude_unset dump 中 -- 区分 '未改' 与 '改为 None'."""
        partial = create_partial_model(_Plain)
        inst = partial.model_validate({"name": "only"})
        assert set(inst.model_dump(exclude_unset=True)) == {"name"}

    # --- include 列表 (*fields) ---

    def test_include_list_limits_fields(self):
        """显式 *fields 仅保留列出的字段 (table 模型: 基类换为 SQLModel, 断开字段继承)."""
        partial = _partial_table(fields=("title",))
        assert set(partial.model_fields) == {"title"}

    # --- ignore_fields ---

    def test_ignore_fields_removed_on_table_model(self):
        """table 模型上, ignore_fields 中的字段从结果模型彻底消失."""
        partial = _partial_table(ignore_fields=("id", "score"))
        assert "id" not in partial.model_fields
        assert "score" not in partial.model_fields
        assert {"title", "note"} <= set(partial.model_fields)

    def test_ignored_field_cannot_leak_via_construction(self):
        """构造时传入被忽略字段不会写入 dump -- 阻断越权赋值 (如外部 POST id/raw)."""
        partial = _partial_table(ignore_fields=("id", "score"))
        inst = partial.model_validate({"title": "t", "id": 999, "score": 888})
        dumped = inst.model_dump()
        assert "id" not in dumped
        assert "score" not in dumped
        assert dumped["title"] == "t"

    def test_ignore_fields_rejected_on_non_table_model(self):
        """普通 BaseModel 经继承会泄漏被忽略字段, 必须显式报错而非静默泄漏."""
        with pytest.raises(ValueError, match="table=True"):
            create_partial_model(_Plain, ignore_fields=("count",))

    def test_ignore_fields_unknown_field_raises_value_error(self):
        """ignore_fields 中拼写错误或已重命名的字段立即报错, 防止静默泄漏只读字段."""
        with pytest.raises(ValueError, match="ignore_fields contains unknown field"):
            _partial_table(ignore_fields=("idd",))  # typo: idd instead of id

    def test_ignore_fields_unknown_field_message_includes_available_fields(self):
        """错误消息中列出可用字段, 帮助调用方快速定位."""
        with pytest.raises(ValueError, match="Available on _PlainTable"):
            _partial_table(ignore_fields=("nonexistent",))

    def test_table_instance_attrs_are_values_not_descriptors(self):
        """table 模型派生后, 实例属性是真实值而非 InstrumentedAttribute (基类换成 SQLModel)."""
        partial = _partial_table()
        inst = partial.model_validate({"title": "t"})
        assert inst.title == "t"  # 读到字符串, 而非 ORM 描述符
        assert inst.id is None

    # --- recursive ---

    def test_recursive_partializes_nested_models(self):
        """recursive=True 时嵌套 BaseModel 字段被递归 partial 化, 整体可无参构造.

        守护 PartialConfig (HotSettings 的递归 partial), 其依赖此行为渲染嵌套配置表单.
        """

        class Inner(BaseModel):
            a: int
            b: str = "x"

        class Outer(BaseModel):
            inner: Inner
            name: str = "n"

        partial = create_partial_model(Outer, recursive=True)
        inst = partial()  # 不抛异常: 嵌套必填字段 a 也已可选
        assert inst.inner is None
        nested = partial.model_fields["inner"].annotation
        nested_non_none = [a for a in nested.__args__ if a is not type(None)]
        assert issubclass(nested_non_none[0], BaseModel)
        assert not nested_non_none[0].model_fields["a"].is_required()

    # --- 退化边界 ---

    def test_ignore_all_fields_returns_base_unchanged(self):
        """忽略全部字段时无任何字段覆盖, 函数走 '无变更' 短路返回源类本身.

        这是已知退化分支 (``if not optional_fields: return base_cls``): 调用方若忽略掉所有字段,
        得到的是原模型而非空模型. 钉死此行为, 提醒勿用全忽略来 '清空' 模型.
        """
        partial = _partial_table(ignore_fields=("id", "title", "score", "note"))
        assert partial is _PlainTable

    # --- json_schema_extras ---

    def test_json_schema_extras_applied_to_field(self):
        """json_schema_extras 合并到指定字段的 JSON Schema 中."""
        partial = create_partial_model(_Plain, json_schema_extras={"name": {"format": "uri"}})
        schema = partial.model_json_schema()
        assert schema["properties"]["name"]["format"] == "uri"

    def test_json_schema_extras_merged_with_existing(self):
        """若源字段已有 json_schema_extra, 与传入的合并 (传入覆盖同名键)."""

        class M(BaseModel):
            url: str = Field(json_schema_extra={"ui:widget": "text"})

        partial = create_partial_model(M, json_schema_extras={"url": {"format": "uri"}})
        schema = partial.model_json_schema()
        props = schema["properties"]["url"]
        assert props.get("format") == "uri"
        assert props.get("ui:widget") == "text"

    def test_json_schema_extras_ignored_for_excluded_field(self):
        """json_schema_extras 指定的字段若被 ignore_fields 排除, 不生效也不报错."""
        partial = _partial_table(ignore_fields=("score",), json_schema_extras={"score": {"maximum": 100}})
        assert "score" not in partial.model_fields

    def test_json_schema_extras_unknown_field_raises_value_error(self):
        """json_schema_extras 中拼写错误的字段立即报错."""
        with pytest.raises(ValueError, match="json_schema_extras contains unknown field"):
            _partial_table(json_schema_extras={"nonexistent": {"format": "uri"}})

    def test_json_schema_extras_none_is_noop(self):
        """json_schema_extras=None 不改变行为."""
        partial_no = create_partial_model(_Plain)
        partial_with = create_partial_model(_Plain, json_schema_extras=None)
        assert partial_no.model_json_schema() == partial_with.model_json_schema()

    # --- extra_fields (非 DB 列扩展可写面) ---

    def test_extra_fields_added_and_partialized(self):
        """extra_fields 字段进入结果模型: 缺省 None, 可写入, 显式 null 被拒 (同不可空列)."""
        partial = create_partial_model(
            _PlainTable,
            ignore_fields=("id", "score", "note"),
            extra_fields={"aliases": Annotated[list[str], Field(description="别名行")]},
        )
        assert partial().aliases is None
        inst = partial.model_validate({"aliases": ["a", "b"]})
        assert inst.aliases == ["a", "b"]
        assert partial().model_dump(exclude_unset=True) == {}
        with pytest.raises(ValidationError):
            partial.model_validate({"aliases": None})
        schema = partial.model_json_schema()
        assert schema["properties"]["aliases"]["anyOf"] == [
            {"type": "array", "items": {"type": "string"}},
            {"type": "null"},
        ]
        assert schema["properties"]["aliases"]["description"] == "别名行"

    def test_extra_fields_rejects_existing_model_field(self):
        """extra_fields 与源字段重名立即报错, 防止静默覆盖."""
        with pytest.raises(ValueError, match="extra_fields contains existing model field"):
            create_partial_model(_Plain, extra_fields={"name": str})


# ============================================================================
# 2. 字段纪律: req ⊆ TypedDict ⊆ DB; 只读字段不外泄
# ============================================================================

# (req model, repo TypedDict, DB 模型, 禁止出现在外部可写面的字段)
_DISCIPLINE = [
    (
        MediaFileUpdateRequest,
        MediaFileUpdates,
        MediaFile,
        {"id", "library_id", "created_at", "updated_at", "content_type", "mosaic", "has_subtitle", "definition"},
    ),
    (LibraryUpdateRequest, LibraryUpdates, Library, {"id"}),
    (ScheduleUpdateRequest, ScheduleUpdates, Schedule, {"id", "last_run", "next_run"}),
    (PartialMetadata, MetadataFields, Metadata, {"id", "number", "created_at", "updated_at", "raw", "field_sources"}),
    (
        FeedUpdateRequest,
        FeedUpdates,
        Feed,
        {"id", "etag", "last_modified", "next_fetch_at", "last_fetched_at", "last_error", "last_enqueued"},
    ),
]
_DISCIPLINE_IDS = ["media", "library", "schedule", "metadata", "feed"]


class TestCovariantSubsetOfLibrary:
    def test_optional_path_defaults_are_covariant_not_contravariant(self):
        """缺省是产出: PathTemplate <: PathTemplate | None; 列上的 None 不能写进非空缺省."""
        assert_model_subset(OptionalPathTemplateDefaults, Library, covariant=True)
        with pytest.raises(ValueError, match="contravariant"):
            assert_model_subset(OptionalPathTemplateDefaults, Library, covariant=False)
        assert "link_template" not in OptionalPathTemplateDefaults.model_fields
        assert "video_template" not in OptionalPathTemplateDefaults.model_fields


class TestFieldDiscipline:
    @pytest.mark.parametrize(("req", "typed_dict", "db", "forbidden"), _DISCIPLINE, ids=_DISCIPLINE_IDS)
    def test_req_subset_of_typeddict(self, req, typed_dict, db, forbidden):
        """外部可写字段必须是 repo 入参的子集, 否则 model_dump 携带的键会被 repo 静默丢弃."""
        req_fields = set(req.model_fields)
        td_fields = set(get_type_hints(typed_dict))
        extra = req_fields - td_fields
        assert not extra, f"{req.__name__} 含 repo 无法接受的字段: {extra}"

    @pytest.mark.parametrize(("req", "typed_dict", "db", "forbidden"), _DISCIPLINE, ids=_DISCIPLINE_IDS)
    def test_typeddict_subset_of_db_columns(self, req, typed_dict, db, forbidden):
        """repo 入参字段必须都是真实 DB 列, 否则去反射后的显式赋值无法静态通过 (此处再兜底)."""
        td_fields = set(get_type_hints(typed_dict))
        db_fields = set(db.model_fields)
        unknown = td_fields - db_fields
        assert not unknown, f"{typed_dict.__name__} 含 {db.__name__} 不存在的列: {unknown}"

    @pytest.mark.parametrize(("req", "typed_dict", "db", "forbidden"), _DISCIPLINE, ids=_DISCIPLINE_IDS)
    def test_readonly_fields_not_externally_writable(self, req, typed_dict, db, forbidden):
        """只读/内部字段绝不出现在外部可写面 (req model)."""
        leaked = set(req.model_fields) & forbidden
        assert not leaked, f"{req.__name__} 越权暴露只读/内部字段: {leaked}"


# ============================================================================
# 3. 序列化保真: fuzzy + DB 往返
# ============================================================================


def _gen_updates(typed_dict: type, seed: int, skip: frozenset[str]) -> dict[str, Any]:
    """按 TypedDict 的精确注解为每个字段生成随机值.

    用 TypedDict (而非 DB 模型的裸 dict 列) 作为类型来源: 它声明了 JSON 字段的精确形状,
    避免裸 dict 生成出反序列化后形状漂移的值, 从而真正测到序列化保真.

    ``skip`` 内的字段不参与生成: 外键列 (值须指向真实行, 属 FK 完整性而非序列化保真)
    与语义校验字段 (域约束如 video_template 须闭合可选组, 属校验层职责而非保真,
    由各自的校验测试覆盖).
    """
    random.seed(seed)
    return {
        name: generate_random_value_for_type(tp) for name, tp in get_type_hints(typed_dict).items() if name not in skip
    }


async def _make_media(repo: Repository) -> int:
    # 服务端测试库启用 FK 约束, MediaFile.library_id 必须指向已存在的 Library.
    lib = await repo.create_library(name="seed", path="/seed/lib")
    assert lib.id is not None
    m = await repo.create_media_file(library_id=lib.id, path="/seed/orig.mp4", number="ORIG-000")
    assert m.id is not None
    return m.id


async def _make_library(repo: Repository) -> int:
    lib = await repo.create_library(name="seed", path="/seed/lib")
    assert lib.id is not None
    return lib.id


async def _make_schedule(repo: Repository) -> int:
    sched = await repo.create_schedule(cron="0 0 * * *", task_type=RoutineType.CLEANUP, payload={})
    assert sched.id is not None
    return sched.id


async def _make_metadata(repo: Repository) -> int:
    meta = await repo.upsert_metadata(number="SEED-000")
    assert meta.id is not None
    return meta.id


async def _make_feed(repo: Repository) -> int:
    feed = await repo.create_feed(name="seed", url="https://example.com/seed.xml")
    assert feed.id is not None
    return feed.id


# (TypedDict, 建种子记录, repo update 方法名, 跳过的外键列, 跳过的语义校验列)
_ROUNDTRIP = [
    (MediaFileUpdates, _make_media, "update_media_file", frozenset({"metadata_id"}), frozenset()),
    (
        LibraryUpdates,
        _make_library,
        "update_library",
        frozenset(),
        frozenset(
            {
                "subtitle_extensions",
                "video_template",
                "link_template",
                "thumb_template",
                "poster_template",
                "fanart_template",
                "extrafanart_template",
                "nfo_template",
                "trailer_template",
                "subtitle_template",
            }
        ),
    ),
    (ScheduleUpdates, _make_schedule, "update_schedule", frozenset(), frozenset()),
    (MetadataFields, _make_metadata, "update_metadata", frozenset(), frozenset()),
    (FeedUpdates, _make_feed, "update_feed", frozenset(), frozenset()),
]
_ROUNDTRIP_IDS = ["media", "library", "schedule", "metadata", "feed"]


@pytest_asyncio.fixture
async def repo(tmp_path: Path) -> AsyncGenerator[Repository]:
    """纯 repo 文件库 (FK ON), 不经 app lifespan.

    api/conftest 的 repo 来自 app runtime, 其 lifespan 启动的 FeedService 会
    并发 poll 测试新建的 Feed -- next_fetch_at 随机生成的是历史时刻, 立即到期,
    拉取失败后时间列被覆盖为当前时间, 与 DB 往返断言竞态 (全套件 -n auto 负载下
    必现). 序列化保真与后台服务无关, 此测试使用无后台服务的独立引擎.
    """
    db_path = tmp_path / "repo.db"
    copy_schema(db_path)
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", connect_args={"timeout": 5})

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=OFF")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    yield Repository(engine)
    await engine.dispose()


class TestRepoRoundTrip:
    """显式赋值 (去反射) 的类型兼容性由静态检查保证; 此处验证运行时序列化保真.

    JSON 列 (list/dict), enum, datetime/tz 经 SQLite 序列化与回读后必须与写入值一致 --
    这是静态类型检查覆盖不到, 唯一会在真实 DB 往返中暴露的不兼容点.
    """

    @pytest.mark.parametrize(
        ("typed_dict", "make_seed", "method_name", "skip", "validated"),
        _ROUNDTRIP,
        ids=_ROUNDTRIP_IDS,
    )
    @pytest.mark.parametrize("seed", range(8))
    @pytest.mark.asyncio(loop_scope="function")
    async def test_update_roundtrip_preserves_values(
        self, repo: Repository, typed_dict, make_seed, method_name, skip, validated, seed
    ):
        updates = _gen_updates(typed_dict, seed, skip | validated)
        record_id = await make_seed(repo)
        method = getattr(repo, method_name)

        result = await method(record_id, **updates)
        assert result is not None, f"{method_name}({record_id}) 返回 None"

        for key, expected in updates.items():
            actual = getattr(result, key)
            if isinstance(expected, datetime):
                # SQLite 回读为 naive datetime; 比较 UTC 时间点而非 tzinfo.
                actual_utc = actual.replace(tzinfo=None) if actual.tzinfo else actual
                expected_utc = expected.replace(tzinfo=None)
                assert actual_utc == expected_utc, f"{method_name}.{key}: {actual!r} != {expected!r}"
            else:
                assert actual == expected, f"{method_name}.{key}: {actual!r} != {expected!r}"
