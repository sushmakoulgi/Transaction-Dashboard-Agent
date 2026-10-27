"""Everything about one transaction, for the drill-down view."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from shared.contracts.contract_model import ContractModel


class PaymentDetail(ContractModel):
    payment_id: uuid.UUID
    payment_method: str
    payment_status: str
    authorized_amount: Decimal | None
    captured_amount: Decimal | None
    authorized_at: datetime | None
    captured_at: datetime | None
    payment_details: dict[str, Any]


class RefundDetail(ContractModel):
    refund_id: uuid.UUID
    refund_amount: Decimal
    refund_status: str
    refunded_at: datetime
    refund_details: dict[str, Any]


class ChargebackEventDetail(ContractModel):
    chargeback_event_id: uuid.UUID
    event_type: str
    event_sequence: int
    event_date: datetime
    event_payload: dict[str, Any]


class ChargebackDetail(ContractModel):
    chargeback_id: uuid.UUID
    reason_code: str
    chargeback_amount: Decimal
    chargeback_status: str
    opened_at: datetime
    dispute_details: dict[str, Any]
    events: tuple[ChargebackEventDetail, ...]  # in event_sequence order


class TransactionDetail(ContractModel):
    transaction_id: uuid.UUID
    customer_id: str
    amount: Decimal
    currency: str
    transaction_type: str
    transaction_date: datetime
    merchant_details: dict[str, Any]
    metadata: dict[str, Any]
    payment: PaymentDetail
    refunds: tuple[RefundDetail, ...]  # oldest first
    chargeback: ChargebackDetail | None
