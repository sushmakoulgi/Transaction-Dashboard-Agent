"""The shared client talking to the real Transaction Service app (in-process, seeded database)."""

from collections.abc import AsyncIterator

import httpx
import pytest
from api_test_helpers import API_TOKEN, settings_for
from filter_test_helpers import condition, group
from pydantic import SecretStr
from sqlalchemy.exc import OperationalError

from shared.clients.transaction_service_client import (
    TransactionServiceClient,
    TransactionServiceError,
)
from shared.config import DatabaseSettings
from shared.contracts import FilterIssueCode, SearchableEntity, SearchRequest
from transaction_service.main import create_app

pytestmark = pytest.mark.integration


@pytest.fixture
async def service_client() -> AsyncIterator[TransactionServiceClient]:
    app = create_app(settings_for(str(DatabaseSettings().postgres_dsn_readonly)))
    try:
        async with (
            app.router.lifespan_context(app),
            TransactionServiceClient(
                "http://transaction-service",
                SecretStr(API_TOKEN),
                transport=httpx.ASGITransport(app=app),
            ) as client,
        ):
            yield client
    except OperationalError as error:
        pytest.skip(f"Postgres is not reachable: {error.orig}")


async def test_search_and_drill_down(service_client: TransactionServiceClient) -> None:
    page = await service_client.search_transactions(
        SearchRequest.model_validate(
            {"filter": condition("has_chargeback", "exists", True), "page_size": 5}
        )
    )
    assert page.total_count == 15
    detail = await service_client.get_transaction_detail(page.items[0].transaction_id)
    assert detail.chargeback is not None
    assert detail.chargeback.events


async def test_chargeback_search(service_client: TransactionServiceClient) -> None:
    page = await service_client.search_chargebacks(
        SearchRequest.model_validate(
            {"filter": condition("opened_at", "between", {"relative": "this_month"})}
        )
    )
    assert page.total_count >= 1
    assert all(item.latest_event_type for item in page.items)


async def test_validation_issues_come_back_typed(service_client: TransactionServiceClient) -> None:
    with pytest.raises(TransactionServiceError) as error:
        await service_client.search_transactions(
            SearchRequest.model_validate(
                {"filter": group(condition("cust_id", "eq", "C-1"), condition("amount", "gt", "x"))}
            )
        )
    assert error.value.status_code == 422
    assert [issue.code for issue in error.value.issues] == [
        FilterIssueCode.UNKNOWN_FIELD,
        FilterIssueCode.INVALID_VALUE,
    ]
    result = await service_client.validate_filters(
        SearchableEntity.TRANSACTIONS,
        SearchRequest.model_validate({"filter": condition("cust_id", "eq", "C-1")}),
    )
    assert result.is_valid is False


async def test_field_list_and_not_found(service_client: TransactionServiceClient) -> None:
    fields = await service_client.list_filterable_fields(SearchableEntity.TRANSACTIONS)
    assert "card_brand" in {field.name for field in fields}
    with pytest.raises(TransactionServiceError) as error:
        await service_client.get_transaction_detail("999999999999999")
    assert (error.value.status_code, error.value.error) == (404, "TRANSACTION_NOT_FOUND")
