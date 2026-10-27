from collections import Counter, defaultdict
from datetime import UTC, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from transaction_database.enums import PaymentMethod, PaymentStatus
from transaction_database.seed.sample_data_generator import SampleDataGenerator, SampleDataset

BUSINESS_TIMEZONE = ZoneInfo("America/New_York")
REFERENCE_TIME = datetime(2026, 10, 27, 15, 0, tzinfo=UTC)
CURRENT_MONTH_START = datetime(2026, 10, 1, tzinfo=BUSINESS_TIMEZONE)


def generate_dataset(random_seed: int = 7) -> SampleDataset:
    return SampleDataGenerator(REFERENCE_TIME, BUSINESS_TIMEZONE, random_seed).generate()


@pytest.fixture(scope="module")
def dataset() -> SampleDataset:
    return generate_dataset()


def test_same_seed_and_reference_time_produce_identical_data() -> None:
    assert generate_dataset(random_seed=42) == generate_dataset(random_seed=42)


def test_different_seeds_produce_different_data() -> None:
    assert generate_dataset(random_seed=1) != generate_dataset(random_seed=2)


def test_generates_one_hundred_transactions_each_with_exactly_one_payment(
    dataset: SampleDataset,
) -> None:
    assert len(dataset.transactions) == 100
    payment_counts = Counter(payment["transaction_id"] for payment in dataset.payments)
    assert set(payment_counts) == {row["transaction_id"] for row in dataset.transactions}
    assert set(payment_counts.values()) == {1}


def test_customer_id_matches_parent_row_everywhere(dataset: SampleDataset) -> None:
    customer_by_transaction = {
        row["transaction_id"]: row["customer_id"] for row in dataset.transactions
    }
    customer_by_payment = {row["payment_id"]: row["customer_id"] for row in dataset.payments}
    for payment in dataset.payments:
        assert payment["customer_id"] == customer_by_transaction[payment["transaction_id"]]
    for refund in dataset.refunds:
        assert refund["customer_id"] == customer_by_payment[refund["original_payment_id"]]
    for chargeback in dataset.chargebacks:
        assert chargeback["customer_id"] == customer_by_transaction[chargeback["transaction_id"]]


def test_all_timestamps_are_timezone_aware_and_not_in_the_future(dataset: SampleDataset) -> None:
    for rows in dataset.rows_by_table_name().values():
        for row in rows:
            for value in row.values():
                if isinstance(value, datetime):
                    assert value.tzinfo is not None
                    assert value <= REFERENCE_TIME


def test_some_transactions_fall_in_the_current_business_month(dataset: SampleDataset) -> None:
    current_month_count = sum(
        row["transaction_date"] >= CURRENT_MONTH_START for row in dataset.transactions
    )
    assert 15 <= current_month_count <= 45


def test_payment_amounts_follow_payment_status(dataset: SampleDataset) -> None:
    for payment in dataset.payments:
        status = payment["payment_status"]
        assert (payment["captured_at"] is not None) == (status == PaymentStatus.CAPTURED)
        assert (payment["authorized_at"] is None) == (status == PaymentStatus.FAILED)


def test_refunds_only_on_captured_payments_and_never_exceed_captured_amount(
    dataset: SampleDataset,
) -> None:
    payments_by_id = {row["payment_id"]: row for row in dataset.payments}
    refunded_total_by_payment: dict[object, Decimal] = defaultdict(Decimal)
    for refund in dataset.refunds:
        payment = payments_by_id[refund["original_payment_id"]]
        assert payment["payment_status"] == PaymentStatus.CAPTURED
        assert refund["refunded_at"] >= payment["captured_at"]
        assert refund["refund_amount"] > 0
        refunded_total_by_payment[payment["payment_id"]] += refund["refund_amount"]

    assert len(refunded_total_by_payment) == 20
    for payment_id, refunded_total in refunded_total_by_payment.items():
        assert refunded_total <= payments_by_id[payment_id]["captured_amount"]


def test_chargebacks_are_unique_per_transaction_and_on_card_payments(
    dataset: SampleDataset,
) -> None:
    assert len(dataset.chargebacks) == 15
    transaction_ids = [row["transaction_id"] for row in dataset.chargebacks]
    assert len(set(transaction_ids)) == len(transaction_ids)

    payment_by_transaction = {row["transaction_id"]: row for row in dataset.payments}
    for chargeback in dataset.chargebacks:
        payment = payment_by_transaction[chargeback["transaction_id"]]
        assert payment["payment_status"] == PaymentStatus.CAPTURED
        assert payment["payment_method"] != PaymentMethod.ACH
        assert chargeback["opened_at"] >= payment["captured_at"]
        assert chargeback["dispute_details"]["network"] == payment["payment_details"]["card_brand"]


def test_about_half_of_chargebacks_open_in_the_current_month(dataset: SampleDataset) -> None:
    current_month_count = sum(
        row["opened_at"] >= CURRENT_MONTH_START for row in dataset.chargebacks
    )
    assert current_month_count >= 8


def test_each_chargeback_has_one_to_four_ordered_events_starting_with_opened(
    dataset: SampleDataset,
) -> None:
    events_by_chargeback: dict[object, list[dict[str, object]]] = defaultdict(list)
    for event in dataset.chargeback_events:
        events_by_chargeback[event["chargeback_id"]].append(event)

    assert set(events_by_chargeback) == {row["chargeback_id"] for row in dataset.chargebacks}
    for events in events_by_chargeback.values():
        assert 1 <= len(events) <= 4
        assert [event["event_sequence"] for event in events] == list(range(1, len(events) + 1))
        assert events[0]["event_type"] == "OPENED"
        event_dates = [event["event_date"] for event in events]
        assert event_dates == sorted(event_dates)  # type: ignore[type-var]


def test_rejects_naive_reference_time() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        SampleDataGenerator(datetime(2026, 10, 27), BUSINESS_TIMEZONE, random_seed=1)
