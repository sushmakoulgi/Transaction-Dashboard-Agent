"""A validated filter: every field is a registry entry and every value has its real type.

The validator produces this tree; the compiler turns it into SQL. Dates are already exact
UTC moments here, with the current-year limit applied.
"""

from dataclasses import dataclass
from typing import Literal

from shared.contracts import Operator
from transaction_service.filtering.field_types import CoercedValue
from transaction_service.filtering.filterable_field import FilterableField


@dataclass(frozen=True)
class Comparison:
    operator: Operator  # eq, neq, gt, gte, lt or lte
    value: CoercedValue


@dataclass(frozen=True)
class Membership:
    values: tuple[CoercedValue, ...]
    negate: bool = False


@dataclass(frozen=True)
class Range:
    start: CoercedValue
    end: CoercedValue
    end_is_exclusive: bool
    negate: bool = False


@dataclass(frozen=True)
class TextContains:
    text: str


@dataclass(frozen=True)
class NullCheck:
    is_null: bool


@dataclass(frozen=True)
class RelatedRowCheck:
    should_exist: bool


Predicate = Comparison | Membership | Range | TextContains | NullCheck | RelatedRowCheck


@dataclass(frozen=True, eq=False)
class ResolvedCondition:
    field: FilterableField
    predicate: Predicate


@dataclass(frozen=True, eq=False)
class ResolvedGroup:
    logic: Literal["and", "or"]
    negate: bool
    children: tuple["ResolvedNode", ...]


ResolvedNode = ResolvedCondition | ResolvedGroup
