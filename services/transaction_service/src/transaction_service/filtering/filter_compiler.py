"""Compiles a resolved filter into a SQLAlchemy WHERE clause.

- Every value is a bound parameter; nothing from the request is pasted into SQL text.
- One-to-one relations (payment, chargeback) are LEFT JOINed, and only when needed.
- One-to-many relations (refunds, chargeback events) become EXISTS subqueries, so root rows
  are never duplicated. Within one AND group, conditions on the same one-to-many relation
  share a single EXISTS: they must all match the same related row.
- Negation is the exact complement: rows where the field is empty are included
  (`neq` → IS DISTINCT FROM, NOT group → NOT coalesce(..., false)).
"""

import operator
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import (
    ColumnElement,
    Select,
    SQLColumnExpression,
    and_,
    false,
    func,
    literal_column,
    not_,
    or_,
    select,
)

from shared.contracts import Operator
from transaction_service.filtering.filterable_field import Relation, RelationCardinality
from transaction_service.filtering.resolved_filter import (
    Comparison,
    Membership,
    NullCheck,
    Predicate,
    Range,
    RelatedRowCheck,
    ResolvedCondition,
    ResolvedGroup,
    ResolvedNode,
    TextContains,
)

LIKE_ESCAPE_CHARACTER = "\\"

COMPARISON_BUILDERS: dict[Operator, Callable[[Any, Any], ColumnElement[bool]]] = {
    Operator.EQUALS: operator.eq,
    Operator.NOT_EQUALS: lambda column, value: column.is_distinct_from(value),
    Operator.GREATER_THAN: operator.gt,
    Operator.GREATER_THAN_OR_EQUAL: operator.ge,
    Operator.LESS_THAN: operator.lt,
    Operator.LESS_THAN_OR_EQUAL: operator.le,
}


@dataclass(frozen=True, eq=False)
class CompiledFilter:
    where_clause: ColumnElement[bool]
    required_relations: tuple[Relation, ...]  # one-to-one relations to LEFT JOIN, in order


def join_required_relations[StatementT: Select[Any]](
    statement: StatementT, relations: tuple[Relation, ...]
) -> StatementT:
    for relation in relations:
        statement = statement.outerjoin(relation.target, relation.join_condition)
    return statement


class FilterCompiler:
    def compile(self, node: ResolvedNode) -> CompiledFilter:
        compilation = _Compilation()
        where_clause = compilation.compile_node(node)
        return CompiledFilter(where_clause, tuple(compilation.required_relations))


class _Compilation:
    """State for compiling one filter: which relations must be joined."""

    def __init__(self) -> None:
        self.required_relations: list[Relation] = []

    def compile_node(self, node: ResolvedNode) -> ColumnElement[bool]:
        if isinstance(node, ResolvedCondition):
            return self._compile_condition(node)
        if node.logic == "and":
            expression = self._compile_and_group(node)
        else:
            expression = or_(*(self.compile_node(child) for child in node.children))
        if node.negate:
            return not_(func.coalesce(expression, false()))
        return expression

    def _compile_and_group(self, group: ResolvedGroup) -> ColumnElement[bool]:
        parts: list[ColumnElement[bool]] = []
        conditions_by_many_relation: dict[Relation, list[ResolvedCondition]] = {}
        for child in group.children:
            many_relation = mergeable_many_relation(child)
            if isinstance(child, ResolvedCondition) and many_relation is not None:
                conditions_by_many_relation.setdefault(many_relation, []).append(child)
            else:
                parts.append(self.compile_node(child))
        for relation, conditions in conditions_by_many_relation.items():
            parts.append(
                self._exists(
                    relation,
                    [
                        predicate_expression(condition.field.column, condition.predicate)
                        for condition in conditions
                    ],
                )
            )
        return and_(*parts)

    def _compile_condition(self, condition: ResolvedCondition) -> ColumnElement[bool]:
        relation = condition.field.relation
        predicate = condition.predicate
        if isinstance(predicate, RelatedRowCheck):
            if relation is None:
                raise ValueError(f"{condition.field.name}: 'exists' needs a relation")
            return self._related_row_check(relation, condition.field.column, predicate)
        if relation is not None and relation.cardinality is RelationCardinality.MANY:
            return self._exists(relation, [predicate_expression(condition.field.column, predicate)])
        if relation is not None:
            self._require(relation)
        return predicate_expression(condition.field.column, predicate)

    def _related_row_check(
        self,
        relation: Relation,
        primary_key: SQLColumnExpression[Any],
        check: RelatedRowCheck,
    ) -> ColumnElement[bool]:
        if relation.cardinality is RelationCardinality.MANY:
            exists_clause = self._exists(relation, [])
            return exists_clause if check.should_exist else not_(exists_clause)
        self._require(relation)
        return primary_key.is_not(None) if check.should_exist else primary_key.is_(None)

    def _exists(
        self, relation: Relation, predicates: list[ColumnElement[bool]]
    ) -> ColumnElement[bool]:
        if relation.via is not None:
            self._require(relation.via)
        return (
            select(literal_column("1"))
            .select_from(relation.target)
            .where(relation.join_condition, *predicates)
            .correlate_except(relation.target)
            .exists()
        )

    def _require(self, relation: Relation) -> None:
        if relation.via is not None:
            self._require(relation.via)
        if relation not in self.required_relations:
            self.required_relations.append(relation)


def mergeable_many_relation(node: ResolvedNode) -> Relation | None:
    """The one-to-many relation of a value condition; `exists` checks are never merged."""
    if not isinstance(node, ResolvedCondition) or isinstance(node.predicate, RelatedRowCheck):
        return None
    relation = node.field.relation
    if relation is None or relation.cardinality is not RelationCardinality.MANY:
        return None
    return relation


def predicate_expression(
    column: SQLColumnExpression[Any], predicate: Predicate
) -> ColumnElement[bool]:
    match predicate:
        case Comparison(operator=comparison_operator, value=value):
            return COMPARISON_BUILDERS[comparison_operator](column, value)
        case Membership(values=values, negate=False):
            return column.in_(values)
        case Membership(values=values, negate=True):
            return or_(column.is_(None), column.not_in(values))
        case Range(start=start, end=end, end_is_exclusive=end_is_exclusive, negate=negate):
            upper_bound = column < end if end_is_exclusive else column <= end
            in_range = and_(column >= start, upper_bound)
            return not_(func.coalesce(in_range, false())) if negate else in_range
        case TextContains(text=text):
            return column.ilike(f"%{escape_like(text)}%", escape=LIKE_ESCAPE_CHARACTER)
        case NullCheck(is_null=is_null):
            return column.is_(None) if is_null else column.is_not(None)
        case RelatedRowCheck():
            raise ValueError("related-row checks are compiled by the relation")
    raise ValueError(f"unsupported predicate {predicate!r}")


def escape_like(text: str) -> str:
    """Makes %, _ and the escape character match literally in ILIKE."""
    for special_character in (LIKE_ESCAPE_CHARACTER, "%", "_"):
        text = text.replace(special_character, LIKE_ESCAPE_CHARACTER + special_character)
    return text
