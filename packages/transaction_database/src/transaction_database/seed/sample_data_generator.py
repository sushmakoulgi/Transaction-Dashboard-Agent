"""Deterministic sample data for local development.

The same `random_seed` and `reference_time` always produce the same dataset. Rows are
plain dicts keyed by column name, ready for SQLAlchemy Core bulk inserts.
"""

import random
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from transaction_database.enums import (
    CardBrand,
    ChargebackEventType,
    ChargebackStatus,
    PaymentMethod,
    PaymentStatus,
    RefundStatus,
    TransactionType,
)

Row = dict[str, Any]
CENT = Decimal("0.01")


@dataclass(frozen=True)
class SampleDataProfile:
    transaction_count: int = 100
    customer_ids: tuple[str, ...] = tuple(f"C-{number}" for number in range(1001, 1016))
    history_days: int = 90
    current_month_transaction_share: float = 0.30
    refunded_payment_share: float = 0.20
    chargeback_count: int = 15
    current_month_chargeback_share: float = 0.50


@dataclass
class SampleDataset:
    transactions: list[Row] = field(default_factory=list)
    payments: list[Row] = field(default_factory=list)
    refunds: list[Row] = field(default_factory=list)
    chargebacks: list[Row] = field(default_factory=list)
    chargeback_events: list[Row] = field(default_factory=list)

    def rows_by_table_name(self) -> dict[str, list[Row]]:
        return {
            "transactions": self.transactions,
            "payments": self.payments,
            "refunds": self.refunds,
            "chargebacks": self.chargebacks,
            "chargeback_events": self.chargeback_events,
        }


MERCHANTS: tuple[Row, ...] = (
    {
        "merchant_id": "M-001",
        "merchant_name": "Northwind Electronics",
        "merchant_category": "ELECTRONICS",
        "country": "US",
    },
    {
        "merchant_id": "M-002",
        "merchant_name": "Blue Harbor Travel",
        "merchant_category": "TRAVEL",
        "country": "US",
    },
    {
        "merchant_id": "M-003",
        "merchant_name": "Greenleaf Grocers",
        "merchant_category": "GROCERY",
        "country": "US",
    },
    {
        "merchant_id": "M-004",
        "merchant_name": "Streamline Media",
        "merchant_category": "DIGITAL_GOODS",
        "country": "GB",
    },
    {
        "merchant_id": "M-005",
        "merchant_name": "Atlas Outfitters",
        "merchant_category": "APPAREL",
        "country": "CA",
    },
    {
        "merchant_id": "M-006",
        "merchant_name": "Lumen Home Goods",
        "merchant_category": "HOME",
        "country": "DE",
    },
)

REASON_CODES_BY_CARD_BRAND: dict[CardBrand, tuple[tuple[str, str], ...]] = {
    CardBrand.VISA: (
        ("10.4", "Other Fraud - Card-Absent Environment"),
        ("13.1", "Merchandise/Services Not Received"),
        ("13.3", "Not as Described or Defective"),
        ("12.6.1", "Duplicate Processing"),
    ),
    CardBrand.MASTERCARD: (
        ("4837", "No Cardholder Authorization"),
        ("4853", "Cardholder Dispute"),
    ),
    CardBrand.AMEX: (
        ("F29", "Card Not Present"),
        ("C08", "Goods/Services Not Received"),
    ),
    CardBrand.DISCOVER: (
        ("UA02", "Fraud - Card Not Present"),
        ("AA", "Does Not Recognize"),
    ),
}

