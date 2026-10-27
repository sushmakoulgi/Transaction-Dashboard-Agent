"""What each searchable entity returns. To add a result column, add one entry here."""

from sqlalchemy import literal_column, select

from shared.contracts import ChargebackSummary, SearchableEntity, TransactionSummary
from transaction_database.orm_models import (
    Chargeback,
    ChargebackEvent,
    Payment,
    Refund,
    Transaction,
)
from transaction_service.filtering.entity_field_registries import (
    CHARGEBACK_FIELD_REGISTRY,
    TRANSACTION_CHARGEBACK,
    TRANSACTION_FIELD_REGISTRY,
    TRANSACTION_PAYMENT,
)
from transaction_service.filtering.filterable_field import Relation, RelationCardinality
from transaction_service.repositories.searchable_repository import SearchQueryDefinition

TRANSACTION_SEARCH = SearchQueryDefinition(
    registry=TRANSACTION_FIELD_REGISTRY,
    item_model=TransactionSummary,
    display_relations=(TRANSACTION_PAYMENT, TRANSACTION_CHARGEBACK),
    result_columns={
        "transaction_id": Transaction.transaction_id,
        "customer_id": Transaction.customer_id,
        "amount": Transaction.amount,
        "currency": Transaction.currency,
        "transaction_type": Transaction.transaction_type,
        "transaction_date": Transaction.transaction_date,
        "merchant_name": Transaction.merchant_details["merchant_name"].astext,
        "merchant_category": Transaction.merchant_details["merchant_category"].astext,
        "payment_id": Payment.payment_id,
        "payment_status": Payment.payment_status,
        "payment_method": Payment.payment_method,
        "card_brand": Payment.payment_details["card_brand"].astext,
        "has_refund": select(literal_column("1"))
        .select_from(Refund)
        .where(Refund.original_payment_id == Payment.payment_id)
        .correlate_except(Refund)
        .exists(),
        "has_chargeback": Chargeback.chargeback_id.is_not(None),
        "chargeback_status": Chargeback.chargeback_status,
    },
)

CHARGEBACK_TRANSACTION = Relation(
    name="transaction",
    cardinality=RelationCardinality.ONE,
    target=Transaction,
    join_condition=Transaction.transaction_id == Chargeback.transaction_id,
)

CHARGEBACK_SEARCH = SearchQueryDefinition(
    registry=CHARGEBACK_FIELD_REGISTRY,
    item_model=ChargebackSummary,
    display_relations=(CHARGEBACK_TRANSACTION,),
    result_columns={
        "chargeback_id": Chargeback.chargeback_id,
        "transaction_id": Chargeback.transaction_id,
        "customer_id": Chargeback.customer_id,
        "reason_code": Chargeback.reason_code,
        "chargeback_status": Chargeback.chargeback_status,
        "chargeback_amount": Chargeback.chargeback_amount,
        "currency": Transaction.currency,
        "opened_at": Chargeback.opened_at,
        "network": Chargeback.dispute_details["network"].astext,
        "latest_event_type": select(ChargebackEvent.event_type)
        .where(ChargebackEvent.chargeback_id == Chargeback.chargeback_id)
        .order_by(ChargebackEvent.event_sequence.desc())
        .limit(1)
        .correlate_except(ChargebackEvent)
        .scalar_subquery(),
        "transaction_date": Transaction.transaction_date,
    },
)

SEARCH_DEFINITION_BY_ENTITY: dict[
    SearchableEntity,
    SearchQueryDefinition[TransactionSummary] | SearchQueryDefinition[ChargebackSummary],
] = {
    SearchableEntity.TRANSACTIONS: TRANSACTION_SEARCH,
    SearchableEntity.CHARGEBACKS: CHARGEBACK_SEARCH,
}
