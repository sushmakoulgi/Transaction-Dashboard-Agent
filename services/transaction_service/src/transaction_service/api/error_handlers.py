"""Maps domain errors to HTTP responses that all share the `ErrorResponse` shape."""

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from shared.contracts import ErrorResponse
from shared.http.error_responses import (
    error_handler,
    error_response,
    register_common_error_handlers,
)
from transaction_service.filtering.filter_validator import FilterValidationFailedError
from transaction_service.services.transaction_detail_service import TransactionNotFoundError


async def handle_filter_validation_failed(
    _: Request, error: FilterValidationFailedError
) -> JSONResponse:
    return error_response(
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        ErrorResponse(
            error="FILTER_VALIDATION_FAILED",
            message=f"the search request has {len(error.issues)} problem(s); fix them and retry",
            issues=tuple(error.issues),
        ),
    )


def register_error_handlers(app: FastAPI) -> None:
    register_common_error_handlers(app)
    app.add_exception_handler(FilterValidationFailedError, handle_filter_validation_failed)  # type: ignore[arg-type]
    app.add_exception_handler(
        TransactionNotFoundError,
        error_handler(
            status.HTTP_404_NOT_FOUND,
            "TRANSACTION_NOT_FOUND",
            lambda error: f"{error}; only transactions from the current year can be viewed",
        ),
    )
