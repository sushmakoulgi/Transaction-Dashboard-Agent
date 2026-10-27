"""Checks a search request against an entity's field registry.

Every problem in the request is collected in one pass, each with its exact path, so the
LLM can fix them all at once. A valid request comes back as a `ValidatedSearch`.
"""

from dataclasses import dataclass
from typing import Literal

from shared.contracts import (
    FilterCondition,
    FilterGroup,
    FilterIssueCode,
    FilterValidationIssue,
    SearchRequest,
    SortSpec,
)
from transaction_service.filtering.condition_resolver import (
    ConditionResolver,
    InvalidConditionError,
)
from transaction_service.filtering.field_registry import FieldRegistry
from transaction_service.filtering.filterable_field import FilterableField
from transaction_service.filtering.resolved_filter import (
    ResolvedCondition,
    ResolvedGroup,
    ResolvedNode,
)


class FilterValidationFailedError(Exception):
    def __init__(self, issues: list[FilterValidationIssue]) -> None:
        super().__init__(f"{len(issues)} filter validation issue(s)")
        self.issues = issues


@dataclass(frozen=True, eq=False)
class ValidatedSearch:
    filter: ResolvedNode | None
    sort_field: FilterableField
    sort_direction: Literal["asc", "desc"]
    page: int
    page_size: int


class FilterValidator:
    def __init__(self, condition_resolver: ConditionResolver) -> None:
        self._condition_resolver = condition_resolver

    def validate(self, request: SearchRequest, registry: FieldRegistry) -> ValidatedSearch:
        """Raises `FilterValidationFailedError` listing every problem found."""
        issues: list[FilterValidationIssue] = []
        resolved_filter = (
            self._resolve_node(request.filter, "filter", registry, issues)
            if request.filter is not None
            else None
        )
        sort_field, sort_direction = self._resolve_sort(request.sort, registry, issues)
        if issues:
            raise FilterValidationFailedError(issues)
        return ValidatedSearch(
            filter=resolved_filter,
            sort_field=sort_field,
            sort_direction=sort_direction,
            page=request.page,
            page_size=request.page_size,
        )

    def _resolve_node(
        self,
        node: FilterCondition | FilterGroup,
        path: str,
        registry: FieldRegistry,
        issues: list[FilterValidationIssue],
    ) -> ResolvedNode | None:
        if isinstance(node, FilterCondition):
            return self._resolve_condition(node, path, registry, issues)
        # Resolve every child (no short-circuit) so all problems are reported.
        children = [
            self._resolve_node(child, f"{path}.children[{index}]", registry, issues)
            for index, child in enumerate(node.children)
        ]
        resolved_children = tuple(child for child in children if child is not None)
        if len(resolved_children) != len(children):
            return None
        return ResolvedGroup(logic=node.logic, negate=node.negate, children=resolved_children)

    def _resolve_condition(
        self,
        condition: FilterCondition,
        path: str,
        registry: FieldRegistry,
        issues: list[FilterValidationIssue],
    ) -> ResolvedCondition | None:
        field = registry.find_field(condition.field)
        if field is None:
            issues.append(
                FilterValidationIssue(
                    path=f"{path}.field",
                    code=FilterIssueCode.UNKNOWN_FIELD,
                    message=f"unknown field '{condition.field}' for {registry.entity}",
                    suggestions=tuple(registry.suggest_similar_names(condition.field)),
                )
            )
            return None
        if condition.operator not in field.operators:
            allowed_operators = tuple(sorted(field.operators))
            issues.append(
                FilterValidationIssue(
                    path=f"{path}.operator",
                    code=FilterIssueCode.OPERATOR_NOT_ALLOWED,
                    message=(
                        f"{field.name} does not support '{condition.operator}'; "
                        f"allowed: {', '.join(allowed_operators)}"
                    ),
                    suggestions=allowed_operators,
                )
            )
            return None
        try:
            predicate = self._condition_resolver.resolve(field, condition.operator, condition.value)
        except InvalidConditionError as error:
            issues.extend(
                FilterValidationIssue(
                    path=f"{path}{issue.path_suffix}",
                    code=issue.code,
                    message=issue.message,
                    suggestions=issue.suggestions,
                )
                for issue in error.issues
            )
            return None
        return ResolvedCondition(field=field, predicate=predicate)

    def _resolve_sort(
        self,
        sort: SortSpec | None,
        registry: FieldRegistry,
        issues: list[FilterValidationIssue],
    ) -> tuple[FilterableField, Literal["asc", "desc"]]:
        if sort is None:
            return registry.default_sort_field, "desc"
        field = registry.find_field(sort.field)
        if field is None:
            issues.append(
                FilterValidationIssue(
                    path="sort.field",
                    code=FilterIssueCode.UNKNOWN_FIELD,
                    message=f"unknown sort field '{sort.field}' for {registry.entity}",
                    suggestions=tuple(registry.suggest_similar_names(sort.field)),
                )
            )
        elif not field.is_sortable:
            issues.append(
                FilterValidationIssue(
                    path="sort.field",
                    code=FilterIssueCode.FIELD_NOT_SORTABLE,
                    message=f"{field.name} can't be sorted on",
                    suggestions=tuple(registry.sortable_field_names),
                )
            )
        else:
            return field, sort.direction
        return registry.default_sort_field, "desc"
