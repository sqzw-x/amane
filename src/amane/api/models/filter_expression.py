from __future__ import annotations

import re
from typing import Annotated, ClassVar, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

from ...db.models import ActorSortField, MetadataSortField, SortOrder
from ...enums import ActorGender
from ...parsing import ContentType, Mosaic, canonical_definition
from ...utils.dates import normalize_calendar_date

MAX_FILTER_BODY_BYTES = 32_768
MAX_FILTER_DEPTH = 8
MAX_FILTER_NODES = 256
MAX_FILTER_ID_RULES = 200
MAX_FILTER_STRING_LENGTH = 200
MAX_FILTER_CANDIDATE_IDS = 100_000
MAX_FILTER_INTEGER_VALUE = (1 << 63) - 1

type FilterOperator = Literal["eq", "contains", "gte", "lte"]
type FilterField = Literal[
    "actor_id",
    "director_id",
    "tag_id",
    "user_tag_id",
    "studio_id",
    "publisher_id",
    "series_id",
    "library_id",
    "has_files",
    "has_subtitle",
    "vr",
    "uncensored",
    "mosaic",
    "definition",
    "content_type",
    "number",
    "title",
    "gender",
    "has_person",
    "has_image",
    "birthday",
    "height",
    "bust",
    "waist",
    "hip",
    "cup",
    "name",
    "alias",
    "birthplace",
]
type BoundedString = Annotated[StrictStr, Field(max_length=MAX_FILTER_STRING_LENGTH)]
type FilterValue = StrictBool | StrictInt | StrictFloat | BoundedString

METADATA_FILTER_FIELDS: frozenset[str] = frozenset(
    {
        "actor_id",
        "director_id",
        "tag_id",
        "user_tag_id",
        "studio_id",
        "publisher_id",
        "series_id",
        "library_id",
        "has_files",
        "has_subtitle",
        "vr",
        "uncensored",
        "mosaic",
        "definition",
        "content_type",
        "number",
        "title",
    }
)
ACTOR_FILTER_FIELDS: frozenset[str] = frozenset(
    {
        "gender",
        "user_tag_id",
        "has_person",
        "has_image",
        "birthday",
        "height",
        "bust",
        "waist",
        "hip",
        "cup",
        "name",
        "alias",
        "birthplace",
    }
)
_ID_FIELDS = frozenset(
    {"actor_id", "director_id", "tag_id", "user_tag_id", "studio_id", "publisher_id", "series_id", "library_id"}
)
_BOOLEAN_FIELDS = frozenset({"has_files", "has_subtitle", "vr", "uncensored", "has_person", "has_image"})
_TEXT_FIELDS = frozenset({"number", "title", "name", "alias", "birthplace"})
_RANGE_INTEGER_FIELDS = frozenset({"height", "bust", "waist", "hip"})
_RANGE_FIELDS = frozenset({"birthday", "height", "bust", "waist", "hip", "cup"})
_FILTER_FIELD_OPERATORS: dict[str, frozenset[FilterOperator]] = {
    **{field: frozenset({"eq"}) for field in _ID_FIELDS | _BOOLEAN_FIELDS},
    "mosaic": frozenset({"eq"}),
    "definition": frozenset({"eq"}),
    "content_type": frozenset({"eq"}),
    "gender": frozenset({"eq"}),
    **{field: frozenset({"contains"}) for field in _TEXT_FIELDS},
    **{field: frozenset({"gte", "lte"}) for field in _RANGE_FIELDS},
}


class FilterGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["group"]
    op: Literal["and", "or"]
    negate: StrictBool = False
    children: list[FilterNode] = Field(min_length=1, max_length=MAX_FILTER_NODES)


class FilterRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["rule"]
    field: FilterField
    operator: FilterOperator
    value: FilterValue

    @model_validator(mode="after")
    def _validate_field_value(self) -> FilterRule:
        allowed_operators = _FILTER_FIELD_OPERATORS[self.field]
        if self.operator not in allowed_operators:
            raise ValueError(f"field {self.field!r} does not support operator {self.operator!r}")

        value = self.value
        if self.field in _ID_FIELDS:
            if type(value) is not int or not 1 <= value <= MAX_FILTER_INTEGER_VALUE:
                raise ValueError(f"{self.field} requires a positive integer")
        elif self.field in _BOOLEAN_FIELDS:
            if type(value) is not bool:
                raise ValueError(f"{self.field} requires a boolean")
        elif self.field in _RANGE_INTEGER_FIELDS:
            if type(value) is not int or not 0 <= value <= MAX_FILTER_INTEGER_VALUE:
                raise ValueError(f"{self.field} requires a non-negative integer")
        elif self.field == "birthday":
            if not isinstance(value, str):
                raise ValueError("birthday requires a calendar date string")
            normalized = normalize_calendar_date(value)
            if normalized is None:
                raise ValueError("birthday must be a valid YYYY-MM-DD date")
            self.value = normalized
        elif self.field == "cup":
            if not isinstance(value, str) or re.fullmatch(r"[A-Za-z]{1,2}", value) is None:
                raise ValueError("cup requires one or two ASCII letters")
            self.value = value.upper()
        elif self.field == "gender":
            if not isinstance(value, str) or value not in {str(item) for item in ActorGender}:
                raise ValueError("gender must be a supported actor gender")
        elif self.field == "mosaic":
            if not isinstance(value, str) or value not in {str(item) for item in Mosaic}:
                raise ValueError("mosaic must be a supported mosaic value")
        elif self.field == "content_type":
            if not isinstance(value, str) or value not in {str(item) for item in ContentType}:
                raise ValueError("content_type must be a supported content type")
        elif self.field == "definition":
            if not isinstance(value, str):
                raise ValueError("definition must be a supported resolution or legacy alias")
            canonical = canonical_definition(value)
            if canonical is None:
                raise ValueError("definition must be a supported resolution or legacy alias")
            self.value = canonical
        elif self.field in _TEXT_FIELDS and not isinstance(value, str):
            raise ValueError(f"{self.field} requires a string")
        return self


type FilterNode = Annotated[FilterGroup | FilterRule, Field(discriminator="type")]
FilterGroup.model_rebuild()


def validate_filter_tree(expression: FilterNode, allowed_fields: frozenset[str]) -> None:
    node_count = 0
    id_rule_count = 0

    def visit(node: FilterNode, path: str, group_depth: int) -> None:
        nonlocal id_rule_count, node_count
        node_count += 1
        if node_count > MAX_FILTER_NODES:
            raise ValueError(f"{path}: filter exceeds {MAX_FILTER_NODES} total nodes")
        if isinstance(node, FilterGroup):
            current_depth = group_depth + 1
            if current_depth > MAX_FILTER_DEPTH:
                raise ValueError(f"{path}: filter exceeds {MAX_FILTER_DEPTH} nested groups")
            for index, child in enumerate(node.children):
                visit(child, f"{path}.children[{index}]", current_depth)
            return

        if node.field not in allowed_fields:
            raise ValueError(f"{path}.field: {node.field!r} is not supported for this entity")
        if node.field in _ID_FIELDS:
            id_rule_count += 1
            if id_rule_count > MAX_FILTER_ID_RULES:
                raise ValueError(f"{path}: filter exceeds {MAX_FILTER_ID_RULES} ID rules")

    visit(expression, "expression", 0)


class FilterSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    expression: FilterGroup | None = None
    search: BoundedString | None = None
    offset: int = Field(default=0, ge=0, le=2_147_483_647, strict=True)
    order: SortOrder = SortOrder.ASC
    _allowed_filter_fields: ClassVar[frozenset[str]] = frozenset()

    @field_validator("search")
    @classmethod
    def _normalize_search(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def _validate_expression_limits(self) -> FilterSearchRequest:
        if self.expression is not None:
            validate_filter_tree(self.expression, self._allowed_filter_fields)
        return self


class MetadataSearchRequest(FilterSearchRequest):
    _allowed_filter_fields: ClassVar[frozenset[str]] = METADATA_FILTER_FIELDS

    limit: int = Field(default=20, ge=1, le=200, strict=True)
    sort_by: MetadataSortField = MetadataSortField.UPDATED_AT
    order: SortOrder = SortOrder.DESC
    semantic_query: Annotated[StrictStr, Field(min_length=1, max_length=MAX_FILTER_STRING_LENGTH)] | None = None
    semantic_kind: Literal["all", "cover", "still"] = "all"


class ActorSearchRequest(FilterSearchRequest):
    _allowed_filter_fields: ClassVar[frozenset[str]] = ACTOR_FILTER_FIELDS

    limit: int = Field(default=50, ge=1, le=1000, strict=True)
    sort_by: ActorSortField = ActorSortField.NAME
