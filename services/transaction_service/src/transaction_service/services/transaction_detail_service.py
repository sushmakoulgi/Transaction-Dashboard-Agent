"""One transaction with its payment, refunds, chargeback and chargeback events."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from shared.contracts import (
    ChargebackDetail,
    ChargebackEventDetail,
    PaymentDetail,
    RefundDetail,
    TransactionDetail,
)
from transaction_database.orm_models import (
    Chargeback,
    ChargebackEvent,
    Payment,
    Refund,
    Transaction,
)
from transaction_service.filtering.business_calendar import BusinessCalendar
from transaction_service.persistence.database import ReadOnlyDatabase


class TransactionNotFoundError(LookupError):
    def __init__(self, transaction_id: uuid.UUID) -> None:
        super().__init__(f"transaction {transaction_id} not found")
        self.transaction_id = transaction_id


class TransactionDetailService:
    def __init__(self, database: ReadOnlyDatabase, calendar: BusinessCalendar) -> None:
        self._database = database
        self._calendar = calendar

    async def get_transaction_detail(self, transaction_id: uuid.UUID) -> TransactionDetail:
        """Raises `TransactionNotFoundError` for unknown IDs and transactions outside the
        current year (only the current year can be viewed)."""
        year = self._calendar.current_year_range()
        statement = (
            select(Transaction)
            .where(
                Transaction.transaction_id == transaction_id,
                Transaction.transaction_date >= year.start,
                Transaction.transaction_date < year.end,
            )
            .options(
                selectinload(Transaction.payment).selectinload(Payment.refunds),
                selectinload(Transaction.chargeback).selectinload(Chargeback.events),
            )
        )
        async with self._database.session() as session:
            transaction = (await session.execute(statement)).scalar_one_or_none()
            if transaction is None:
                raise TransactionNotFoundError(transaction_id)
            return to_transaction_detail(transaction)


def to_transaction_detail(transaction: Transaction) -> TransactionDetail:
    return TransactionDetail(
        transaction_id=transaction.transaction_id,
        customer_id=transaction.customer_id,
        amount=transaction.amount,
        currency=transaction.currency,
        transaction_type=transaction.transaction_type,
        transaction_date=transaction.transaction_date,
        merchant_details=transaction.merchant_details,
        metadata=transaction.transaction_metadata,
        payment=to_payment_detail(transaction.payment),
        refunds=tuple(
            to_refund_detail(refund)
            for refund in sorted(transaction.payment.refunds, key=lambda refund: refund.refunded_at)
        ),
        chargeback=(
            to_chargeback_detail(transaction.chargeback)
            if transaction.chargeback is not None
            else None
        ),
    )


def to_payment_detail(payment: Payment) -> PaymentDetail:
    return PaymentDetail(
        payment_id=payment.payment_id,
        payment_method=payment.payment_method,
        payment_status=payment.payment_status,
        authorized_amount=payment.authorized_amount,
        captured_amount=payment.captured_amount,
        authorized_at=payment.authorized_at,
        captured_at=payment.captured_at,
        payment_details=payment.payment_details,
    )


def to_refund_detail(refund: Refund) -> RefundDetail:
    return RefundDetail(
        refund_id=refund.refund_id,
        refund_amount=refund.refund_amount,
        refund_status=refund.refund_status,
        refunded_at=refund.refunded_at,
        refund_details=refund.refund_details,
    )


def to_chargeback_detail(chargeback: Chargeback) -> ChargebackDetail:
    return ChargebackDetail(
        chargeback_id=chargeback.chargeback_id,
        reason_code=chargeback.reason_code,
        chargeback_amount=chargeback.chargeback_amount,
        chargeback_status=chargeback.chargeback_status,
        opened_at=chargeback.opened_at,
        dispute_details=chargeback.dispute_details,
        events=tuple(to_chargeback_event_detail(event) for event in chargeback.events),
    )


def to_chargeback_event_detail(event: ChargebackEvent) -> ChargebackEventDetail:
    return ChargebackEventDetail(
        chargeback_event_id=event.chargeback_event_id,
        event_type=event.event_type,
        event_sequence=event.event_sequence,
        event_date=event.event_date,
        event_payload=event.event_payload,
    )
