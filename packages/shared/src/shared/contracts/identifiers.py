"""Formats of the business identifiers, shared by the database, APIs, MCP tools and filters.

- transaction_id: 15 digits, e.g. 482910375610294
- payment_id:     12 uppercase letters or digits, e.g. K7Q2M9XA4B1Z
- customer_id:    8 digits, e.g. 10001641
"""

from typing import Annotated, NamedTuple

from pydantic import Field

TRANSACTION_ID_PATTERN = r"^[0-9]{15}$"
PAYMENT_ID_PATTERN = r"^[A-Z0-9]{12}$"
CUSTOMER_ID_PATTERN = r"^[0-9]{8}$"

TRANSACTION_ID_FORMAT = "15 digits, e.g. 482910375610294"
PAYMENT_ID_FORMAT = "12 uppercase letters or digits, e.g. K7Q2M9XA4B1Z"
CUSTOMER_ID_FORMAT = "8 digits, e.g. 10001641"


class IdentifierFormat(NamedTuple):
    pattern: str
    description: str


TRANSACTION_ID = IdentifierFormat(TRANSACTION_ID_PATTERN, TRANSACTION_ID_FORMAT)
PAYMENT_ID = IdentifierFormat(PAYMENT_ID_PATTERN, PAYMENT_ID_FORMAT)
CUSTOMER_ID = IdentifierFormat(CUSTOMER_ID_PATTERN, CUSTOMER_ID_FORMAT)

TransactionId = Annotated[
    str,
    Field(pattern=TRANSACTION_ID_PATTERN, description=f"Transaction ID: {TRANSACTION_ID_FORMAT}"),
]
PaymentId = Annotated[
    str, Field(pattern=PAYMENT_ID_PATTERN, description=f"Payment ID: {PAYMENT_ID_FORMAT}")
]
CustomerId = Annotated[
    str, Field(pattern=CUSTOMER_ID_PATTERN, description=f"Customer ID: {CUSTOMER_ID_FORMAT}")
]
