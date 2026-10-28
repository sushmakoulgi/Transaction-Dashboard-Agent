"""The filterable fields of each searchable entity. To add a filter, add one entry here.

JSON fields read a fixed key from a JSONB column, e.g. `payments.payment_details ->> 'card_brand'`.
The key comes from this file, never from the request.
"""

from enum import StrEnum

from shared.contracts import SearchableEntity
from shared.contracts.identifiers import (
    CUSTOMER_ID,
    CUSTOMER_ID_FORMAT,
    PAYMENT_ID,
    PAYMENT_ID_FORMAT,
    TRANSACTION_ID,
    TRANSACTION_ID_FORMAT,
)
from transaction_database.enums import (
    CardBrand,
    ChargebackEventType,
    ChargebackStatus,
    MerchantCategory,
    PaymentMethod,
    PaymentStatus,
    RefundStatus,
    TransactionType,
)
from transaction_database.orm_models import (
    Chargeback,
    ChargebackEvent,
    Payment,
    Refund,
    Transaction,
)
from transaction_service.filtering.field_registry import FieldRegistry
from transaction_service.filtering.field_types import FieldType
from transaction_service.filtering.filterable_field import (
    Relation,
    RelationCardinality,
    filterable_field,
    related_row_exists_field,
)


def values_of(enum_class: type[StrEnum]) -> tuple[str, ...]:
    return tuple(member.value for member in enum_class)


# --- Relations reachable from a transaction ---

TRANSACTION_PAYMENT = Relation(
    name="payment",
    cardinality=RelationCardinality.ONE,
    target=Payment,
    join_condition=Payment.transaction_id == Transaction.transaction_id,
)
TRANSACTION_CHARGEBACK = Relation(
    name="chargeback",
    cardinality=RelationCardinality.ONE,
    target=Chargeback,
    join_condition=Chargeback.transaction_id == Transaction.transaction_id,
)
TRANSACTION_REFUNDS = Relation(
    name="refunds",
    cardinality=RelationCardinality.MANY,
    target=Refund,
    join_condition=Refund.original_payment_id == Payment.payment_id,
    via=TRANSACTION_PAYMENT,
)

TRANSACTION_FIELD_REGISTRY = FieldRegistry(
    entity=SearchableEntity.TRANSACTIONS,
    root_entity=Transaction,
    default_sort_field_name="transaction_date",
    current_year_scope_field_name="transaction_date",
    fields=[
        filterable_field(
            "transaction_id",
            FieldType.STRING,
            Transaction.transaction_id,
            f"Transaction ID: {TRANSACTION_ID_FORMAT}.",
            id_format=TRANSACTION_ID,
        ),
        filterable_field(
            "customer_id",
            FieldType.STRING,
            Transaction.customer_id,
            f"Customer ID: {CUSTOMER_ID_FORMAT}.",
            id_format=CUSTOMER_ID,
        ),
        filterable_field(
            "amount",
            FieldType.DECIMAL,
            Transaction.amount,
            "Transaction amount in its own currency.",
            is_sortable=True,
        ),
        filterable_field(
            "currency", FieldType.STRING, Transaction.currency, "ISO currency code, e.g. USD."
        ),
        filterable_field(
            "transaction_type",
            FieldType.ENUM,
            Transaction.transaction_type,
            "Kind of transaction.",
            enum_values=values_of(TransactionType),
        ),
        filterable_field(
            "transaction_date",
            FieldType.TIMESTAMP,
            Transaction.transaction_date,
            "When the transaction happened.",
            is_sortable=True,
        ),
        filterable_field(
            "merchant_category",
            FieldType.ENUM,
            Transaction.merchant_details["merchant_category"].astext,
            "Merchant's business category.",
            enum_values=values_of(MerchantCategory),
        ),
        filterable_field(
            "merchant_name",
            FieldType.STRING,
            Transaction.merchant_details["merchant_name"].astext,
            "Merchant name; use 'contains' for partial matches.",
        ),
        filterable_field(
            "merchant_country",
            FieldType.STRING,
            Transaction.merchant_details["country"].astext,
            "Merchant's ISO country code, e.g. US.",
        ),
        filterable_field(
            "payment_id",
            FieldType.STRING,
            Payment.payment_id,
            f"ID of the payment for this transaction: {PAYMENT_ID_FORMAT}.",
            relation=TRANSACTION_PAYMENT,
            id_format=PAYMENT_ID,
        ),
        filterable_field(
            "payment_status",
            FieldType.ENUM,
            Payment.payment_status,
            "Payment lifecycle status.",
            relation=TRANSACTION_PAYMENT,
            enum_values=values_of(PaymentStatus),
        ),
        filterable_field(
            "payment_method",
            FieldType.ENUM,
            Payment.payment_method,
            "How the customer paid.",
            relation=TRANSACTION_PAYMENT,
            enum_values=values_of(PaymentMethod),
        ),
        filterable_field(
            "card_brand",
            FieldType.ENUM,
            Payment.payment_details["card_brand"].astext,
            "Card network of a card or wallet payment.",
            relation=TRANSACTION_PAYMENT,
            enum_values=values_of(CardBrand),
        ),
        filterable_field(
            "captured_at",
            FieldType.TIMESTAMP,
            Payment.captured_at,
            "When the payment was captured; null if never captured.",
            relation=TRANSACTION_PAYMENT,
            is_nullable=True,
        ),
        related_row_exists_field(
            "has_chargeback",
            TRANSACTION_CHARGEBACK,
            Chargeback.chargeback_id,
            "Whether the transaction has a chargeback.",
        ),
        filterable_field(
            "chargeback_status",
            FieldType.ENUM,
            Chargeback.chargeback_status,
            "Status of the transaction's chargeback.",
            relation=TRANSACTION_CHARGEBACK,
            enum_values=values_of(ChargebackStatus),
        ),
        filterable_field(
            "chargeback_reason_code",
            FieldType.STRING,
            Chargeback.reason_code,
            "Card-network reason code of the chargeback, e.g. 10.4.",
            relation=TRANSACTION_CHARGEBACK,
        ),
        filterable_field(
            "chargeback_opened_at",
            FieldType.TIMESTAMP,
            Chargeback.opened_at,
            "When the transaction's chargeback was opened.",
            relation=TRANSACTION_CHARGEBACK,
            is_sortable=True,
        ),
        related_row_exists_field(
            "has_refund",
            TRANSACTION_REFUNDS,
            Refund.refund_id,
            "Whether the transaction's payment has at least one refund.",
        ),
        filterable_field(
            "refund_status",
            FieldType.ENUM,
            Refund.refund_status,
            "Matches if any refund of the payment has this status.",
            relation=TRANSACTION_REFUNDS,
            enum_values=values_of(RefundStatus),
        ),
        filterable_field(
            "refunded_at",
            FieldType.TIMESTAMP,
            Refund.refunded_at,
            "Matches if any refund of the payment happened at this time.",
            relation=TRANSACTION_REFUNDS,
        ),
    ],
)

