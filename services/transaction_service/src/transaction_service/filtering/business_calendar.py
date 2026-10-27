"""Business-day arithmetic in the business timezone, and the current-year search limit.

All ranges are half-open `[start, end)` and returned in UTC. Only dates inside the current
calendar year (in the business timezone) may be searched; anything else is rejected.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import assert_never
from zoneinfo import ZoneInfo

from shared.clock import Clock
from shared.contracts import RelativeDatePeriod, RelativeDateValue


@dataclass(frozen=True)
class DateRange:
    start: datetime  # inclusive, UTC
    end: datetime  # exclusive, UTC


class DateOutsideAllowedRangeError(ValueError):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class BusinessCalendar:
    def __init__(self, business_timezone: ZoneInfo, clock: Clock) -> None:
        self._business_timezone = business_timezone
        self._clock = clock

    def today(self) -> date:
        return self._clock.now().astimezone(self._business_timezone).date()

    def day_range(self, day: date) -> DateRange:
        """The whole business day, e.g. for "transactions on 2026-10-15"."""
        return self._range_between_days(day, day + timedelta(days=1))

    def current_year_range(self) -> DateRange:
        year = self.today().year
        return self._range_between_days(date(year, 1, 1), date(year + 1, 1, 1))

    def resolve(self, relative_date: RelativeDateValue) -> DateRange:
        date_range = self._resolve_without_year_check(relative_date)
        self._ensure_range_in_current_year(date_range, describe(relative_date))
        return date_range

    def ensure_in_current_year(self, value: date | datetime) -> None:
        """Rejects an explicit date or date-time outside the current year."""
        if isinstance(value, datetime):
            year_range = self.current_year_range()
            if not year_range.start <= value < year_range.end:
                raise self._outside_current_year_error(value.isoformat())
            return
        self._ensure_range_in_current_year(self.day_range(value), value.isoformat())

    def _resolve_without_year_check(self, relative_date: RelativeDateValue) -> DateRange:
        today = self.today()
        tomorrow = today + timedelta(days=1)
        if relative_date.last_n_days is not None:
            # last_n_days: 7 means today and the six days before it.
            first_day = today - timedelta(days=relative_date.last_n_days - 1)
            return self._range_between_days(first_day, tomorrow)

        period = relative_date.relative
        if period is None:
            raise ValueError("relative date has no period")
        first_of_this_month = today.replace(day=1)
        match period:
            case RelativeDatePeriod.TODAY:
                return self.day_range(today)
            case RelativeDatePeriod.YESTERDAY:
                return self.day_range(today - timedelta(days=1))
            case RelativeDatePeriod.THIS_MONTH:
                return self._range_between_days(
                    first_of_this_month, first_of_next_month(first_of_this_month)
                )
            case RelativeDatePeriod.LAST_MONTH:
                return self._range_between_days(
                    first_of_previous_month(first_of_this_month), first_of_this_month
                )
            case RelativeDatePeriod.THIS_YEAR:
                return self.current_year_range()
            case _:
                assert_never(period)

    def _ensure_range_in_current_year(self, date_range: DateRange, described_as: str) -> None:
        year_range = self.current_year_range()
        if date_range.start < year_range.start or date_range.end > year_range.end:
            raise self._outside_current_year_error(described_as)

    def _outside_current_year_error(self, described_as: str) -> DateOutsideAllowedRangeError:
        year = self.today().year
        return DateOutsideAllowedRangeError(
            f"{described_as} falls outside {year}; only dates from {year}-01-01 to "
            f"{year}-12-31 can be searched"
        )

    def _range_between_days(self, first_day: date, day_after_last: date) -> DateRange:
        return DateRange(
            start=self._start_of_day_in_utc(first_day),
            end=self._start_of_day_in_utc(day_after_last),
        )

    def _start_of_day_in_utc(self, day: date) -> datetime:
        return datetime.combine(day, time.min, tzinfo=self._business_timezone).astimezone(UTC)


def first_of_next_month(first_of_month: date) -> date:
    if first_of_month.month == 12:
        return date(first_of_month.year + 1, 1, 1)
    return date(first_of_month.year, first_of_month.month + 1, 1)


def first_of_previous_month(first_of_month: date) -> date:
    if first_of_month.month == 1:
        return date(first_of_month.year - 1, 12, 1)
    return date(first_of_month.year, first_of_month.month - 1, 1)


def describe(relative_date: RelativeDateValue) -> str:
    if relative_date.last_n_days is not None:
        return f"last_n_days: {relative_date.last_n_days}"
    return str(relative_date.relative)
