from collections.abc import Callable
from datetime import UTC, datetime
from types import UnionType
from typing import Annotated, Any, Optional, Self, Union, get_args, get_origin, no_type_check

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    Field,
    PlainValidator,
    WrapValidator,
    create_model,
    model_validator,
)
from pydantic.config import JsonDict
from pydantic.fields import FieldInfo
from sqlmodel import SQLModel

_ANNOTATED_VALIDATORS = (AfterValidator, BeforeValidator, PlainValidator, WrapValidator)


def _none_skipping(
    validators: tuple[AfterValidator | BeforeValidator | PlainValidator | WrapValidator, ...],
) -> tuple[AfterValidator | BeforeValidator | PlainValidator | WrapValidator, ...]:
    """Partial 字段默认 None; 把 Annotated 校验器包一层, 空值不跑源校验."""
    wrapped: list[AfterValidator | BeforeValidator | PlainValidator | WrapValidator] = []
    for meta in validators:
        if isinstance(meta, AfterValidator):
            inner = meta.func

            def _after(value: Any, *, _inner: Any = inner) -> Any:
                return value if value is None else _inner(value)

            wrapped.append(AfterValidator(_after))
        elif isinstance(meta, BeforeValidator):
            inner = meta.func

            def _before(value: Any, *, _inner: Any = inner) -> Any:
                return value if value is None else _inner(value)

            wrapped.append(BeforeValidator(_before))
        elif isinstance(meta, PlainValidator):
            inner = meta.func

            def _plain(value: Any, *, _inner: Any = inner) -> Any:
                return value if value is None else _inner(value)

            wrapped.append(PlainValidator(_plain))
        else:
            wrapped.append(meta)
    return tuple(wrapped)


_MISSING = object()


def _allows_none(annotation: Any) -> bool:
    """类型是否包含 None (Optional / T | None / Annotated 包装)."""
    origin = get_origin(annotation)
    if origin is Annotated:
        args = get_args(annotation)
        return bool(args) and _allows_none(args[0])
    if origin in (Union, UnionType):
        return any(arg is type(None) for arg in get_args(annotation))
    return annotation is type(None)


def to_resp[T: BaseModel](t: type[T], model: SQLModel) -> T:
    """将 ORM model 转换为 Response model.

    自动将 naive datetime 字段标记为 UTC (SQLite 读回的 datetime 无 tzinfo).
    ORM 上不存在的响应字段跳过, 由 Response model 默认值填充 (如 file_count).
    ORM 为 NULL 且响应字段非 Optional、有默认值时同样跳过 (JSON 列读出 None).
    """
    data = {}
    for field_name, field_info in t.model_fields.items():
        value = getattr(model, field_name, _MISSING)
        if value is _MISSING:
            continue
        if value is None and not field_info.is_required() and not _allows_none(field_info.annotation):
            continue
        if isinstance(value, datetime) and value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        data[field_name] = value
    return t.model_validate(data)


def kv(d: JsonDict) -> Callable[[JsonDict], None]:
    """
    用于简化 dict 类型的 schema 注入.

    以 "v-" 为前缀的键注入到 "additionalProperties" 中 (dict 类型的值字段).
    其他键直接注入到 schema 顶层 (适用于普通字段).
    """

    def inner(schema: JsonDict) -> None:
        vprops = schema.get("additionalProperties")
        vprops = vprops if isinstance(vprops, dict) else {}
        for k, v in d.items():
            if k.startswith("v-"):
                vprops[k.removeprefix("v-")] = v
            else:
                schema[k] = v

    return inner


def anyof_extras(extras: JsonDict | Callable[[JsonDict], None]) -> Callable[[JsonDict], None]:
    """
    用于简化 X | None 类型的 schema 注入.

    这种类型会生成 anyOf: [{type: "null"}, {...}] 的 schema 结构,
    此函数搜索 Schema 第一个非 null 分支并注入额外字段.
    """

    def inner(schema: JsonDict) -> None:
        anyof = schema.get("anyOf")
        if isinstance(anyof, list) and len(anyof) == 2:
            non_null_schema = next((s for s in anyof if isinstance(s, dict) and s.get("type") != "null"), None)
            if non_null_schema is not None:
                if isinstance(extras, dict):
                    non_null_schema.update(extras)
                else:
                    extras(non_null_schema)

    return inner


