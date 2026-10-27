"""Response bodies shared by every service's HTTP API."""

from shared.contracts.contract_model import ContractModel
from shared.contracts.filter_validation_issue import FilterValidationIssue


class ErrorResponse(ContractModel):
    """Every error has this shape, so clients (and the LLM) can handle them uniformly."""

    error: str  # e.g. FILTER_VALIDATION_FAILED, INVALID_REQUEST, TRANSACTION_NOT_FOUND
    message: str
    issues: tuple[FilterValidationIssue, ...] = ()


class FilterValidationResult(ContractModel):
    is_valid: bool
    issues: tuple[FilterValidationIssue, ...]


class HealthStatus(ContractModel):
    status: str  # "ok" or "unavailable"