EVENT_PROGRESSION_BY_CHARGEBACK_STATUS: dict[ChargebackStatus, tuple[ChargebackEventType, ...]] = {
    ChargebackStatus.OPEN: (ChargebackEventType.OPENED,),
    ChargebackStatus.UNDER_REVIEW: (
        ChargebackEventType.OPENED,
        ChargebackEventType.EVIDENCE_REQUESTED,
        ChargebackEventType.EVIDENCE_SUBMITTED,
    ),
    ChargebackStatus.WON: (
        ChargebackEventType.OPENED,
        ChargebackEventType.EVIDENCE_REQUESTED,
        ChargebackEventType.REPRESENTMENT,
        ChargebackEventType.WON,
    ),
    ChargebackStatus.LOST: (
        ChargebackEventType.OPENED,
        ChargebackEventType.EVIDENCE_SUBMITTED,
        ChargebackEventType.PRE_ARBITRATION,
        ChargebackEventType.LOST,
    ),
    ChargebackStatus.ACCEPTED: (ChargebackEventType.OPENED, ChargebackEventType.ACCEPTED),
}

EVENT_ACTOR_BY_TYPE: dict[ChargebackEventType, str] = {
    ChargebackEventType.OPENED: "ISSUER",
    ChargebackEventType.EVIDENCE_REQUESTED: "NETWORK",
    ChargebackEventType.EVIDENCE_SUBMITTED: "MERCHANT",
    ChargebackEventType.REPRESENTMENT: "MERCHANT",
    ChargebackEventType.PRE_ARBITRATION: "ISSUER",
    ChargebackEventType.ARBITRATION: "NETWORK",
    ChargebackEventType.WON: "NETWORK",
    ChargebackEventType.LOST: "NETWORK",
    ChargebackEventType.ACCEPTED: "MERCHANT",
}

REFUND_REASONS = ("CUSTOMER_REQUEST", "ITEM_RETURNED", "DUPLICATE_ORDER", "SERVICE_ISSUE")


