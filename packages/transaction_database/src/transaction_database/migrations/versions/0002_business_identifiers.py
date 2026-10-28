"""Business identifiers: transaction_id and payment_id become text.

transaction_id is now 15 digits and payment_id 12 uppercase letters or digits (formats in
shared/contracts/identifiers.py), so their columns, and the columns referencing them, change
from uuid to text. Foreign keys over a column can't survive a type change, so they are
dropped and recreated around it. Existing uuid values convert to their text form; reseed
(`make seed`) to get IDs in the new formats.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-28
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (table, column) pairs whose type changes, referenced columns first.
IDENTIFIER_COLUMNS = (
    ("transactions", "transaction_id"),
    ("payments", "payment_id"),
    ("payments", "transaction_id"),
    ("refunds", "original_payment_id"),
    ("chargebacks", "transaction_id"),
)

# name, table, local columns, referenced table, referenced columns
FOREIGN_KEYS = (
    (
        "fk_payments_transaction_customer",
        "payments",
        ["transaction_id", "customer_id"],
        "transactions",
        ["transaction_id", "customer_id"],
    ),
    (
        "fk_chargebacks_transaction_customer",
        "chargebacks",
        ["transaction_id", "customer_id"],
        "transactions",
        ["transaction_id", "customer_id"],
    ),
    (
        "fk_refunds_payment_customer",
        "refunds",
        ["original_payment_id", "customer_id"],
        "payments",
        ["payment_id", "customer_id"],
    ),
)


def drop_foreign_keys() -> None:
    for name, table, _, _, _ in FOREIGN_KEYS:
        op.drop_constraint(name, table, type_="foreignkey")


def create_foreign_keys() -> None:
    for name, table, columns, referenced_table, referenced_columns in FOREIGN_KEYS:
        op.create_foreign_key(name, table, referenced_table, columns, referenced_columns)


def change_identifier_type(sql_type: str) -> None:
    for table, column in IDENTIFIER_COLUMNS:
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN {column} TYPE {sql_type} "
            f'USING "{column}"::{sql_type}'
        )


def upgrade() -> None:
    drop_foreign_keys()
    change_identifier_type("text")
    create_foreign_keys()


def downgrade() -> None:
    """Back to uuid. The new-format IDs aren't uuids, so the data is removed first."""
    op.execute("TRUNCATE chargeback_events, chargebacks, refunds, payments, transactions")
    drop_foreign_keys()
    change_identifier_type("uuid")
    create_foreign_keys()
