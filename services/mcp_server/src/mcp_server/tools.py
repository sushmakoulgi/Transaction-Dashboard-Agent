"""The MCP tools: thin adapters from tool calls to the Transaction Service.

Every tool is read-only. Failures are raised as `ToolError` whose text is a JSON
`ErrorResponse` ({error, message, issues[]}), so the LLM gets the same precise problems and
suggestions as the REST API and can correct its call.
"""

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field, ValidationError

from mcp_server import tool_descriptions
from shared.clients.transaction_service_client import (
    TransactionServiceClient,
    TransactionServiceError,
    TransactionServiceUnavailableError,
)
from shared.contracts import (
    ChargebackSummary,
    ErrorResponse,
    FilterableFieldDescription,
    FilterIssueCode,
    FilterNode,
    FilterValidationIssue,
    FilterValidationResult,
    PaginatedResult,
    SearchableEntity,
    SearchRequest,
    SortSpec,
    TransactionDetail,
    TransactionSummary,
)
from shared.contracts.contract_model import ContractModel
from shared.contracts.search_request import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE

READ_ONLY = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False)


class FilterableFields(ContractModel):
    entity: SearchableEntity
    fields: tuple[FilterableFieldDescription, ...]


def register_tools(server: MCPServer[Any]) -> None:
    @server.tool(description=tool_descriptions.SEARCH_TRANSACTIONS, annotations=READ_ONLY)
    async def search_transactions(
        ctx: Context[Any, Any],
        filter: FilterNode | None = None,  # the name the LLM sees
        sort: SortSpec | None = None,
        page: int = Field(default=1, ge=1),
        page_size: int = Field(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    ) -> PaginatedResult[TransactionSummary]:
        request = search_request(filter, sort, page, page_size)
        return await call_transaction_service(
            ctx, lambda client: client.search_transactions(request)
        )

    @server.tool(description=tool_descriptions.SEARCH_CHARGEBACKS, annotations=READ_ONLY)
    async def search_chargebacks(
        ctx: Context[Any, Any],
        filter: FilterNode | None = None,  # the name the LLM sees
        sort: SortSpec | None = None,
        page: int = Field(default=1, ge=1),
        page_size: int = Field(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    ) -> PaginatedResult[ChargebackSummary]:
        request = search_request(filter, sort, page, page_size)
        return await call_transaction_service(
            ctx, lambda client: client.search_chargebacks(request)
        )

    @server.tool(description=tool_descriptions.GET_TRANSACTION_DETAIL, annotations=READ_ONLY)
    async def get_transaction_detail(
        ctx: Context[Any, Any], transaction_id: uuid.UUID
    ) -> TransactionDetail:
        return await call_transaction_service(
            ctx, lambda client: client.get_transaction_detail(transaction_id)
        )

    @server.tool(description=tool_descriptions.LIST_FILTERABLE_FIELDS, annotations=READ_ONLY)
    async def list_filterable_fields(
        ctx: Context[Any, Any], entity: SearchableEntity
    ) -> FilterableFields:
        fields = await call_transaction_service(
            ctx, lambda client: client.list_filterable_fields(entity)
        )
        return FilterableFields(entity=entity, fields=tuple(fields))

    @server.tool(description=tool_descriptions.VALIDATE_FILTERS, annotations=READ_ONLY)
    async def validate_filters(
        ctx: Context[Any, Any],
        entity: SearchableEntity,
        filter: FilterNode | None = None,  # the name the LLM sees
        sort: SortSpec | None = None,
        page: int = Field(default=1, ge=1),
        page_size: int = Field(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    ) -> FilterValidationResult:
        try:
            request = SearchRequest(filter=filter, sort=sort, page=page, page_size=page_size)
        except ValidationError as error:
            return FilterValidationResult(is_valid=False, issues=shape_issues(error))
        return await call_transaction_service(
            ctx, lambda client: client.validate_filters(entity, request)
        )


def search_request(
    filter_tree: FilterNode | None, sort: SortSpec | None, page: int, page_size: int
) -> SearchRequest:
    """Builds the shared request, enforcing its limits (nesting depth, condition count)."""
    try:
        return SearchRequest(filter=filter_tree, sort=sort, page=page, page_size=page_size)
    except ValidationError as error:
        raise tool_error(
            ErrorResponse(
                error="INVALID_REQUEST",
                message="the search request does not match the expected format",
                issues=shape_issues(error),
            )
        ) from None


def shape_issues(error: ValidationError) -> tuple[FilterValidationIssue, ...]:
    return tuple(
        FilterValidationIssue(
            path=".".join(str(part) for part in detail["loc"]) or "request",
            code=FilterIssueCode.INVALID_REQUEST_SHAPE,
            message=str(detail["msg"]),
        )
        for detail in error.errors()
    )


async def call_transaction_service[ResultT](
    ctx: Context[Any, Any],
    operation: Callable[[TransactionServiceClient], Awaitable[ResultT]],
) -> ResultT:
    client: TransactionServiceClient = ctx.request_context.lifespan_context.transaction_service
    try:
        return await operation(client)
    except TransactionServiceError as error:
        raise tool_error(
            ErrorResponse(error=error.error, message=error.message, issues=error.issues)
        ) from None
    except TransactionServiceUnavailableError as error:
        raise tool_error(
            ErrorResponse(
                error="TRANSACTION_SERVICE_UNAVAILABLE",
                message=f"{error}; try again shortly",
            )
        ) from None


def tool_error(response: ErrorResponse) -> ToolError:
    return ToolError(response.model_dump_json(exclude_defaults=False))
