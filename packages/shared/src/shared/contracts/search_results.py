"""One row of a search result, as shown in the dashboard table."""

import uuid
from datetime import datetime
from decimal import Decimal

from shared.contracts.contract_model import ContractModel


class TransactionSummary(ContractModel):
    transaction_id: uuid.UUID
    customer_id: str
    amount: Decimal
    currency: str
    transaction_type: str
    transaction_date: datetime
    merchant_name: str | None
    merchant_category: str | None
    payment_id: uuid.UUID
    payment_status: str
    payment_method: str
    card_brand: str | None  # None for bank (ACH) payments
    has_refund: bool
    has_chargeback: bool
    chargeback_status: str | None


class ChargebackSummary(ContractModel):
    chargeback_id: uuid.UUID
    transaction_id: uuid.UUID
    customer_id: str
    reason_code: str
    chargeback_status: str
    chargeback_amount: Decimal
    currency: str
    opened_at: datetime
    network: str | None
    latest_event_type: str | None
    transaction_date: datetime
