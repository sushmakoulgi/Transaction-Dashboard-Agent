import difflib
from collections.abc import Sequence

from shared.contracts import FilterableFieldDescription, SearchableEntity
from transaction_database.orm_models import Base
from transaction_service.filtering.field_types import FieldType
from transaction_service.filtering.filterable_field import FilterableField

MAX_NAME_SUGGESTIONS = 3
NAME_SIMILARITY_CUTOFF = 0.6


class FieldRegistry:
    """The allowlist of fields one entity can be filtered and sorted on.

    Any field name not in the registry is rejected; adding a filter means adding one field.
    `current_year_scope_field` is the root date that every search is limited to the current
    year on, whether or not the request filters on a date.
    """

    def __init__(
        self,
        entity: SearchableEntity,
        root_entity: type[Base],
        default_sort_field_name: str,
        current_year_scope_field_name: str,
        fields: Sequence[FilterableField],
    ) -> None:
        self.entity = entity
        self.root_entity = root_entity
        self._field_by_name: dict[str, FilterableField] = {}
        for filterable_field in fields:
            if filterable_field.name in self._field_by_name:
                raise ValueError(f"duplicate field '{filterable_field.name}' in {entity}")
            self._field_by_name[filterable_field.name] = filterable_field

        default_sort_field = self._field_by_name.get(default_sort_field_name)
        if default_sort_field is None or not default_sort_field.is_sortable:
            raise ValueError(f"default sort field '{default_sort_field_name}' must be sortable")
        self.default_sort_field = default_sort_field

        scope_field = self._field_by_name.get(current_year_scope_field_name)
        if (
            scope_field is None
            or scope_field.field_type is not FieldType.TIMESTAMP
            or scope_field.relation is not None
        ):
            raise ValueError(
                f"current-year scope field '{current_year_scope_field_name}' must be a "
                "timestamp on the root entity"
            )
        self.current_year_scope_field = scope_field

    @property
    def field_names(self) -> list[str]:
        return list(self._field_by_name)

    @property
    def sortable_field_names(self) -> list[str]:
        return [name for name, field in self._field_by_name.items() if field.is_sortable]

    def find_field(self, name: str) -> FilterableField | None:
        return self._field_by_name.get(name)

    def suggest_similar_names(self, unknown_name: str) -> list[str]:
        return difflib.get_close_matches(
            unknown_name, self.field_names, n=MAX_NAME_SUGGESTIONS, cutoff=NAME_SIMILARITY_CUTOFF
        )

    def describe_fields(self) -> list[FilterableFieldDescription]:
        return [
            FilterableFieldDescription(
                name=filterable_field.name,
                description=filterable_field.description,
                field_type=filterable_field.field_type.value,
                operators=tuple(sorted(filterable_field.operators)),
                allowed_values=filterable_field.enum_values,
                is_sortable=filterable_field.is_sortable,
            )
            for filterable_field in self._field_by_name.values()
        ]
