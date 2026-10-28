"""SQLAlchemy models for the transactions database.

`customer_id` is copied onto payments, refunds and chargebacks. Composite foreign keys
guarantee every copy matches its parent row.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CHAR,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

MONEY = Numeric(12, 2)
EMPTY_JSON_OBJECT = text("'{}'::jsonb")


class Base(DeclarativeBase):
    pass


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        UniqueConstraint("transaction_id", "customer_id", name="uq_transactions_id_customer"),
        Index("ix_transactions_customer_id", "customer_id"),
        Index("ix_transactions_transaction_date", "transaction_date"),
        Index("ix_transactions_merchant_details", "merchant_details", postgresql_using="gin"),
    )

    transaction_id: Mapped[str] = mapped_column(Text, primary_key=True)  # 15 digits
    customer_id: Mapped[str] = mapped_column(Text)
    amount: Mapped[Decimal] = mapped_column(MONEY)
    currency: Mapped[str] = mapped_column(CHAR(3))
    transaction_type: Mapped[str] = mapped_column(Text)
    transaction_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    merchant_details: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=EMPTY_JSON_OBJECT
    )
    # `metadata` is reserved on declarative models, so the attribute name differs from the column.
    transaction_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, server_default=EMPTY_JSON_OBJECT
    )

    payment: Mapped["Payment"] = relationship(back_populates="transaction")
    chargeback: Mapped["Chargeback | None"] = relationship(back_populates="transaction")


class Payment(Base):
    __tablename__ = "payments"
    __table_args__ = (
        UniqueConstraint("transaction_id", name="uq_payments_transaction_id"),
        UniqueConstraint("payment_id", "customer_id", name="uq_payments_id_customer"),
        ForeignKeyConstraint(
            ["transaction_id", "customer_id"],
            ["transactions.transaction_id", "transactions.customer_id"],
            name="fk_payments_transaction_customer",
        ),
        Index("ix_payments_customer_id", "customer_id"),
        Index("ix_payments_payment_details", "payment_details", postgresql_using="gin"),
    )

    payment_id: Mapped[str] = mapped_column(Text, primary_key=True)  # 12 alphanumeric
    transaction_id: Mapped[str] = mapped_column(Text)
    customer_id: Mapped[str] = mapped_column(Text)
    payment_method: Mapped[str] = mapped_column(Text)
    payment_status: Mapped[str] = mapped_column(Text)
    authorized_amount: Mapped[Decimal | None] = mapped_column(MONEY)
    captured_amount: Mapped[Decimal | None] = mapped_column(MONEY)
    authorized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    payment_details: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=EMPTY_JSON_OBJECT)

    transaction: Mapped[Transaction] = relationship(back_populates="payment")
    refunds: Mapped[list["Refund"]] = relationship(back_populates="original_payment")


class Refund(Base):
    __tablename__ = "refunds"
    __table_args__ = (
        ForeignKeyConstraint(
            ["original_payment_id", "customer_id"],
            ["payments.payment_id", "payments.customer_id"],
            name="fk_refunds_payment_customer",
        ),
        Index("ix_refunds_original_payment_id", "original_payment_id"),
        Index("ix_refunds_customer_id", "customer_id"),
    )

    refund_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    original_payment_id: Mapped[str] = mapped_column(Text)
    customer_id: Mapped[str] = mapped_column(Text)
    refund_amount: Mapped[Decimal] = mapped_column(MONEY)
    refund_status: Mapped[str] = mapped_column(Text)
    refunded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    refund_details: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=EMPTY_JSON_OBJECT)

    original_payment: Mapped[Payment] = relationship(back_populates="refunds")


class Chargeback(Base):
    __tablename__ = "chargebacks"
    __table_args__ = (
        UniqueConstraint("transaction_id", name="uq_chargebacks_transaction_id"),
        ForeignKeyConstraint(
            ["transaction_id", "customer_id"],
            ["transactions.transaction_id", "transactions.customer_id"],
            name="fk_chargebacks_transaction_customer",
        ),
        Index("ix_chargebacks_customer_id_opened_at", "customer_id", "opened_at"),
        Index("ix_chargebacks_opened_at", "opened_at"),
    )

    chargeback_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    transaction_id: Mapped[str] = mapped_column(Text)
    customer_id: Mapped[str] = mapped_column(Text)
    reason_code: Mapped[str] = mapped_column(Text)
    chargeback_amount: Mapped[Decimal] = mapped_column(MONEY)
    chargeback_status: Mapped[str] = mapped_column(Text)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    dispute_details: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=EMPTY_JSON_OBJECT)

    transaction: Mapped[Transaction] = relationship(back_populates="chargeback")
    events: Mapped[list["ChargebackEvent"]] = relationship(
        back_populates="chargeback", order_by="ChargebackEvent.event_sequence"
    )


class ChargebackEvent(Base):
    __tablename__ = "chargeback_events"
    __table_args__ = (
        UniqueConstraint("chargeback_id", "event_sequence", name="uq_chargeback_events_sequence"),
        Index("ix_chargeback_events_chargeback_id", "chargeback_id"),
    )

    chargeback_event_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    chargeback_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chargebacks.chargeback_id"))
    event_type: Mapped[str] = mapped_column(Text)
    event_sequence: Mapped[int] = mapped_column(Integer)
    event_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    event_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=EMPTY_JSON_OBJECT)

    chargeback: Mapped[Chargeback] = relationship(back_populates="events")
