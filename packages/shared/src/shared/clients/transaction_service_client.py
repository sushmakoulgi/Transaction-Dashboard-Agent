"""HTTP client for the Transaction Service, used by the MCP server and the agent.

Every endpoint only reads data, so every call (including POST searches) is safe to retry.
Retries happen on connection errors, timeouts and 502/503/504, never on 4xx. Error
responses are raised as `TransactionServiceError` carrying the service's `issues`, so
callers can pass the exact problems back to the LLM.
"""

import asyncio
import uuid
from types import TracebackType
from typing import Any, Self

import httpx
from pydantic import BaseModel, SecretStr, TypeAdapter, ValidationError

from shared.contracts import (
    ChargebackSummary,
    ErrorResponse,
    FilterableFieldDescription,
    FilterValidationIssue,
    FilterValidationResult,
    PaginatedResult,
    SearchableEntity,
    SearchRequest,
    TransactionDetail,
    TransactionSummary,
)

RETRYABLE_STATUS_CODES = frozenset({502, 503, 504})
FIELD_DESCRIPTIONS = TypeAdapter(list[FilterableFieldDescription])


class TransactionServiceError(Exception):
    """The service answered with an error (4xx/5xx), in its `ErrorResponse` shape."""

    def __init__(
        self,
        status_code: int,
        error: str,
        message: str,
        issues: tuple[FilterValidationIssue, ...] = (),
    ) -> None:
        super().__init__(f"{status_code} {error}: {message}")
        self.status_code = status_code
        self.error = error
        self.message = message
        self.issues = issues


class TransactionServiceUnavailableError(Exception):
    """The service could not be reached, or stayed unavailable after every retry."""


class TransactionServiceClient:
    def __init__(
        self,
        base_url: str,
        api_token: SecretStr,
        timeout_seconds: float = 10.0,
        max_retries: int = 2,
        retry_backoff_seconds: float = 0.2,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._http_client = httpx.AsyncClient(
            base_url=base_url.rstrip("/") + "/api/v1",
            headers={"Authorization": f"Bearer {api_token.get_secret_value()}"},
            timeout=timeout_seconds,
            transport=transport,
        )
        self._max_retries = max_retries
        self._retry_backoff_seconds = retry_backoff_seconds

    async def search_transactions(
        self, request: SearchRequest
    ) -> PaginatedResult[TransactionSummary]:
        body = await self._send("POST", "/transactions/search", request)
        return PaginatedResult[TransactionSummary].model_validate(body)

    async def search_chargebacks(
        self, request: SearchRequest
    ) -> PaginatedResult[ChargebackSummary]:
        body = await self._send("POST", "/chargebacks/search", request)
        return PaginatedResult[ChargebackSummary].model_validate(body)

    async def get_transaction_detail(self, transaction_id: uuid.UUID) -> TransactionDetail:
        body = await self._send("GET", f"/transactions/{transaction_id}")
        return TransactionDetail.model_validate(body)

    async def list_filterable_fields(
        self, entity: SearchableEntity
    ) -> list[FilterableFieldDescription]:
        body = await self._send("GET", f"/{entity}/filterable-fields")
        return FIELD_DESCRIPTIONS.validate_python(body)

    async def validate_filters(
        self, entity: SearchableEntity, request: SearchRequest
    ) -> FilterValidationResult:
        body = await self._send("POST", f"/{entity}/filters/validate", request)
        return FilterValidationResult.model_validate(body)

    async def aclose(self) -> None:
        await self._http_client.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def _send(self, method: str, path: str, body: BaseModel | None = None) -> Any:
        json_body = body.model_dump(mode="json", exclude_none=True) if body is not None else None
        last_failure = ""
        for attempt in range(self._max_retries + 1):
            if attempt > 0:
                await asyncio.sleep(self._retry_backoff_seconds * 2 ** (attempt - 1))
            try:
                response = await self._http_client.request(method, path, json=json_body)
            except httpx.TransportError as error:
                last_failure = f"{type(error).__name__}: {error}"
                continue
            if response.status_code in RETRYABLE_STATUS_CODES:
                last_failure = f"HTTP {response.status_code}"
                continue
            if response.is_error:
                raise error_from_response(response)
            return response.json()
        raise TransactionServiceUnavailableError(
            f"Transaction Service unavailable after {self._max_retries + 1} attempts "
            f"({last_failure})"
        )


def error_from_response(response: httpx.Response) -> TransactionServiceError:
    try:
        error_body = ErrorResponse.model_validate(response.json())
    except (ValueError, ValidationError):
        return TransactionServiceError(
            response.status_code, "HTTP_ERROR", response.text[:500] or response.reason_phrase
        )
    return TransactionServiceError(
        response.status_code, error_body.error, error_body.message, error_body.issues
    )
