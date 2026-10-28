"""Error handlers every FastAPI service shares, so all errors have the `ErrorResponse` shape."""

from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from shared.contracts import ErrorResponse, FilterIssueCode, FilterValidationIssue

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


def error_handler[ErrorT: Exception](
    status_code: int, error_code: str, message: Callable[[ErrorT], str]
) -> Callable[[Request, ErrorT], Awaitable[JSONResponse]]:
    """A handler answering `ErrorT` with this status, error code and message."""

    async def handle(_: Request, error: ErrorT) -> JSONResponse:
        return error_response(status_code, ErrorResponse(error=error_code, message=message(error)))

    return handle


async def handle_invalid_request(_: Request, error: RequestValidationError) -> JSONResponse:
    """Malformed bodies or paths: unknown keys, wrong shapes, limits exceeded."""
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


async def handle_http_exception(_: Request, error: HTTPException) -> JSONResponse:
    return error_response(
        error.status_code,
        ErrorResponse(
            error=ERROR_CODE_BY_HTTP_STATUS.get(error.status_code, "HTTP_ERROR"),
            message=str(error.detail),
        ),
        headers=dict(error.headers) if error.headers else None,
    )


def register_common_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(RequestValidationError, handle_invalid_request)  # type: ignore[arg-type]
    app.add_exception_handler(HTTPException, handle_http_exception)  # type: ignore[arg-type]
