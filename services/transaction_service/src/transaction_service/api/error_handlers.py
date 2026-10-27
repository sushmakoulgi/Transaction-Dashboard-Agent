"""Maps domain errors to HTTP responses that all share the `ErrorResponse` shape."""

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from shared.contracts import ErrorResponse, FilterIssueCode, FilterValidationIssue
from transaction_service.filtering.filter_validator import FilterValidationFailedError
from transaction_service.services.transaction_detail_service import TransactionNotFoundError

ERROR_CODE_BY_HTTP_STATUS = {
    status.HTTP_401_UNAUTHORIZED: "UNAUTHORIZED",
    status.HTTP_404_NOT_FOUND: "NOT_FOUND",
    status.HTTP_405_METHOD_NOT_ALLOWED: "METHOD_NOT_ALLOWED",
}


def error_response(
    status_code: int, body: ErrorResponse, headers: dict[str, str] | None = None
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code, content=body.model_dump(mode="json"), headers=headers
    )


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


async def handle_invalid_request(_: Request, error: RequestValidationError) -> JSONResponse:
    """Malformed bodies: unknown keys, wrong shapes, nesting or count limits exceeded."""
    issues = tuple(
        FilterValidationIssue(
            path=".".join(str(part) for part in detail["loc"] if part != "body") or "body",
            code=FilterIssueCode.INVALID_REQUEST_SHAPE,
            message=str(detail["msg"]),
        )
        for detail in error.errors()
    )
    return error_response(
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        ErrorResponse(
            error="INVALID_REQUEST",
            message="the request does not match the expected format",
            issues=issues,
        ),
    )


async def handle_transaction_not_found(_: Request, error: TransactionNotFoundError) -> JSONResponse:
    return error_response(
        status.HTTP_404_NOT_FOUND,
        ErrorResponse(
            error="TRANSACTION_NOT_FOUND",
            message=f"{error}; only transactions from the current year can be viewed",
        ),
    )


async def handle_http_exception(_: Request, error: HTTPException) -> JSONResponse:
    return error_response(
        error.status_code,
        ErrorResponse(
            error=ERROR_CODE_BY_HTTP_STATUS.get(error.status_code, "HTTP_ERROR"),
            message=str(error.detail),
        ),
        headers=dict(error.headers) if error.headers else None,
    )


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(FilterValidationFailedError, handle_filter_validation_failed)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, handle_invalid_request)  # type: ignore[arg-type]
    app.add_exception_handler(TransactionNotFoundError, handle_transaction_not_found)  # type: ignore[arg-type]
    app.add_exception_handler(HTTPException, handle_http_exception)  # type: ignore[arg-type]