def _unwrap_annotated(annotation: Any) -> Any:
    origin = get_origin(annotation)
    if origin is Annotated:
        args = get_args(annotation)
        return _unwrap_annotated(args[0]) if args else annotation
    return annotation


def _is_subtype(src: Any, dest: Any) -> bool:
    """``src`` 能否用在需要 ``dest`` 的位置 (src <: dest). ``Annotated`` 只作元数据, 按内层比."""
    src = _unwrap_annotated(src)
    dest = _unwrap_annotated(dest)
    if src is dest or src == dest:
        return True
    src_origin = get_origin(src)
    dest_origin = get_origin(dest)
    if src_origin in (Union, UnionType):
        return all(_is_subtype(m, dest) for m in get_args(src))
    if dest_origin in (Union, UnionType):
        return any(_is_subtype(src, m) for m in get_args(dest))
    return False


def _assert_names_subset(subset: type[BaseModel], base: type[BaseModel]) -> None:
    extra = set(subset.model_fields) - set(base.model_fields)
    if extra:
        raise ValueError(f"{subset.__name__} has fields not on {base.__name__}: {extra}")


def assert_model_subset(subset: type[BaseModel], base: type[BaseModel], *, covariant: bool) -> None:
    """字段名 ⊆ ``base``. ``covariant``: 产出 ``subset.T <: base.T``; 否则消费 ``base.T <: subset.T``."""
    _assert_names_subset(subset, base)
    kind = "covariant" if covariant else "contravariant"
    for name, info in subset.model_fields.items():
        src = info.annotation
        dest = base.model_fields[name].annotation
        if not covariant:
            src, dest = dest, src
        if not _is_subtype(src, dest):
            raise ValueError(
                f"{subset.__name__}.{name} is not a {kind} subtype of {base.__name__}.{name}: {src!r} <: {dest!r}"
            )


def subset_of[T: BaseModel](base: type[BaseModel], *, covariant: bool) -> Callable[[type[T]], type[T]]:
    """声明 ``cls`` 是 ``base`` 的字段子集; 导入时按 ``covariant`` 校验."""

    def deco(cls: type[T]) -> type[T]:
        assert_model_subset(cls, base, covariant=covariant)
        return cls

    return deco