class SampleDataGenerator:
    def __init__(
        self,
        reference_time: datetime,
        business_timezone: ZoneInfo,
        random_seed: int,
        profile: SampleDataProfile | None = None,
    ) -> None:
        if reference_time.tzinfo is None:
            raise ValueError("reference_time must be timezone-aware")
        self._random = random.Random(random_seed)
        self._profile = profile or SampleDataProfile()
        self._reference_time = reference_time.astimezone(UTC)
        self._current_month_start = (
            reference_time.astimezone(business_timezone)
            .replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            .astimezone(UTC)
        )
        self._history_start = self._reference_time - timedelta(days=self._profile.history_days)

    def generate(self) -> SampleDataset:
        dataset = SampleDataset()
        for _ in range(self._profile.transaction_count):
            transaction = self._build_transaction()
            dataset.transactions.append(transaction)
            dataset.payments.append(self._build_payment(transaction))
        refunded_payment_ids = self._add_refunds(dataset)
        self._add_chargebacks(dataset, refunded_payment_ids)
        return dataset

    def _build_transaction(self) -> Row:
        is_in_current_month = self._random.random() < self._profile.current_month_transaction_share
        if is_in_current_month:
            transaction_date = self._random_time_between(
                self._current_month_start, self._reference_time
            )
        else:
            transaction_date = self._random_time_between(
                self._history_start, self._current_month_start
            )
        merchant = self._random.choice(MERCHANTS)
        return {
            "transaction_id": self._random_uuid(),
            "customer_id": self._random.choice(self._profile.customer_ids),
            "amount": self._random_amount(Decimal("5.00"), Decimal("1500.00")),
            "currency": self._random.choices(("USD", "EUR", "GBP"), weights=(80, 12, 8))[0],
            "transaction_type": self._random.choices(tuple(TransactionType), weights=(75, 20, 5))[
                0
            ].value,
            "transaction_date": transaction_date,
            "merchant_details": dict(merchant),
            "metadata": {
                "channel": self._random.choice(("WEB", "MOBILE_APP", "POS")),
                "device_type": self._random.choice(("DESKTOP", "IOS", "ANDROID")),
                "ip_country": merchant["country"],
            },
        }

    def _build_payment(self, transaction: Row) -> Row:
        payment_method = self._random.choices(tuple(PaymentMethod), weights=(70, 20, 10))[0]
        payment_status = self._random.choices(
            (
                PaymentStatus.CAPTURED,
                PaymentStatus.AUTHORIZED,
                PaymentStatus.VOIDED,
                PaymentStatus.FAILED,
            ),
            weights=(70, 12, 8, 10),
        )[0]
        amount: Decimal = transaction["amount"]
        is_authorized = payment_status is not PaymentStatus.FAILED
        is_captured = payment_status is PaymentStatus.CAPTURED

        authorized_at = None
        captured_at = None
        if is_authorized:
            authorized_at = self._not_after_reference_time(
                transaction["transaction_date"] + timedelta(seconds=self._random.randint(1, 30))
            )
        if is_captured and authorized_at is not None:
            captured_at = self._not_after_reference_time(
                authorized_at + timedelta(minutes=self._random.randint(5, 2880))
            )

        return {
            "payment_id": self._random_uuid(),
            "transaction_id": transaction["transaction_id"],
            "customer_id": transaction["customer_id"],
            "payment_method": payment_method.value,
            "payment_status": payment_status.value,
            "authorized_amount": amount if is_authorized else None,
            "captured_amount": amount if is_captured else None,
            "authorized_at": authorized_at,
            "captured_at": captured_at,
            "payment_details": self._build_payment_details(payment_method, payment_status),
        }

    def _build_payment_details(
        self, payment_method: PaymentMethod, payment_status: PaymentStatus
    ) -> Row:
        processor_response_code = (
            self._random.choice(("05", "51")) if payment_status is PaymentStatus.FAILED else "00"
        )
        if payment_method is PaymentMethod.ACH:
            return {
                "bank_name": self._random.choice(("First Republic Trust", "Lakeside Credit Union")),
                "account_last4": self._random_digits(4),
                "processor_response_code": processor_response_code,
            }
        card_details: Row = {
            "card_brand": self._random.choices(tuple(CardBrand), weights=(50, 30, 15, 5))[0].value,
            "card_last4": self._random_digits(4),
            "processor_response_code": processor_response_code,
        }
        if payment_method is PaymentMethod.WALLET:
            card_details["wallet_type"] = self._random.choice(("APPLE_PAY", "GOOGLE_PAY"))
        return card_details

    def _add_refunds(self, dataset: SampleDataset) -> set[uuid.UUID]:
        captured_payments = [
            payment
            for payment in dataset.payments
            if payment["payment_status"] == PaymentStatus.CAPTURED
        ]
        target_refunded_count = round(len(dataset.payments) * self._profile.refunded_payment_share)
        refunded_payments = self._random.sample(
            captured_payments, min(target_refunded_count, len(captured_payments))
        )
        for payment in refunded_payments:
            dataset.refunds.extend(self._build_refunds(payment))
        return {payment["payment_id"] for payment in refunded_payments}

    def _build_refunds(self, payment: Row) -> list[Row]:
        refund_count = self._random.randint(1, 2)
        refund_times = sorted(
            self._random_time_between(payment["captured_at"], self._reference_time)
            for _ in range(refund_count)
        )
        remaining_amount: Decimal = payment["captured_amount"]
        refunds: list[Row] = []
        for refunded_at in refund_times:
            is_full_refund = refund_count == 1 and self._random.random() < 0.5
            refund_amount = (
                remaining_amount
                if is_full_refund
                else max(
                    CENT,
                    (remaining_amount * Decimal(str(self._random.uniform(0.2, 0.6)))).quantize(
                        CENT
                    ),
                )
            )
            remaining_amount -= refund_amount
            refunds.append(
                {
                    "refund_id": self._random_uuid(),
                    "original_payment_id": payment["payment_id"],
                    "customer_id": payment["customer_id"],
                    "refund_amount": refund_amount,
                    "refund_status": self._random.choices(tuple(RefundStatus), weights=(15, 80, 5))[
                        0
                    ].value,
                    "refunded_at": refunded_at,
                    "refund_details": {
                        "reason": self._random.choice(REFUND_REASONS),
                        "initiated_by": self._random.choice(
                            ("CUSTOMER_SUPPORT", "MERCHANT_PORTAL", "API")
                        ),
                    },
                }
            )
        return refunds

    def _add_chargebacks(
        self, dataset: SampleDataset, refunded_payment_ids: set[uuid.UUID]
    ) -> None:
        eligible_pairs = [
            (transaction, payment)
            for transaction, payment in zip(dataset.transactions, dataset.payments, strict=True)
            if payment["payment_status"] == PaymentStatus.CAPTURED
            and payment["payment_method"] != PaymentMethod.ACH
            and payment["payment_id"] not in refunded_payment_ids
        ]
        selected_pairs = self._random.sample(
            eligible_pairs, min(self._profile.chargeback_count, len(eligible_pairs))
        )
        current_month_target = round(
            len(selected_pairs) * self._profile.current_month_chargeback_share
        )
        for index, (transaction, payment) in enumerate(selected_pairs):
            chargeback = self._build_chargeback(
                transaction, payment, opens_in_current_month=index < current_month_target
            )
            dataset.chargebacks.append(chargeback)
            dataset.chargeback_events.extend(self._build_chargeback_events(chargeback))

    def _build_chargeback(
        self, transaction: Row, payment: Row, opens_in_current_month: bool
    ) -> Row:
        card_brand = CardBrand(payment["payment_details"]["card_brand"])
        reason_code, reason_description = self._random.choice(
            REASON_CODES_BY_CARD_BRAND[card_brand]
        )
        opened_at = self._chargeback_opened_at(payment["captured_at"], opens_in_current_month)
        return {
            "chargeback_id": self._random_uuid(),
            "transaction_id": transaction["transaction_id"],
            "customer_id": transaction["customer_id"],
            "reason_code": reason_code,
            "chargeback_amount": transaction["amount"],
            "chargeback_status": self._random.choice(tuple(ChargebackStatus)).value,
            "opened_at": opened_at,
            "dispute_details": {
                "network": card_brand.value,
                "network_case_id": f"CASE-{self._random_digits(8)}",
                "reason_description": reason_description,
                "evidence_due_date": (opened_at + timedelta(days=20)).date().isoformat(),
            },
        }

    def _chargeback_opened_at(
        self, captured_at: datetime, opens_in_current_month: bool
    ) -> datetime:
        captured_before_current_month = captured_at < self._current_month_start
        if opens_in_current_month or not captured_before_current_month:
            window_start = max(captured_at, self._current_month_start)
            return self._random_time_between(window_start, self._reference_time)
        return self._random_time_between(captured_at, self._current_month_start)

    def _build_chargeback_events(self, chargeback: Row) -> list[Row]:
        progression = EVENT_PROGRESSION_BY_CHARGEBACK_STATUS[
            ChargebackStatus(chargeback["chargeback_status"])
        ]
        events: list[Row] = []
        event_date: datetime = chargeback["opened_at"]
        for event_sequence, event_type in enumerate(progression, start=1):
            if event_sequence > 1:
                event_date = self._not_after_reference_time(
                    event_date + timedelta(hours=self._random.randint(12, 240))
                )
            events.append(
                {
                    "chargeback_event_id": self._random_uuid(),
                    "chargeback_id": chargeback["chargeback_id"],
                    "event_type": event_type.value,
                    "event_sequence": event_sequence,
                    "event_date": event_date,
                    "event_payload": {
                        "actor": EVENT_ACTOR_BY_TYPE[event_type],
                        "note": f"{event_type.value.replace('_', ' ').title()} recorded",
                    },
                }
            )
        return events

    def _random_uuid(self) -> uuid.UUID:
        return uuid.UUID(int=self._random.getrandbits(128), version=4)

    def _random_time_between(self, start: datetime, end: datetime) -> datetime:
        return start + (end - start) * self._random.random()

    def _random_amount(self, minimum: Decimal, maximum: Decimal) -> Decimal:
        return Decimal(str(self._random.uniform(float(minimum), float(maximum)))).quantize(CENT)

    def _random_digits(self, length: int) -> str:
        return "".join(self._random.choices("0123456789", k=length))

    def _not_after_reference_time(self, moment: datetime) -> datetime:
        return min(moment, self._reference_time)
