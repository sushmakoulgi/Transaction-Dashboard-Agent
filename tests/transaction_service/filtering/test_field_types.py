import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from shared.contracts.filter_expression import ScalarFilterValue
from transaction_service.filtering.field_types import (
    CoercedValue,
    FieldType,
    FieldValueCoercer,
    ValueCoercionError,
)

PAYMENT_STATUSES = ("AUTHORIZED", "CAPTURED", "VOIDED", "FAILED")
SAMPLE_UUID = "3f2b8c1e-9d4a-4e6b-8f1a-2c3d4e5f6a7b"


@pytest.fixture
def coercer() -> FieldValueCoercer:
    return FieldValueCoercer(business_timezone=ZoneInfo("America/New_York"))


@pytest.mark.parametrize(
    ("field_type", "raw_value", "expected"),
    [
        (FieldType.STRING, "10001641", "10001641"),
        (FieldType.DECIMAL, 500, Decimal("500")),
        (FieldType.DECIMAL, 12.5, Decimal("12.5")),
        (FieldType.DECIMAL, "1499.99", Decimal("1499.99")),
        (FieldType.DECIMAL, 0.1, Decimal("0.1")),  # no binary float noise
        (FieldType.INTEGER, 3, 3),
        (FieldType.INTEGER, "4", 4),
        (FieldType.INTEGER, 2.0, 2),
        (FieldType.BOOLEAN, True, True),
        (FieldType.BOOLEAN, "false", False),
        (FieldType.BOOLEAN, " TRUE ", True),
        (FieldType.UUID, SAMPLE_UUID, uuid.UUID(SAMPLE_UUID)),
        (FieldType.UUID, SAMPLE_UUID.upper(), uuid.UUID(SAMPLE_UUID)),
        (FieldType.ENUM, "CAPTURED", "CAPTURED"),
        (FieldType.ENUM, "captured", "CAPTURED"),
        (FieldType.TIMESTAMP, "2026-10-15", date(2026, 10, 15)),
        (
            FieldType.TIMESTAMP,
            "2026-10-15T09:30:00-04:00",
            datetime(2026, 10, 15, 13, 30, tzinfo=UTC),
        ),
        (FieldType.TIMESTAMP, "2026-10-15T13:30:00Z", datetime(2026, 10, 15, 13, 30, tzinfo=UTC)),
    ],
)
def test_converts_valid_values(
    coercer: FieldValueCoercer,
    field_type: FieldType,
    raw_value: ScalarFilterValue,
    expected: CoercedValue,
) -> None:
    assert coercer.coerce(field_type, raw_value, PAYMENT_STATUSES) == expected


@pytest.mark.parametrize(
    ("field_type", "raw_value"),
    [
        (FieldType.STRING, 42),
        (FieldType.STRING, "x" * 201),
        (FieldType.DECIMAL, True),
        (FieldType.DECIMAL, "five hundred"),
        (FieldType.DECIMAL, "NaN"),
        (FieldType.DECIMAL, "Infinity"),
        (FieldType.DECIMAL, float("inf")),
        (FieldType.INTEGER, 2.5),
        (FieldType.INTEGER, False),
        (FieldType.BOOLEAN, 1),
        (FieldType.BOOLEAN, "yes"),
        (FieldType.UUID, "not-a-uuid"),
        (FieldType.UUID, 123),
        (FieldType.ENUM, "SETTLED"),
        (FieldType.ENUM, 1),
        (FieldType.TIMESTAMP, "15/10/2026"),
        (FieldType.TIMESTAMP, "2026-13-01"),
        (FieldType.TIMESTAMP, 1760000000),
    ],
)
def test_rejects_invalid_values(
    coercer: FieldValueCoercer, field_type: FieldType, raw_value: ScalarFilterValue
) -> None:
    with pytest.raises(ValueCoercionError):
        coercer.coerce(field_type, raw_value, PAYMENT_STATUSES)


def test_naive_date_time_is_read_in_business_timezone(coercer: FieldValueCoercer) -> None:
    # New York is on EDT (UTC-4) in October and EST (UTC-5) in December.
    assert coercer.coerce(FieldType.TIMESTAMP, "2026-10-15T09:00:00") == datetime(
        2026, 10, 15, 13, 0, tzinfo=UTC
    )
    assert coercer.coerce(FieldType.TIMESTAMP, "2026-12-15T09:00:00") == datetime(
        2026, 12, 15, 14, 0, tzinfo=UTC
    )


def test_unknown_enum_value_suggests_allowed_values(coercer: FieldValueCoercer) -> None:
    with pytest.raises(ValueCoercionError) as error:
        coercer.coerce(FieldType.ENUM, "SETTLED", PAYMENT_STATUSES)
    assert error.value.message == "'SETTLED' is not an allowed value"
    assert error.value.suggestions == PAYMENT_STATUSES


def test_error_messages_name_the_received_value(coercer: FieldValueCoercer) -> None:
    with pytest.raises(ValueCoercionError, match="expected a number, got boolean true"):
        coercer.coerce(FieldType.DECIMAL, True)
    with pytest.raises(ValueCoercionError, match="such as 2026-10-15"):
        coercer.coerce(FieldType.TIMESTAMP, "15/10/2026")


def test_every_field_type_has_a_coercer(coercer: FieldValueCoercer) -> None:
    sample_by_field_type: dict[FieldType, ScalarFilterValue] = {
        FieldType.STRING: "a",
        FieldType.DECIMAL: 1,
        FieldType.INTEGER: 1,
        FieldType.BOOLEAN: True,
        FieldType.UUID: SAMPLE_UUID,
        FieldType.ENUM: "CAPTURED",
        FieldType.TIMESTAMP: "2026-10-15",
    }
    assert set(sample_by_field_type) == set(FieldType)
    for field_type, raw_value in sample_by_field_type.items():
        coercer.coerce(field_type, raw_value, PAYMENT_STATUSES)