def create_partial_model[T: BaseModel](
    base_cls: type[T],
    fields: tuple[str, ...] = (),
    *,
    recursive: bool = False,
    partial_cls_name: str | None = None,
    ignore_fields: tuple[str, ...] = (),
    json_schema_extras: dict[str, JsonDict | Callable[[JsonDict], None]] | None = None,
    extra_fields: dict[str, Any] | None = None,
) -> Any:
    """基于 ``base_cls`` 生成一个所有字段可选的 partial 模型.

    用于派生 "部分更新" 请求体 (PATCH/PUT): 每个字段变为 ``Optional`` 且默认 ``None``,
    从而仅校验显式提供的字段. 字段集与类型由 ``base_cls`` 静态派生, 不再手写, 保证两者兼容.
    源列本身不允许 None 时, 请求体里**显式** ``null`` 仍 422; 省略该键才是「不更新」.

    Args:
        base_cls: 源模型.
        fields: 若提供, 仅这些字段 (及其子字段) 参与 partial 化; 空 tuple 表示全部字段.
        recursive: 递归 partial 化嵌套的 ``BaseModel`` 字段.
        partial_cls_name: 生成类名, 默认 ``f"{base_cls.__name__}Partial"``.
        ignore_fields: 从结果中**完全排除**的字段名 (如只读列 id/时间戳, 或仅后端可写的字段).
            被排除字段不会出现在生成模型上, 因此无法经由该模型读写, 阻断越权赋值.
        json_schema_extras: 按字段名覆盖 ``json_schema_extra``, 合并到生成模型对应字段的 JSON Schema 中.
            格式为 ``{field_name: {...extra}}``.
        extra_fields: 非 DB 列的扩展可写字段 (如 ``Actor`` 别名行): ``{field_name: Annotated[type, Field(...)]}``.
            仅表模型可用; 与源列一致 partial 化 (Optional 默认 None), 显式 null 被拒 (同不可空列).
    """

    # Convert one type to being partial - if possible
    def _partial_annotation_arg(field_name_: str, field_annotation: type) -> type:
        if isinstance(field_annotation, type) and issubclass(field_annotation, BaseModel):
            field_prefix = f"{field_name_}."
            children_fields = [field.removeprefix(field_prefix) for field in fields_ if field.startswith(field_prefix)]
            if children_fields == ["*"]:
                children_fields = []
            return create_partial_model(field_annotation, tuple(children_fields), recursive=recursive)
        return field_annotation

    # By default make all fields optional, but use passed fields when possible
    fields_ = list(fields) if fields else list(base_cls.model_fields.keys())

    # ignore_fields 需保证被排除字段在结果模型上真正不存在. 仅当生成模型不继承 base_cls 的字段时
    # 才能保证这一点 -- 即 base_cls 是 table=True 的 SQLModel (下方会改用 SQLModel 作基类, 断开继承).
    # 普通 BaseModel 会经 __base__ 继承字段, 被忽略字段反而泄漏, 故在此显式拒绝以免静默出错.
    if ignore_fields:
        if not base_cls.model_config.get("table"):
            raise ValueError(
                f"ignore_fields requires a table=True SQLModel base; {base_cls.__name__} is not one, "
                "ignored fields would leak through inheritance."
            )
        if unknown := set(ignore_fields) - set(base_cls.model_fields):
            raise ValueError(
                f"ignore_fields contains unknown field(s): {unknown}. "
                f"Available on {base_cls.__name__}: {set(base_cls.model_fields)}"
            )

    json_schema_extras = json_schema_extras or {}
    if unknown := json_schema_extras.keys() - base_cls.model_fields.keys():
        raise ValueError(
            f"json_schema_extras contains unknown field(s): {unknown}. "
            f"Available on {base_cls.__name__}: {set(base_cls.model_fields)}"
        )

    # Construct list of optional new field overrides
    optional_fields: dict[str, Any] = {}
    non_nullable_source: set[str] = set()
    for field_name, field_info in base_cls.model_fields.items():
        if field_name in ignore_fields:
            continue
        field_annotation = field_info.annotation
        if field_annotation is None:  # pragma: no cover
            continue

        # Do we have any fields starting with $FIELD_NAME + "."?
        sub_fields_requested = any(field.startswith(f"{field_name}.") for field in fields_)

        # Continue if this field needs not to be handled
        if field_name not in fields_ and not sub_fields_requested:
            continue

        source_allows_none = _allows_none(field_annotation)

        # Change type for sub models, if requested
        if recursive or sub_fields_requested:
            field_annotation_origin = get_origin(field_annotation)
            if field_annotation_origin in (Union, UnionType, tuple, list, set, dict):
                if field_annotation_origin is UnionType:
                    field_annotation_origin = Union
                field_annotation = field_annotation_origin[  # pyright: ignore[reportInvalidTypeArguments]
                    tuple(
                        _partial_annotation_arg(field_name, field_annotation_arg)
                        for field_annotation_arg in get_args(field_annotation)
                    )
                ]
            else:
                field_annotation = _partial_annotation_arg(field_name, field_annotation)

        # Construct new field definition
        extra = json_schema_extras.get(field_name)
        extra_kwargs = {"json_schema_extra": extra} if extra else {}
        validators = tuple(m for m in field_info.metadata if isinstance(m, _ANNOTATED_VALIDATORS))
        if field_name in fields_:
            if not source_allows_none:
                non_nullable_source.add(field_name)
            annotation: Any = Optional[field_annotation]  # noqa: UP045  # ty:ignore[invalid-type-form]
            if validators:
                # PATCH 缺省为 None, 校验器只跑显式提供的值.
                annotation = Annotated[annotation, *_none_skipping(validators)]
            optional_fields[field_name] = (
                annotation,
                copy_field_info(field_info, default=None, default_factory=None, **extra_kwargs),
            )
        elif recursive or sub_fields_requested:
            annotation: Any = (
                Annotated[field_annotation, *validators]  # ty:ignore[invalid-type-form]
                if validators
                else field_annotation
            )
            optional_fields[field_name] = (annotation, copy_field_info(field_info, **extra_kwargs))

    # 非 DB 列扩展字段 (仅表模型): Annotated[type, Field(...)] 或裸类型.
    for field_name, spec in (extra_fields or {}).items():
        if field_name in base_cls.model_fields:
            raise ValueError(f"extra_fields contains existing model field: {field_name}")
        origin = get_origin(spec)
        if origin is Annotated:
            args = get_args(spec)
            annotation = args[0]
            infos = [a for a in args[1:] if isinstance(a, FieldInfo)]
            field = copy_field_info(infos[0], default=None) if infos else Field(default=None)
        else:
            annotation = spec
            field = Field(default=None)
        if annotation is None:  # pragma: no cover
            raise ValueError(f"extra_fields.{field_name} 缺少类型注解")
        if not _allows_none(annotation):
            non_nullable_source.add(field_name)
        optional_fields[field_name] = (Optional[annotation], field)  # noqa: UP045

    # Return original model class if nothing has changed
    if not optional_fields:
        return base_cls

    if partial_cls_name is None:
        partial_cls_name = f"{base_cls.__name__}Partial"

    # Generate new subclass model with those optional fields.
    # If the base is a SQLModel table class, use SQLModel as the base instead of
    # the table class to avoid inheriting InstrumentedAttribute descriptors that
    # would shadow Pydantic field accessors on instances.
    base = SQLModel if base_cls.model_config.get("table") else base_cls
    # 校验 mixin 不能再包一层具名子类: FastAPI 会用嵌套类 qualname 当 schema 名,
    # 把 LibraryUpdateRequest 冲成 Guarded__N, 生成客户端类型跟着坏掉.
    model_base: type[BaseModel] | tuple[type[BaseModel], ...] = base
    if non_nullable_source:
        model_base = (base, _explicit_null_mixin(frozenset(non_nullable_source)))
    return create_model(partial_cls_name, __base__=model_base, **optional_fields)


