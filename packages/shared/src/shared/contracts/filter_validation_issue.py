from enum import StrEnum

from shared.contracts.contract_model import ContractModel


class FilterIssueCode(StrEnum):
    UNKNOWN_FIELD = "UNKNOWN_FIELD"
    OPERATOR_NOT_ALLOWED = "OPERATOR_NOT_ALLOWED"
    INVALID_VALUE = "INVALID_VALUE"
    INVALID_VALUE_COUNT = "INVALID_VALUE_COUNT"
    DATE_OUT_OF_RANGE = "DATE_OUT_OF_RANGE"
    FIELD_NOT_SORTABLE = "FIELD_NOT_SORTABLE"
    INVALID_REQUEST_SHAPE = "INVALID_REQUEST_SHAPE"  # malformed JSON, unknown keys, limits


class FilterValidationIssue(ContractModel):
    """One problem in a search request, precise enough for the LLM to fix it."""

    path: str  # e.g. "filter.children[2].field"
    code: FilterIssueCode
    message: str
    suggestions: tuple[str, ...] = ()
