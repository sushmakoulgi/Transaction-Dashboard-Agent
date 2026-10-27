import json

import pytest
from sqlalchemy.dialects import postgresql

from shared.contracts import Operator, SearchableEntity
from transaction_database.orm_models import Payment, Transaction
from transaction_service.filtering.entity_field_registries import (
    CHARGEBACK_FIELD_REGISTRY,
    FIELD_REGISTRY_BY_ENTITY,
    TRANSACTION_FIELD_REGISTRY,
    TRANSACTION_PAYMENT,
    TRANSACTION_REFUNDS,
)
from transaction_service.filtering.field_registry import FieldRegistry
from transaction_service.filtering.field_types import FieldType
from transaction_service.filtering.filterable_field import (
    FilterableField,
    RelationCardinality,
    filterable_field,
)

ALL_REGISTRIES = list(FIELD_REGISTRY_BY_ENTITY.values())


def test_every_searchable_entity_has_a_registry() -> None:
    assert set(FIELD_REGISTRY_BY_ENTITY) == set(SearchableEntity)


def test_transaction_registry_has_the_designed_fields() -> None:
    assert set(TRANSACTION_FIELD_REGISTRY.field_names) == {
        "transaction_id", "customer_id", "amount", "currency", "transaction_type",
        "transaction_date", "merchant_category", "merchant_name", "merchant_country",
        "payment_id", "payment_status", "payment_method", "card_brand", "captured_at",
        "has_chargeback", "chargeback_status", "chargeback_reason_code", "chargeback_opened_at",
        "has_refund", "refund_status", "refunded_at",
    }  # fmt: skip


def test_chargeback_registry_has_the_designed_fields() -> None:
    assert set(CHARGEBACK_FIELD_REGISTRY.field_names) == {
        "chargeback_id", "transaction_id", "customer_id", "reason_code", "chargeback_status",
        "chargeback_amount", "opened_at", "network", "chargeback_event_type",
        "chargeback_event_date",
    }  # fmt: skip


def test_default_sort_is_the_entity_date() -> None:
    assert TRANSACTION_FIELD_REGISTRY.default_sort_field.name == "transaction_date"
    assert CHARGEBACK_FIELD_REGISTRY.default_sort_field.name == "opened_at"


def test_searches_are_limited_to_the_current_year_on_the_entity_date() -> None:
    assert TRANSACTION_FIELD_REGISTRY.current_year_scope_field.name == "transaction_date"
    assert CHARGEBACK_FIELD_REGISTRY.current_year_scope_field.name == "opened_at"


@pytest.mark.parametrize("scope_field_name", ["amount", "captured_at", "missing"])
def test_current_year_scope_must_be_a_root_timestamp(scope_field_name: str) -> None:
    fields = [
        filterable_field(
            "transaction_date",
            FieldType.TIMESTAMP,
            Transaction.transaction_date,
            "x",
            is_sortable=True,
        ),
        filterable_field("amount", FieldType.DECIMAL, Transaction.amount, "x"),
        filterable_field(
            "captured_at",
            FieldType.TIMESTAMP,
            Payment.captured_at,
            "x",
            relation=TRANSACTION_PAYMENT,
        ),
    ]
    with pytest.raises(ValueError, match="current-year scope field"):
        FieldRegistry(
            SearchableEntity.TRANSACTIONS, Transaction, "transaction_date", scope_field_name, fields
        )


@pytest.mark.parametrize("registry", ALL_REGISTRIES, ids=lambda registry: registry.entity)
def test_field_names_are_valid_contract_field_names(registry: FieldRegistry) -> None:
    for name in registry.field_names:
        assert name.isidentifier()
        assert name == name.lower()


@pytest.mark.parametrize(
    ("unknown_name", "expected_suggestion"),
    [
        ("cust_id", "customer_id"),
        ("paymnt_status", "payment_status"),
        ("chargeback_date", "chargeback_opened_at"),
        ("merchant", "merchant_name"),
    ],
)
def test_suggests_close_field_names(unknown_name: str, expected_suggestion: str) -> None:
    assert expected_suggestion in TRANSACTION_FIELD_REGISTRY.suggest_similar_names(unknown_name)