def _explicit_null_mixin(names: frozenset[str]) -> type[BaseModel]:
    """源列非 Optional 时, PATCH JSON 里显式 null 拒绝 (省略键仍表示不更新)."""

    class _RejectExplicitNull(BaseModel):
        @model_validator(mode="after")
        def _reject_explicit_null(self) -> Self:
            for field_name in names:
                if field_name in self.model_fields_set and getattr(self, field_name) is None:
                    raise ValueError(f"{field_name} cannot be null")
            return self

    return _RejectExplicitNull


@no_type_check
def copy_field_info(field_info: FieldInfo, **overrides: Any) -> FieldInfo:
    """
    Return a copy of a pydantic FieldInfo object, allow to override
    certain values.
    """

    if "json_schema_extra" in overrides and field_info.json_schema_extra:
        overrides = overrides.copy()
        overrides["json_schema_extra"] = {**field_info.json_schema_extra, **overrides["json_schema_extra"]}

    base_args = {
        k: v for k, v in field_info.__repr_args__() if k not in ("extra", "annotation", "required", "metadata")
    }
    # overrides may shadow keys already in base_args (e.g. default, json_schema_extra);
    # Python disallows duplicate kwargs, so drop overlapping keys from base_args first.
    for dup in overrides.keys() & base_args.keys():
        del base_args[dup]
    return Field(**base_args, **overrides)
