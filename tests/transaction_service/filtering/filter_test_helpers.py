"""Builders shared by the filtering tests."""

from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from shared.clock import Clock, FixedClock
from transaction_service.filtering.business_calendar import BusinessCalendar
from transaction_service.filtering.condition_resolver import ConditionResolver
from transaction_service.filtering.field_types import FieldValueCoercer
from transaction_service.filtering.filter_validator import FilterValidator

NEW_YORK = ZoneInfo("America/New_York")
# 2026-10-27 11:00 in New York (EDT, UTC-4).
OCTOBER_27_CLOCK = FixedClock(datetime(2026, 10, 27, 15, tzinfo=UTC))


def build_validator(clock: Clock = OCTOBER_27_CLOCK) -> FilterValidator:
    return FilterValidator(
        ConditionResolver(FieldValueCoercer(NEW_YORK), BusinessCalendar(NEW_YORK, clock))
    )


def utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def condition(field: str, operator: str, value: Any = None) -> dict[str, Any]:
    return {"kind": "condition", "field": field, "operator": operator, "value": value}


def group(*children: dict[str, Any], logic: str = "and", negate: bool = False) -> dict[str, Any]:
    return {"kind": "group", "logic": logic, "negate": negate, "children": list(children)}