# --- Relations reachable from a chargeback ---

CHARGEBACK_EVENTS = Relation(
    name="chargeback_events",
    cardinality=RelationCardinality.MANY,
    target=ChargebackEvent,
    join_condition=ChargebackEvent.chargeback_id == Chargeback.chargeback_id,
)

CHARGEBACK_FIELD_REGISTRY = FieldRegistry(
    entity=SearchableEntity.CHARGEBACKS,
    root_entity=Chargeback,
    default_sort_field_name="opened_at",
    current_year_scope_field_name="opened_at",
    fields=[
        filterable_field(
            "chargeback_id", FieldType.UUID, Chargeback.chargeback_id, "Chargeback ID."
        ),
        filterable_field(
            "transaction_id",
            FieldType.STRING,
            Chargeback.transaction_id,
            f"ID of the disputed transaction: {TRANSACTION_ID_FORMAT}.",
            id_format=TRANSACTION_ID,
        ),
        filterable_field(
            "customer_id",
            FieldType.STRING,
            Chargeback.customer_id,
            f"Customer ID: {CUSTOMER_ID_FORMAT}.",
            id_format=CUSTOMER_ID,
        ),
        filterable_field(
            "reason_code",
            FieldType.STRING,
            Chargeback.reason_code,
            "Card-network reason code, e.g. 10.4.",
        ),
        filterable_field(
            "chargeback_status",
            FieldType.ENUM,
            Chargeback.chargeback_status,
            "Current chargeback status.",
            enum_values=values_of(ChargebackStatus),
        ),
        filterable_field(
            "chargeback_amount",
            FieldType.DECIMAL,
            Chargeback.chargeback_amount,
            "Disputed amount.",
            is_sortable=True,
        ),
        filterable_field(
            "opened_at",
            FieldType.TIMESTAMP,
            Chargeback.opened_at,
            "When the chargeback was opened. Use this for 'chargebacks this month'.",
            is_sortable=True,
        ),
        filterable_field(
            "network",
            FieldType.ENUM,
            Chargeback.dispute_details["network"].astext,
            "Card network handling the dispute.",
            enum_values=values_of(CardBrand),
        ),
        filterable_field(
            "chargeback_event_type",
            FieldType.ENUM,
            ChargebackEvent.event_type,
            "Matches if any event of the chargeback has this type.",
            relation=CHARGEBACK_EVENTS,
            enum_values=values_of(ChargebackEventType),
        ),
        filterable_field(
            "chargeback_event_date",
            FieldType.TIMESTAMP,
            ChargebackEvent.event_date,
            "Matches if any event of the chargeback happened at this time.",
            relation=CHARGEBACK_EVENTS,
        ),
    ],
)

FIELD_REGISTRY_BY_ENTITY: dict[SearchableEntity, FieldRegistry] = {
    SearchableEntity.TRANSACTIONS: TRANSACTION_FIELD_REGISTRY,
    SearchableEntity.CHARGEBACKS: CHARGEBACK_FIELD_REGISTRY,
}
