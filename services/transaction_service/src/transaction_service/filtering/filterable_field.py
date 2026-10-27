"""Building blocks of the field registry: what may be filtered, and how to reach it.

Dataclasses use `eq=False` because SQLAlchemy overloads `==` on columns to build SQL; these
objects are compared by identity instead.
"""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from sqlalchemy import ColumnElement, SQLColumnExpression

from shared.contracts.filter_expression import Operator
from transaction_database.orm_models import Base
from transaction_service.filtering.field_types import FieldType


class RelationCardinality(StrEnum):
    ONE = "one"  # joined into the main query (LEFT JOIN)
    MANY = "many"  # checked with an EXISTS subquery, so root rows are never duplicated


@dataclass(frozen=True, eq=False)
class Relation:
    """A table reachable from the registry's root entity.

    `join_condition` links `target` to the root entity, or to `via` when set. `via` must be
    joined first (refunds are reached through the payment).
    """

    name: str
    cardinality: RelationCardinality
    target: type[Base]
    join_condition: ColumnElement[bool]
    via: "Relation | None" = None


COMPARISON_OPERATORS = frozenset(
    {
        Operator.EQUALS,
        Operator.NOT_EQUALS,
        Operator.GREATER_THAN,
        Operator.GREATER_THAN_OR_EQUAL,
        Operator.LESS_THAN,
        Operator.LESS_THAN_OR_EQUAL,
        Operator.BETWEEN,
    }
)
MEMBERSHIP_OPERATORS = frozenset(
    {Operator.EQUALS, Operator.NOT_EQUALS, Operator.IN, Operator.NOT_IN}
)

DEFAULT_OPERATORS_BY_FIELD_TYPE: dict[FieldType, frozenset[Operator]] = {
    FieldType.STRING: MEMBERSHIP_OPERATORS | {Operator.CONTAINS},
    FieldType.ENUM: MEMBERSHIP_OPERATORS,
    FieldType.UUID: MEMBERSHIP_OPERATORS,
    FieldType.DECIMAL: COMPARISON_OPERATORS,
    FieldType.INTEGER: COMPARISON_OPERATORS,
    FieldType.TIMESTAMP: COMPARISON_OPERATORS,
    FieldType.BOOLEAN: frozenset({Operator.EQUALS}),
}


@dataclass(frozen=True, eq=False)
class FilterableField:
    name: str
    description: str
    field_type: FieldType
    column: SQLColumnExpression[Any]
    operators: frozenset[Operator]
    relation: Relation | None = None
    enum_values: tuple[str, ...] = field(default=())
    is_sortable: bool = False

    def __post_init__(self) -> None:
        if not self.operators:
            raise ValueError(f"field '{self.name}' allows no operators")
        if (self.field_type is FieldType.ENUM) != bool(self.enum_values):
            raise ValueError(f"field '{self.name}': enum_values must be set for enum fields only")
        if Operator.EXISTS in self.operators and (
            self.relation is None or self.field_type is not FieldType.BOOLEAN
        ):
            raise ValueError(f"field '{self.name}': 'exists' needs a boolean field on a relation")
        if self.is_sortable and (
            self.relation is not None and self.relation.cardinality is RelationCardinality.MANY
        ):
            raise ValueError(f"field '{self.name}': fields on a one-to-many relation can't sort")


def filterable_field(
    name: str,
    field_type: FieldType,
    column: SQLColumnExpression[Any],
    description: str,
    *,
    relation: Relation | None = None,
    enum_values: tuple[str, ...] = (),
    is_sortable: bool = False,
    is_nullable: bool = False,
) -> FilterableField:
    """A field with the default operators for its type (plus `is_null` when nullable)."""
    operators = DEFAULT_OPERATORS_BY_FIELD_TYPE[field_type]
    if is_nullable:
        operators = operators | {Operator.IS_NULL}
    return FilterableField(
        name=name,
        description=description,
        field_type=field_type,
        column=column,
        operators=operators,
        relation=relation,
        enum_values=enum_values,
        is_sortable=is_sortable,
    )


def related_row_exists_field(
    name: str, relation: Relation, primary_key: SQLColumnExpression[Any], description: str
) -> FilterableField:
    """A true/false field meaning "at least one related row exists" (e.g. has_chargeback)."""
    return FilterableField(
        name=name,
        description=description,
        field_type=FieldType.BOOLEAN,
        column=primary_key,
        operators=frozenset({Operator.EXISTS}),
        relation=relation,
    )
