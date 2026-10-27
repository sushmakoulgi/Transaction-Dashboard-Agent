"""Unit tests for TransactionServiceClient, using httpx's mock transport."""

import json
import uuid
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from shared.clients.transaction_service_client import (
    TransactionServiceClient,
    TransactionServiceError,
    TransactionServiceUnavailableError,
)
from shared.contracts import FilterIssueCode, SearchableEntity, SearchRequest

API_TOKEN = SecretStr("test-token-0123456789abcdef")
EMPTY_PAGE = {"items": [], "page": 1, "page_size": 25, "total_count": 0, "total_pages": 0}


class RecordingTransport(httpx.MockTransport):
    """Answers each request with the next queued response and remembers every request."""

    def __init__(self, *responses: httpx.Response | Exception) -> None:
        self.requests: list[httpx.Request] = []
        queued = list(responses)

        def respond(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            next_response = queued.pop(0) if len(queued) > 1 else queued[0]
            if isinstance(next_response, Exception):
                raise next_response
            return next_response

        super().__init__(respond)


def client_with(transport: httpx.MockTransport) -> TransactionServiceClient:
    return TransactionServiceClient(
        "http://transaction-service:8200/",
        API_TOKEN,
        max_retries=2,
        retry_backoff_seconds=0,
        transport=transport,
    )


def json_response(status_code: int, body: Any) -> httpx.Response:
    return httpx.Response(status_code, json=body)


async def call(
    transport: RecordingTransport,
    operation: Callable[[TransactionServiceClient], Any],
) -> Any:
    async with client_with(transport) as client:
        return await operation(client)


async def test_sends_the_token_and_the_request_body() -> None:
    transport = RecordingTransport(json_response(200, EMPTY_PAGE))
    request = SearchRequest.model_validate(
        {"filter": {"kind": "condition", "field": "customer_id", "operator": "eq", "value": "C-1"}}
    )
    result = await call(transport, lambda client: client.search_transactions(request))

    sent = transport.requests[0]
    assert sent.method == "POST"
    assert str(sent.url) == "http://transaction-service:8200/api/v1/transactions/search"
    assert sent.headers["authorization"] == f"Bearer {API_TOKEN.get_secret_value()}"
    assert json.loads(sent.content)["filter"] == {
        "kind": "condition",
        "field": "customer_id",
        "operator": "eq",
        "value": "C-1",
    }
    assert result.total_count == 0


async def test_paths_for_every_operation() -> None:
    transaction_id = uuid.uuid4()
    transport = RecordingTransport(
        json_response(200, EMPTY_PAGE),
        json_response(200, []),
        json_response(200, {"is_valid": True, "issues": []}),
        json_response(404, {"error": "TRANSACTION_NOT_FOUND", "message": "x", "issues": []}),
    )
    async with client_with(transport) as client:
        await client.search_chargebacks(SearchRequest())
        await client.list_filterable_fields(SearchableEntity.CHARGEBACKS)
        await client.validate_filters(SearchableEntity.TRANSACTIONS, SearchRequest())
        with pytest.raises(TransactionServiceError):
            await client.get_transaction_detail(transaction_id)
    assert [(request.method, request.url.path) for request in transport.requests] == [
        ("POST", "/api/v1/chargebacks/search"),
        ("GET", "/api/v1/chargebacks/filterable-fields"),
        ("POST", "/api/v1/transactions/filters/validate"),
        ("GET", f"/api/v1/transactions/{transaction_id}"),
    ]


async def test_validation_errors_carry_the_issues_and_are_not_retried() -> None:
    transport = RecordingTransport(
        json_response(
            422,
            {
                "error": "FILTER_VALIDATION_FAILED",
                "message": "the search request has 1 problem(s)",
                "issues": [
                    {
                        "path": "filter.field",
                        "code": "UNKNOWN_FIELD",
                        "message": "unknown field 'cust_id'",
                        "suggestions": ["customer_id"],
                    }
                ],
            },
        )
    )
    with pytest.raises(TransactionServiceError) as error:
        await call(transport, lambda client: client.search_transactions(SearchRequest()))
    assert (error.value.status_code, error.value.error) == (422, "FILTER_VALIDATION_FAILED")
    (issue,) = error.value.issues
    assert issue.code is FilterIssueCode.UNKNOWN_FIELD
    assert issue.suggestions == ("customer_id",)
    assert len(transport.requests) == 1


@pytest.mark.parametrize(
    ("status_code", "expected_error"),
    [(401, "UNAUTHORIZED"), (404, "TRANSACTION_NOT_FOUND")],
)
async def test_client_errors_are_raised_without_retry(
    status_code: int, expected_error: str
) -> None:
    transport = RecordingTransport(
        json_response(status_code, {"error": expected_error, "message": "nope", "issues": []})
    )
    with pytest.raises(TransactionServiceError) as error:
        await call(transport, lambda client: client.get_transaction_detail(uuid.uuid4()))
    assert error.value.error == expected_error
    assert len(transport.requests) == 1


async def test_retries_temporary_unavailability_then_succeeds() -> None:
    transport = RecordingTransport(
        httpx.Response(503),
        httpx.ConnectError("connection refused"),
        json_response(200, EMPTY_PAGE),
    )
    result = await call(transport, lambda client: client.search_transactions(SearchRequest()))
    assert result.total_count == 0
    assert len(transport.requests) == 3


async def test_gives_up_after_every_retry() -> None:
    transport = RecordingTransport(httpx.ReadTimeout("timed out"))
    with pytest.raises(TransactionServiceUnavailableError, match="after 3 attempts"):
        await call(transport, lambda client: client.search_transactions(SearchRequest()))
    assert len(transport.requests) == 3


async def test_error_without_the_standard_body() -> None:
    transport = RecordingTransport(httpx.Response(500, text="Internal Server Error"))
    with pytest.raises(TransactionServiceError) as error:
        await call(transport, lambda client: client.search_transactions(SearchRequest()))
    assert (error.value.status_code, error.value.error) == (500, "HTTP_ERROR")
    assert error.value.message == "Internal Server Error"
    assert len(transport.requests) == 1


async def test_sends_no_authorization_header_without_a_token() -> None:
    transport = RecordingTransport(json_response(200, EMPTY_PAGE))
    async with TransactionServiceClient(
        "http://transaction-service:8200", None, transport=transport
    ) as client:
        await client.search_transactions(SearchRequest())
    assert "authorization" not in transport.requests[0].headers
