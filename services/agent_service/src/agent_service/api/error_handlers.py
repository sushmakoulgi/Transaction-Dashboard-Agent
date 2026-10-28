"""Maps agent errors to HTTP responses, all in the shared `ErrorResponse` shape."""

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from agent_service.agent.query_agent import InvalidQuestionError
from agent_service.llm.claude_client import (
    ClaudeConfigurationError,
    ClaudeRefusalError,
    ClaudeReplyTruncatedError,
    ClaudeUnavailableError,
    ClaudeUnexpectedStopError,
)
from agent_service.memory.session_store import InvalidSessionIdError
from agent_service.plan_execution.plan_executor import (
    InvalidPageRequestError,
    PlanHasNoSearchError,
)
from shared.clients.transaction_service_client import (
    TransactionServiceError,
    TransactionServiceUnavailableError,
)
from shared.contracts import ErrorResponse
from shared.http.error_responses import (
    error_handler,
    error_response,
    register_common_error_handlers,
)

UNPROCESSABLE = status.HTTP_422_UNPROCESSABLE_CONTENT


async def pass_through_transaction_service_error(
    _: Request, error: TransactionServiceError
) -> JSONResponse:
    """Keeps the service's status, error code and issues (e.g. 404, or 422 with issues)."""
    return error_response(
        error.status_code,
        ErrorResponse(error=error.error, message=error.message, issues=error.issues),
    )


def describe_refusal(error: ClaudeRefusalError) -> str:
    return (
        "Claude declined to answer this question"
        + (f" (category: {error.category})" if error.category else "")
        + "; try rephrasing it"
    )


def register_error_handlers(app: FastAPI) -> None:
    register_common_error_handlers(app)
    handlers = {
        InvalidQuestionError: error_handler(UNPROCESSABLE, "INVALID_QUESTION", str),
        InvalidSessionIdError: error_handler(UNPROCESSABLE, "INVALID_SESSION_ID", str),
        PlanHasNoSearchError: error_handler(UNPROCESSABLE, "PLAN_HAS_NO_SEARCH", str),
        InvalidPageRequestError: error_handler(UNPROCESSABLE, "INVALID_PAGE_REQUEST", str),
        ClaudeRefusalError: error_handler(UNPROCESSABLE, "CLAUDE_DECLINED", describe_refusal),
        ClaudeUnavailableError: error_handler(
            status.HTTP_503_SERVICE_UNAVAILABLE, "CLAUDE_UNAVAILABLE", str
        ),
        ClaudeConfigurationError: error_handler(
            status.HTTP_502_BAD_GATEWAY, "CLAUDE_CONFIGURATION_ERROR", str
        ),
        ClaudeReplyTruncatedError: error_handler(
            status.HTTP_502_BAD_GATEWAY, "CLAUDE_REPLY_ERROR", str
        ),
        ClaudeUnexpectedStopError: error_handler(
            status.HTTP_502_BAD_GATEWAY, "CLAUDE_REPLY_ERROR", str
        ),
        TransactionServiceUnavailableError: error_handler(
            status.HTTP_503_SERVICE_UNAVAILABLE, "TRANSACTION_SERVICE_UNAVAILABLE", str
        ),
    }
    for error_type, handler in handlers.items():
        app.add_exception_handler(error_type, handler)  # type: ignore[arg-type]
    app.add_exception_handler(TransactionServiceError, pass_through_transaction_service_error)  # type: ignore[arg-type]
