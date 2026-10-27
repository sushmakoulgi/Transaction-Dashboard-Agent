"""The filter language the LLM uses instead of SQL.

A filter is a tree: `FilterCondition` leaves combined by `FilterGroup` nodes (AND / OR,
optionally negated). This module only checks the tree's shape. Whether a field exists and
which operators it allows is checked by the Transaction Service against its field registry.
"""

from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import Field, StrictBool, StrictFloat, StrictInt, StrictStr, model_validator

from shared.contracts.contract_model import ContractModel

# Lowercase snake_case only, so a field name can never carry SQL syntax.
FIELD_NAME_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"
MAX_FILTER_GROUP_DEPTH = 3
MAX_FILTER_CONDITIONS = 20
MAX_LIST_VALUES = 50

FieldName = Annotated[str, Field(pattern=FIELD_NAME_PATTERN)]


class Operator(StrEnum):
    EQUALS = "eq"
    NOT_EQUALS = "neq"
    GREATER_THAN = "gt"
    GREATER_THAN_OR_EQUAL = "gte"
    LESS_THAN = "lt"
    LESS_THAN_OR_EQUAL = "lte"
    IN = "in"
    NOT_IN = "not_in"
    BETWEEN = "between"
    CONTAINS = "contains"
    IS_NULL = "is_null"
    EXISTS = "exists"


class RelativeDatePeriod(StrEnum):
    TODAY = "today"
    YESTERDAY = "yesterday"
    THIS_MONTH = "this_month"
    LAST_MONTH = "last_month"
    THIS_YEAR = "this_year"


class RelativeDateValue(ContractModel):
    """A date range resolved by the Transaction Service in the business timezone.

    Set exactly one of `relative` or `last_n_days`.
    """

    relative: RelativeDatePeriod | None = None
    last_n_days: int | None = Field(default=None, ge=1, le=366)

    @model_validator(mode="after")
    def require_exactly_one_period(self) -> Self:
        if (self.relative is None) == (self.last_n_days is None):
            raise ValueError("set exactly one of 'relative' or 'last_n_days'")
        return self


# Strict types: values are not coerced here ("500" stays a string). The Transaction Service
# converts each value to its field's declared type.
ScalarFilterValue = StrictStr | StrictInt | StrictFloat | StrictBool
FilterValue = (
    ScalarFilterValue
    | Annotated[list[ScalarFilterValue], Field(min_length=1, max_length=MAX_LIST_VALUES)]
    | RelativeDateValue
    | None
)


class FilterCondition(ContractModel):
    kind: Literal["condition"] = "condition"
    field: FieldName
    operator: Operator
    value: FilterValue = None


class FilterGroup(ContractModel):
    kind: Literal["group"] = "group"
    logic: Literal["and", "or"] = "and"
    negate: bool = False
    children: list["FilterNode"] = Field(min_length=1, max_length=MAX_FILTER_CONDITIONS)


FilterNode = Annotated[FilterCondition | FilterGroup, Field(discriminator="kind")]

FilterGroup.model_rebuild()


def group_depth(node: FilterCondition | FilterGroup) -> int:
    """Number of nested groups on the deepest path. A bare condition has depth 0."""
    if isinstance(node, FilterCondition):
        return 0
    return 1 + max(group_depth(child) for child in node.children)


def count_conditions(node: FilterCondition | FilterGroup) -> int:
    if isinstance(node, FilterCondition):
        return 1
    return sum(count_conditions(child) for child in node.children)


def ensure_within_filter_limits(node: FilterCondition | FilterGroup) -> None:
    depth = group_depth(node)
    if depth > MAX_FILTER_GROUP_DEPTH:
        raise ValueError(
            f"filter nests {depth} groups deep; the maximum is {MAX_FILTER_GROUP_DEPTH}"
        )
    condition_count = count_conditions(node)
    if condition_count > MAX_FILTER_CONDITIONS:
        raise ValueError(
            f"filter has {condition_count} conditions; the maximum is {MAX_FILTER_CONDITIONS}"
        )
