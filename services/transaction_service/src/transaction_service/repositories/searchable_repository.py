"""Generic paginated search for any entity described by a `SearchQueryDefinition`.

Two queries run in the same read-only transaction:
- a count, joining only what the filter needs;
- a page, also joining the relations the result columns display.
Both carry the same WHERE clause, including the current-year scope. Pages are ordered by the
requested field (empty values last), then by primary key, so paging is stable.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel
from sqlalchemy import ColumnElement, Label, SQLColumnExpression, and_, func, inspect, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.contracts import PaginatedResult
from transaction_service.filtering.business_calendar import DateRange
from transaction_service.filtering.field_registry import FieldRegistry
from transaction_service.filtering.filter_compiler import FilterCompiler, join_required_relations
from transaction_service.filtering.filter_validator import ValidatedSearch
from transaction_service.filtering.filterable_field import Relation


@dataclass(frozen=True, eq=False)
class SearchQueryDefinition[ItemT: BaseModel]:
    """What one searchable entity returns: its fields, result columns and row model."""

    registry: FieldRegistry
    item_model: type[ItemT]
    result_columns: Mapping[str, SQLColumnExpression[Any]]
    display_relations: tuple[Relation, ...] = ()  # one-to-one relations the columns read from

    def __post_init__(self) -> None:
        missing = set(self.item_model.model_fields) - set(self.result_columns)
        if missing:
            raise ValueError(f"result columns missing for: {', '.join(sorted(missing))}")


class SearchableRepository[ItemT: BaseModel]:
    def __init__(self, definition: SearchQueryDefinition[ItemT], compiler: FilterCompiler) -> None:
        self._definition = definition
        self._compiler = compiler
        root_entity = definition.registry.root_entity
        self._root_entity = root_entity
        self._primary_key = inspect(root_entity).primary_key[0]

    async def search(
        self, session: AsyncSession, search: ValidatedSearch, scope: DateRange
    ) -> PaginatedResult[ItemT]:
        compiled = self._compiler.compile(search.filter) if search.filter is not None else None
        filter_relations = compiled.required_relations if compiled is not None else ()
        where_clause = and_(
            self._scope_condition(scope),
            *([compiled.where_clause] if compiled is not None else []),
        )

        count_statement = join_required_relations(
            select(func.count()).select_from(self._root_entity), filter_relations
        ).where(where_clause)
        total_count = (await session.execute(count_statement)).scalar_one()

        sort_relations = relation_with_vias(search.sort_field.relation)
        page_relations = unique_in_order(
            (*self._definition.display_relations, *filter_relations, *sort_relations)
        )
        labelled_columns: list[Label[Any]] = [
            column.label(name) for name, column in self._definition.result_columns.items()
        ]
        page_statement = (
            join_required_relations(
                select(*labelled_columns).select_from(self._root_entity), page_relations
            )
            .where(where_clause)
            .order_by(self._sort_order(search), self._primary_key.asc())
            .limit(search.page_size)
            .offset((search.page - 1) * search.page_size)
        )
        rows = (await session.execute(page_statement)).mappings()
        return PaginatedResult[ItemT](
            items=[self._definition.item_model.model_validate(dict(row)) for row in rows],
            page=search.page,
            page_size=search.page_size,
            total_count=total_count,
        )

    def _scope_condition(self, scope: DateRange) -> ColumnElement[bool]:
        scope_column = self._definition.registry.current_year_scope_field.column
        return and_(scope_column >= scope.start, scope_column < scope.end)

    @staticmethod
    def _sort_order(search: ValidatedSearch) -> ColumnElement[Any]:
        sort_column = search.sort_field.column
        ordered = sort_column.asc() if search.sort_direction == "asc" else sort_column.desc()
        return ordered.nulls_last()


def relation_with_vias(relation: Relation | None) -> tuple[Relation, ...]:
    """A relation preceded by every relation it is reached through."""
    chain: list[Relation] = []
    while relation is not None:
        chain.insert(0, relation)
        relation = relation.via
    return tuple(chain)


def unique_in_order(relations: Iterable[Relation]) -> tuple[Relation, ...]:
    unique: list[Relation] = []
    for relation in relations:
        if relation not in unique:
            unique.append(relation)
    return tuple(unique)
