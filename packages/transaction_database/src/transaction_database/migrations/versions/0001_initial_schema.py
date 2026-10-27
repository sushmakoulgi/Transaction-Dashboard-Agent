"""Initial schema: transactions, payments, refunds, chargebacks, chargeback_events.

Revision ID: 0001
Revises:
Create Date: 2026-10-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MONEY = sa.Numeric(12, 2)
TIMESTAMP = sa.DateTime(timezone=True)


def json_object_column(name: str) -> sa.Column[dict[str, object]]:
    return sa.Column(name, JSONB, nullable=False, server_default=sa.text("'{}'::jsonb"))


def upgrade() -> None:
    op.create_table(
        "transactions",
        sa.Column("transaction_id", sa.Uuid, primary_key=True),
        sa.Column("customer_id", sa.Text, nullable=False),
        sa.Column("amount", MONEY, nullable=False),
        sa.Column("currency", sa.CHAR(3), nullable=False),
        sa.Column("transaction_type", sa.Text, nullable=False),
        sa.Column("transaction_date", TIMESTAMP, nullable=False),
        json_object_column("merchant_details"),
        json_object_column("metadata"),
        sa.UniqueConstraint("transaction_id", "customer_id", name="uq_transactions_id_customer"),
    )
    op.create_index("ix_transactions_customer_id", "transactions", ["customer_id"])
    op.create_index("ix_transactions_transaction_date", "transactions", ["transaction_date"])
    op.create_index(
        "ix_transactions_merchant_details",
        "transactions",
        ["merchant_details"],
        postgresql_using="gin",
    )

    op.create_table(
        "payments",
        sa.Column("payment_id", sa.Uuid, primary_key=True),
        sa.Column("transaction_id", sa.Uuid, nullable=False),
        sa.Column("customer_id", sa.Text, nullable=False),
        sa.Column("payment_method", sa.Text, nullable=False),
        sa.Column("payment_status", sa.Text, nullable=False),
        sa.Column("authorized_amount", MONEY),
        sa.Column("captured_amount", MONEY),
        sa.Column("authorized_at", TIMESTAMP),
        sa.Column("captured_at", TIMESTAMP),
        json_object_column("payment_details"),
        sa.UniqueConstraint("transaction_id", name="uq_payments_transaction_id"),
        sa.UniqueConstraint("payment_id", "customer_id", name="uq_payments_id_customer"),
        sa.ForeignKeyConstraint(
            ["transaction_id", "customer_id"],
            ["transactions.transaction_id", "transactions.customer_id"],
            name="fk_payments_transaction_customer",
        ),
    )
    op.create_index("ix_payments_customer_id", "payments", ["customer_id"])
    op.create_index(
        "ix_payments_payment_details", "payments", ["payment_details"], postgresql_using="gin"
    )

    op.create_table(
        "refunds",
        sa.Column("refund_id", sa.Uuid, primary_key=True),
        sa.Column("original_payment_id", sa.Uuid, nullable=False),
        sa.Column("customer_id", sa.Text, nullable=False),
        sa.Column("refund_amount", MONEY, nullable=False),
        sa.Column("refund_status", sa.Text, nullable=False),
        sa.Column("refunded_at", TIMESTAMP, nullable=False),
        json_object_column("refund_details"),
        sa.ForeignKeyConstraint(
            ["original_payment_id", "customer_id"],
            ["payments.payment_id", "payments.customer_id"],
            name="fk_refunds_payment_customer",
        ),
    )
    op.create_index("ix_refunds_original_payment_id", "refunds", ["original_payment_id"])
    op.create_index("ix_refunds_customer_id", "refunds", ["customer_id"])

    op.create_table(
        "chargebacks",
        sa.Column("chargeback_id", sa.Uuid, primary_key=True),
        sa.Column("transaction_id", sa.Uuid, nullable=False),
        sa.Column("customer_id", sa.Text, nullable=False),
        sa.Column("reason_code", sa.Text, nullable=False),
        sa.Column("chargeback_amount", MONEY, nullable=False),
        sa.Column("chargeback_status", sa.Text, nullable=False),
        sa.Column("opened_at", TIMESTAMP, nullable=False),
        json_object_column("dispute_details"),
        sa.UniqueConstraint("transaction_id", name="uq_chargebacks_transaction_id"),
        sa.ForeignKeyConstraint(
            ["transaction_id", "customer_id"],
            ["transactions.transaction_id", "transactions.customer_id"],
            name="fk_chargebacks_transaction_customer",
        ),
    )
    op.create_index(
        "ix_chargebacks_customer_id_opened_at", "chargebacks", ["customer_id", "opened_at"]
    )
    op.create_index("ix_chargebacks_opened_at", "chargebacks", ["opened_at"])

    op.create_table(
        "chargeback_events",
        sa.Column("chargeback_event_id", sa.Uuid, primary_key=True),
        sa.Column(
            "chargeback_id",
            sa.Uuid,
            sa.ForeignKey("chargebacks.chargeback_id"),
            nullable=False,
        ),
        sa.Column("event_type", sa.Text, nullable=False),
        sa.Column("event_sequence", sa.Integer, nullable=False),
        sa.Column("event_date", TIMESTAMP, nullable=False),
        json_object_column("event_payload"),
        sa.UniqueConstraint(
            "chargeback_id", "event_sequence", name="uq_chargeback_events_sequence"
        ),
    )
    op.create_index("ix_chargeback_events_chargeback_id", "chargeback_events", ["chargeback_id"])


def downgrade() -> None:
    for table_name in ("chargeback_events", "chargebacks", "refunds", "payments", "transactions"):
        op.drop_table(table_name)
