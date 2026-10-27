"""Convert raw filter values (as sent by the LLM) into each field's real Python type.

Every failure raises `ValueCoercionError` with a message, and suggestions where useful,
that the LLM can act on.
"""

import math
import uuid
from collections.abc import Callable, Sequence
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from zoneinfo import ZoneInfo

from shared.contracts.filter_expression import ScalarFilterValue

MAX_STRING_LENGTH = 200

CoercedValue = str | Decimal | int | bool | uuid.UUID | date | datetime


class FieldType(StrEnum):
    STRING = "string"
    DECIMAL = "decimal"
    INTEGER = "integer"
    BOOLEAN = "boolean"
    UUID = "uuid"
    ENUM = "enum"
    TIMESTAMP = "timestamp"


class ValueCoercionError(ValueError):
    def __init__(self, message: str, suggestions: Sequence[str] = ()) -> None:
        super().__init__(message)
        self.message = message
        self.suggestions = tuple(suggestions)


class FieldValueCoercer:
    """Converts one scalar value for a given field type.

    Timestamps: a date-only string ("2026-10-15") stays a `date`, so callers can treat it as
    a whole business day. A date-time without an offset is read in the business timezone.
    Date-times are returned timezone-aware, in UTC.
    """

    def __init__(self, business_timezone: ZoneInfo) -> None:
        self._business_timezone = business_timezone
        self._coercer_by_field_type: dict[
            FieldType, Callable[[ScalarFilterValue, Sequence[str]], CoercedValue]
        ] = {
            FieldType.STRING: self._coerce_string,
            FieldType.DECIMAL: self._coerce_decimal,
            FieldType.INTEGER: self._coerce_integer,
            FieldType.BOOLEAN: self._coerce_boolean,
            FieldType.UUID: self._coerce_uuid,
            FieldType.ENUM: self._coerce_enum,
            FieldType.TIMESTAMP: self._coerce_timestamp,
        }

    def coerce(
        self,
        field_type: FieldType,
        raw_value: ScalarFilterValue,
        enum_values: Sequence[str] = (),
    ) -> CoercedValue:
        return self._coercer_by_field_type[field_type](raw_value, enum_values)

    def _coerce_string(self, raw_value: ScalarFilterValue, _: Sequence[str]) -> str:
        if not isinstance(raw_value, str):
            raise ValueCoercionError(f"expected text, got {describe(raw_value)}")
        if len(raw_value) > MAX_STRING_LENGTH:
            raise ValueCoercionError(f"text is longer than {MAX_STRING_LENGTH} characters")
        return raw_value

    def _coerce_decimal(self, raw_value: ScalarFilterValue, _: Sequence[str]) -> Decimal:
        if isinstance(raw_value, bool):
            raise ValueCoercionError(f"expected a number, got {describe(raw_value)}")
        if isinstance(raw_value, float) and not math.isfinite(raw_value):
            raise ValueCoercionError("expected a finite number")
        try:
            number = Decimal(str(raw_value).strip())
        except InvalidOperation:
            raise ValueCoercionError(f"expected a number, got {describe(raw_value)}") from None
        if not number.is_finite():
            raise ValueCoercionError("expected a finite number")
        return number

    def _coerce_integer(self, raw_value: ScalarFilterValue, enum_values: Sequence[str]) -> int:
        number = self._coerce_decimal(raw_value, enum_values)
        if number != number.to_integral_value():
            raise ValueCoercionError(f"expected a whole number, got {describe(raw_value)}")
        return int(number)

    def _coerce_boolean(self, raw_value: ScalarFilterValue, _: Sequence[str]) -> bool:
        if isinstance(raw_value, bool):
            return raw_value
        if isinstance(raw_value, str) and raw_value.strip().lower() in ("true", "false"):
            return raw_value.strip().lower() == "true"
        raise ValueCoercionError(
            f"expected true or false, got {describe(raw_value)}", suggestions=("true", "false")
        )

    def _coerce_uuid(self, raw_value: ScalarFilterValue, _: Sequence[str]) -> uuid.UUID:
        if isinstance(raw_value, str):
            try:
                return uuid.UUID(raw_value.strip())
            except ValueError:
                pass
        raise ValueCoercionError(f"expected an ID in UUID format, got {describe(raw_value)}")

    def _coerce_enum(self, raw_value: ScalarFilterValue, enum_values: Sequence[str]) -> str:
        if isinstance(raw_value, str):
            canonical_by_normalized = {value.lower(): value for value in enum_values}
            match = canonical_by_normalized.get(raw_value.strip().lower())
            if match is not None:
                return match
        raise ValueCoercionError(
            f"{describe(raw_value)} is not an allowed value", suggestions=enum_values
        )

    def _coerce_timestamp(self, raw_value: ScalarFilterValue, _: Sequence[str]) -> date | datetime:
        if not isinstance(raw_value, str):
            raise ValueCoercionError(
                f"expected an ISO 8601 date or date-time, got {describe(raw_value)}"
            )
        text = raw_value.strip()
        try:
            if len(text) == len("YYYY-MM-DD"):
                return date.fromisoformat(text)
            moment = datetime.fromisoformat(text)
        except ValueError:
            raise ValueCoercionError(
                f"expected an ISO 8601 date or date-time such as 2026-10-15 or "
                f"2026-10-15T09:30:00-04:00, got {describe(raw_value)}"
            ) from None
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=self._business_timezone)
        return moment.astimezone(ZoneInfo("UTC"))


def describe(raw_value: ScalarFilterValue) -> str:
    if isinstance(raw_value, bool):
        return f"boolean {str(raw_value).lower()}"
    if isinstance(raw_value, str):
        return f"'{raw_value}'"
    return f"number {raw_value}"
