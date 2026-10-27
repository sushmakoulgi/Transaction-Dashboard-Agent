"""Domain value sets. Stored as text columns so new values need no migration."""

from enum import StrEnum


class TransactionType(StrEnum):
    PURCHASE = "PURCHASE"
    SUBSCRIPTION = "SUBSCRIPTION"
    TOP_UP = "TOP_UP"


class PaymentMethod(StrEnum):
    CARD = "CARD"
    WALLET = "WALLET"
    ACH = "ACH"


class PaymentStatus(StrEnum):
    AUTHORIZED = "AUTHORIZED"
    CAPTURED = "CAPTURED"
    VOIDED = "VOIDED"
    FAILED = "FAILED"


class CardBrand(StrEnum):
    VISA = "VISA"
    MASTERCARD = "MASTERCARD"
    AMEX = "AMEX"
    DISCOVER = "DISCOVER"


class RefundStatus(StrEnum):
    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ChargebackStatus(StrEnum):
    OPEN = "OPEN"
    UNDER_REVIEW = "UNDER_REVIEW"
    WON = "WON"
    LOST = "LOST"
    ACCEPTED = "ACCEPTED"


class ChargebackEventType(StrEnum):
    OPENED = "OPENED"
    EVIDENCE_REQUESTED = "EVIDENCE_REQUESTED"
    EVIDENCE_SUBMITTED = "EVIDENCE_SUBMITTED"
    REPRESENTMENT = "REPRESENTMENT"
    PRE_ARBITRATION = "PRE_ARBITRATION"
    ARBITRATION = "ARBITRATION"
    WON = "WON"
    LOST = "LOST"
    ACCEPTED = "ACCEPTED"
