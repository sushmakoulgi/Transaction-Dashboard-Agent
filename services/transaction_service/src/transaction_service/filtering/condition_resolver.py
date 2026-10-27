"""Turns one condition (field + operator + raw value) into a typed `Predicate`.

Problems are reported as `ConditionIssue`s with a path suffix such as ".value[1]"; the
validator adds the condition's own path in front.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from shared.contracts import FilterIssueCode, FilterValue, Operator, RelativeDateValue
from transaction_service.filtering.business_calendar import (
    BusinessCalendar,
    DateOutsideAllowedRangeError,
    DateRange,
)
from transaction_service.filtering.field_types import (
    CoercedValue,
    FieldType,
    FieldValueCoercer,
    ValueCoercionError,
)
from transaction_service.filtering.filterable_field import FilterableField
from transaction_service.filtering.resolved_filter import (
    Comparison,
    Membership,
    NullCheck,
    Predicate,
    Range,
    RelatedRowCheck,
    TextContains,
)

VALUE_PATH = ".value"
COMPARISON_OPERATORS = frozenset(
    {
        Operator.EQUALS,
        Operator.NOT_EQUALS,
        Operator.GREATER_THAN,
        Operator.GREATER_THAN_OR_EQUAL,
        Operator.LESS_THAN,
        Operator.LESS_THAN_OR_EQUAL,
    }
)


@dataclass(frozen=True)
class ConditionIssue:
    path_suffix: str
    code: FilterIssueCode
    message: str
    suggestions: tuple[str, ...] = ()


class InvalidConditionError(Exception):
    def __init__(self, issues: Sequence[ConditionIssue]) -> None:
        super().__init__("; ".join(issue.message for issue in issues))
        self.issues = tuple(issues)


def invalid(
    path_suffix: str, code: FilterIssueCode, message: str, suggestions: Sequence[str] = ()
) -> InvalidConditionError:
    return InvalidConditionError([ConditionIssue(path_suffix, code, message, tuple(suggestions))])


class ConditionResolver:
    def __init__(self, coercer: FieldValueCoercer, calendar: BusinessCalendar) -> None:
        self._coercer = coercer
        self._calendar = calendar

    def resolve(self, field: FilterableField, operator: Operator, value: FilterValue) -> Predicate:
        """The caller has already checked that `field` allows `operator`."""
        if operator is Operator.EXISTS:
            return RelatedRowCheck(should_exist=self._flag(field, value))
        if operator is Operator.IS_NULL:
            return NullCheck(is_null=self._flag(field, value))
        if operator in (Operator.IN, Operator.NOT_IN):
            return Membership(
                values=self._coerce_list(field, operator, value),
                negate=operator is Operator.NOT_IN,
            )
        if operator is Operator.BETWEEN:
            return self._between(field, value)
        if operator is Operator.CONTAINS:
            return TextContains(text=str(self._coerce_scalar(field, value, VALUE_PATH)))
        if operator in COMPARISON_OPERATORS:
            return self._comparison(field, operator, value)
        raise ValueError(f"unsupported operator {operator}")

    def _comparison(
        self, field: FilterableField, operator: Operator, value: FilterValue
    ) -> Predicate:
        if field.field_type is not FieldType.TIMESTAMP:
            return Comparison(operator, self._coerce_scalar(field, value, VALUE_PATH))
        operand = self._timestamp_operand(field, value, VALUE_PATH)
        if isinstance(operand, DateRange):
            return compare_with_period(operator, operand)
        return Comparison(operator, operand)

    def _between(self, field: FilterableField, value: FilterValue) -> Range:
        if field.field_type is FieldType.TIMESTAMP and isinstance(value, RelativeDateValue):
            period = self._resolve_relative_date(value, VALUE_PATH)
            return Range(period.start, period.end, end_is_exclusive=True)
        if not isinstance(value, list) or len(value) != 2:
            raise invalid(
                VALUE_PATH,
                FilterIssueCode.INVALID_VALUE_COUNT,
                f"{field.name}: 'between' expects [start, end]"
                + (" or a relative date" if field.field_type is FieldType.TIMESTAMP else ""),
            )

        start_value, end_value = value
        if field.field_type is FieldType.TIMESTAMP:
            return self._timestamp_between(field, start_value, end_value)

        start, end = resolve_both(
            lambda: self._coerce_scalar(field, start_value, f"{VALUE_PATH}[0]"),
            lambda: self._coerce_scalar(field, end_value, f"{VALUE_PATH}[1]"),
        )
        if isinstance(start, int | Decimal) and isinstance(end, int | Decimal) and start > end:
            raise start_after_end(field)
        return Range(start, end, end_is_exclusive=False)

    def _timestamp_between(
        self, field: FilterableField, start_value: FilterValue, end_value: FilterValue
    ) -> Range:
        """A date endpoint covers its whole day; a date-time endpoint is an exact moment."""
        start_operand, end_operand = resolve_both(
            lambda: self._timestamp_operand(field, start_value, f"{VALUE_PATH}[0]"),
            lambda: self._timestamp_operand(field, end_value, f"{VALUE_PATH}[1]"),
        )
        start = start_operand.start if isinstance(start_operand, DateRange) else start_operand
        if isinstance(end_operand, DateRange):
            end, end_is_exclusive = end_operand.end, True
        else:
            end, end_is_exclusive = end_operand, False
        if start > end:
            raise start_after_end(field)
        return Range(start, end, end_is_exclusive)

    def _timestamp_operand(
        self, field: FilterableField, value: FilterValue, path_suffix: str
    ) -> DateRange | datetime:
        """A whole period (date or relative date) or an exact moment (date-time)."""
        if isinstance(value, RelativeDateValue):
            return self._resolve_relative_date(value, path_suffix)
        coerced = self._coerce_scalar(field, value, path_suffix)
        if not isinstance(coerced, date):
            raise ValueError("timestamp coercion returned a non-date")
        try:
            self._calendar.ensure_in_current_year(coerced)
        except DateOutsideAllowedRangeError as error:
            raise invalid(path_suffix, FilterIssueCode.DATE_OUT_OF_RANGE, error.message) from None
        if isinstance(coerced, datetime):
            return coerced
        return self._calendar.day_range(coerced)

    def _resolve_relative_date(self, value: RelativeDateValue, path_suffix: str) -> DateRange:
        try:
            return self._calendar.resolve(value)
        except DateOutsideAllowedRangeError as error:
            raise invalid(path_suffix, FilterIssueCode.DATE_OUT_OF_RANGE, error.message) from None

    def _coerce_list(
        self, field: FilterableField, operator: Operator, value: FilterValue
    ) -> tuple[CoercedValue, ...]:
        if not isinstance(value, list):
            raise invalid(
                VALUE_PATH,
                FilterIssueCode.INVALID_VALUE_COUNT,
                f"{field.name}: '{operator}' expects a list of values",
            )
        coerced_values: list[CoercedValue] = []
        issues: list[ConditionIssue] = []
        for index, item in enumerate(value):
            try:
                coerced_values.append(self._coerce_scalar(field, item, f"{VALUE_PATH}[{index}]"))
            except InvalidConditionError as error:
                issues.extend(error.issues)
        if issues:
            raise InvalidConditionError(issues)
        return tuple(coerced_values)

    def _coerce_scalar(
        self, field: FilterableField, value: FilterValue, path_suffix: str
    ) -> CoercedValue:
        if isinstance(value, list):
            raise invalid(
                path_suffix,
                FilterIssueCode.INVALID_VALUE_COUNT,
                f"{field.name}: expects a single value, not a list",
            )
        if isinstance(value, RelativeDateValue):
            raise invalid(
                path_suffix,
                FilterIssueCode.INVALID_VALUE,
                f"{field.name}: relative dates only work on date fields with comparisons "
                "or 'between'",
            )
        if value is None:
            raise invalid(
                path_suffix, FilterIssueCode.INVALID_VALUE, f"{field.name}: needs a value"
            )
        try:
            return self._coercer.coerce(field.field_type, value, field.enum_values)
        except ValueCoercionError as error:
            raise invalid(
                path_suffix,
                FilterIssueCode.INVALID_VALUE,
                f"{field.name}: {error.message}",
                error.suggestions,
            ) from None

    def _flag(self, field: FilterableField, value: FilterValue) -> bool:
        """`exists` / `is_null` take true (the default) or false."""
        if value is None:
            return True
        if isinstance(value, bool):
            return value
        raise invalid(
            VALUE_PATH,
            FilterIssueCode.INVALID_VALUE,
            f"{field.name}: expects true or false",
            ("true", "false"),
        )


def resolve_both[FirstT, SecondT](
    resolve_first: Callable[[], FirstT], resolve_second: Callable[[], SecondT]
) -> tuple[FirstT, SecondT]:
    """Runs both, so problems at either end of a range are reported together."""
    issues: list[ConditionIssue] = []
    first: FirstT | None = None
    second: SecondT | None = None
    try:
        first = resolve_first()
    except InvalidConditionError as error:
        issues.extend(error.issues)
    try:
        second = resolve_second()
    except InvalidConditionError as error:
        issues.extend(error.issues)
    if issues or first is None or second is None:
        raise InvalidConditionError(issues)
    return first, second


def start_after_end(field: FilterableField) -> InvalidConditionError:
    return invalid(VALUE_PATH, FilterIssueCode.INVALID_VALUE, f"{field.name}: start is after end")


def compare_with_period(operator: Operator, period: DateRange) -> Predicate:
    """Comparisons against a whole period: 'after Oct 15' means from Oct 16 00:00 onwards."""
    match operator:
        case Operator.EQUALS:
            return Range(period.start, period.end, end_is_exclusive=True)
        case Operator.NOT_EQUALS:
            return Range(period.start, period.end, end_is_exclusive=True, negate=True)
        case Operator.GREATER_THAN:
            return Comparison(Operator.GREATER_THAN_OR_EQUAL, period.end)
        case Operator.GREATER_THAN_OR_EQUAL:
            return Comparison(Operator.GREATER_THAN_OR_EQUAL, period.start)
        case Operator.LESS_THAN:
            return Comparison(Operator.LESS_THAN, period.start)
        case Operator.LESS_THAN_OR_EQUAL:
            return Comparison(Operator.LESS_THAN, period.end)
        case _:
            raise ValueError(f"{operator} is not a comparison")