def test_no_suggestions_for_unrelated_names() -> None:
    assert TRANSACTION_FIELD_REGISTRY.suggest_similar_names("zzzz") == []


def test_unknown_field_is_not_found() -> None:
    assert TRANSACTION_FIELD_REGISTRY.find_field("password") is None


def test_json_fields_read_a_fixed_key_from_the_jsonb_column() -> None:
    card_brand = TRANSACTION_FIELD_REGISTRY.find_field("card_brand")
    assert card_brand is not None
    compiled = card_brand.column.compile(
        dialect=postgresql.dialect(),  # type: ignore[no-untyped-call]
        compile_kwargs={"literal_binds": True},
    )
    assert str(compiled) == "payments.payment_details ->> 'card_brand'"


def test_one_to_one_fields_share_one_relation_object() -> None:
    payment_fields = [
        TRANSACTION_FIELD_REGISTRY.find_field(name)
        for name in ("payment_id", "payment_status", "payment_method", "card_brand")
    ]
    assert all(
        field is not None and field.relation is TRANSACTION_PAYMENT for field in payment_fields
    )


def test_refunds_are_one_to_many_and_reached_through_the_payment() -> None:
    assert TRANSACTION_REFUNDS.cardinality is RelationCardinality.MANY
    assert TRANSACTION_REFUNDS.via is TRANSACTION_PAYMENT


def test_presence_fields_only_allow_exists() -> None:
    for name in ("has_chargeback", "has_refund"):
        field = TRANSACTION_FIELD_REGISTRY.find_field(name)
        assert field is not None
        assert field.operators == frozenset({Operator.EXISTS})


def test_operators_follow_field_type() -> None:
    amount = TRANSACTION_FIELD_REGISTRY.find_field("amount")
    status = TRANSACTION_FIELD_REGISTRY.find_field("payment_status")
    captured_at = TRANSACTION_FIELD_REGISTRY.find_field("captured_at")
    assert amount is not None and status is not None and captured_at is not None
    assert Operator.GREATER_THAN in amount.operators
    assert Operator.CONTAINS not in amount.operators
    assert Operator.IN in status.operators
    assert Operator.GREATER_THAN not in status.operators
    assert Operator.IS_NULL in captured_at.operators


def test_describe_fields_gives_the_llm_everything_it_needs() -> None:
    descriptions = {item.name: item for item in TRANSACTION_FIELD_REGISTRY.describe_fields()}
    card_brand = descriptions["card_brand"]
    assert card_brand.field_type == "enum"
    assert "VISA" in card_brand.allowed_values
    assert descriptions["transaction_date"].is_sortable
    json.dumps([item.model_dump(mode="json") for item in descriptions.values()])


def test_rejects_duplicate_field_names() -> None:
    field = filterable_field("amount", FieldType.DECIMAL, Transaction.amount, "x", is_sortable=True)
    with pytest.raises(ValueError, match="duplicate field 'amount'"):
        FieldRegistry(
            SearchableEntity.TRANSACTIONS, Transaction, "amount", "amount", [field, field]
        )


def test_rejects_default_sort_on_a_non_sortable_field() -> None:
    field = filterable_field("amount", FieldType.DECIMAL, Transaction.amount, "x")
    with pytest.raises(ValueError, match="must be sortable"):
        FieldRegistry(SearchableEntity.TRANSACTIONS, Transaction, "amount", "amount", [field])


def test_enum_field_needs_allowed_values() -> None:
    with pytest.raises(ValueError, match="enum_values"):
        filterable_field("payment_status", FieldType.ENUM, Payment.payment_status, "x")


def test_exists_needs_a_relation() -> None:
    with pytest.raises(ValueError, match="'exists' needs"):
        FilterableField(
            name="has_amount",
            description="x",
            field_type=FieldType.BOOLEAN,
            column=Transaction.amount,
            operators=frozenset({Operator.EXISTS}),
        )


def test_one_to_many_fields_cannot_be_sortable() -> None:
    with pytest.raises(ValueError, match="can't sort"):
        filterable_field(
            "refunded_at",
            FieldType.TIMESTAMP,
            Transaction.amount,
            "x",
            relation=TRANSACTION_REFUNDS,
            is_sortable=True,
        )
