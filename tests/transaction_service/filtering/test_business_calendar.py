from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest

from shared.clock import FixedClock
from shared.contracts import RelativeDatePeriod, RelativeDateValue
from transaction_service.filtering.business_calendar import (
    BusinessCalendar,
    DateOutsideAllowedRangeError,
    DateRange,
)

NEW_YORK = ZoneInfo("America/New_York")


def calendar_at(utc_moment: datetime) -> BusinessCalendar:
    return BusinessCalendar(NEW_YORK, FixedClock(utc_moment))


def utc(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, tzinfo=UTC)


def relative(period: str) -> RelativeDateValue:
    return RelativeDateValue(relative=RelativeDatePeriod(period))


# 2026-10-27 11:00 in New York (EDT, UTC-4). DST ends on 2026-11-01.
OCTOBER_27 = calendar_at(utc(2026, 10, 27, 15))


@pytest.mark.parametrize(
    ("relative_date", "expected"),
    [
        (relative("today"), DateRange(utc(2026, 10, 27, 4), utc(2026, 10, 28, 4))),
        (relative("yesterday"), DateRange(utc(2026, 10, 26, 4), utc(2026, 10, 27, 4))),
        (relative("this_month"), DateRange(utc(2026, 10, 1, 4), utc(2026, 11, 1, 4))),
        (relative("last_month"), DateRange(utc(2026, 9, 1, 4), utc(2026, 10, 1, 4))),
        # Jan 1 is EST (UTC-5) on both ends of the year.
        (relative("this_year"), DateRange(utc(2026, 1, 1, 5), utc(2027, 1, 1, 5))),
        (RelativeDateValue(last_n_days=1), DateRange(utc(2026, 10, 27, 4), utc(2026, 10, 28, 4))),
        (RelativeDateValue(last_n_days=7), DateRange(utc(2026, 10, 21, 4), utc(2026, 10, 28, 4))),
    ],
)
def test_resolves_relative_dates_in_new_york_time(
    relative_date: RelativeDateValue, expected: DateRange
) -> None:
    assert OCTOBER_27.resolve(relative_date) == expected


def test_month_spanning_the_end_of_daylight_saving_time() -> None:
    november_15 = calendar_at(utc(2026, 11, 15, 17))
    # Starts in EDT (UTC-4), ends in EST (UTC-5).
    assert november_15.resolve(relative("this_month")) == DateRange(
        utc(2026, 11, 1, 4), utc(2026, 12, 1, 5)
    )


def test_today_follows_new_york_not_utc() -> None:
    # 02:00 UTC on Oct 28 is still 22:00 on Oct 27 in New York.
    late_evening = calendar_at(utc(2026, 10, 28, 2))
    assert late_evening.today() == date(2026, 10, 27)


def test_current_year_follows_new_york_at_new_year() -> None:
    # 03:00 UTC on 2027-01-01 is still 22:00 on 2026-12-31 in New York.
    new_years_eve = calendar_at(utc(2027, 1, 1, 3))
    assert new_years_eve.today() == date(2026, 12, 31)
    assert new_years_eve.resolve(relative("this_month")) == DateRange(
        utc(2026, 12, 1, 5), utc(2027, 1, 1, 5)
    )


def test_day_range_covers_the_whole_business_day() -> None:
    assert OCTOBER_27.day_range(date(2026, 10, 15)) == DateRange(
        utc(2026, 10, 15, 4), utc(2026, 10, 16, 4)
    )


# --- Current-year limit ---

JANUARY_10 = calendar_at(utc(2026, 1, 10, 17))
JANUARY_1 = calendar_at(utc(2026, 1, 1, 17))


def test_ranges_inside_the_current_year_are_allowed_in_january() -> None:
    JANUARY_10.resolve(relative("this_month"))
    JANUARY_10.resolve(relative("this_year"))
    JANUARY_10.resolve(relative("yesterday"))
    JANUARY_10.resolve(RelativeDateValue(last_n_days=10))


@pytest.mark.parametrize(
    ("calendar", "relative_date"),
    [
        (JANUARY_10, relative("last_month")),
        (JANUARY_10, RelativeDateValue(last_n_days=11)),
        (JANUARY_1, relative("yesterday")),
        (OCTOBER_27, RelativeDateValue(last_n_days=366)),
    ],
)
def test_rejects_relative_ranges_that_reach_into_the_previous_year(
    calendar: BusinessCalendar, relative_date: RelativeDateValue
) -> None:
    with pytest.raises(DateOutsideAllowedRangeError):
        calendar.resolve(relative_date)


def test_error_message_explains_the_limit() -> None:
    with pytest.raises(DateOutsideAllowedRangeError) as error:
        JANUARY_10.resolve(relative("last_month"))
    assert error.value.message == (
        "last_month falls outside 2026; only dates from 2026-01-01 to 2026-12-31 can be searched"
    )


@pytest.mark.parametrize(
    "value",
    [
        date(2026, 1, 1),
        date(2026, 12, 31),
        datetime(2026, 1, 1, 0, 0, tzinfo=NEW_YORK),
        datetime(2026, 12, 31, 23, 59, tzinfo=NEW_YORK),
    ],
)
def test_explicit_dates_inside_the_current_year_are_allowed(value: date | datetime) -> None:
    OCTOBER_27.ensure_in_current_year(value)


@pytest.mark.parametrize(
    "value",
    [
        date(2025, 12, 31),
        date(2027, 1, 1),
        datetime(2025, 12, 31, 23, 59, tzinfo=NEW_YORK),
        datetime(2027, 1, 1, 0, 0, tzinfo=NEW_YORK),
        # 04:00 UTC on Jan 1 2026 is still Dec 31 2025 in New York.
        utc(2026, 1, 1, 4),
    ],
)
def test_rejects_explicit_dates_outside_the_current_year(value: date | datetime) -> None:
    with pytest.raises(DateOutsideAllowedRangeError, match="falls outside 2026"):
        OCTOBER_27.ensure_in_current_year(value)


def test_fixed_clock_needs_a_timezone() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        FixedClock(datetime(2026, 10, 27))
