"""What a question was answered with: the tool calls and their filters, never their results.

The agent records a plan for every answered question. The UI re-runs it for other pages,
session memory keeps it so follow-up questions can narrow an earlier search, and Phase 2
caches it. Steps are typed and reject unknown keys, so result rows can't be stored in one.
"""

import uuid
from typing import Annotated, Literal

from pydantic import Field

from shared.contracts.contract_model import ContractModel
from shared.contracts.filter_expression import FilterNode
from shared.contracts.filterable_field_description import SearchableEntity
from shared.contracts.search_request import SortSpec

MAX_PLAN_STEPS = 10

ENTITY_BY_SEARCH_TOOL: dict[str, SearchableEntity] = {
    "search_transactions": SearchableEntity.TRANSACTIONS,
    "search_chargebacks": SearchableEntity.CHARGEBACKS,
}


class SearchStep(ContractModel):
    """A search, without paging: page and page_size are chosen when the plan is run."""

    tool_name: Literal["search_transactions", "search_chargebacks"]
    filter: FilterNode | None = None
    sort: SortSpec | None = None

    @property
    def entity(self) -> SearchableEntity:
        return ENTITY_BY_SEARCH_TOOL[self.tool_name]


class DetailStep(ContractModel):
    tool_name: Literal["get_transaction_detail"]
    transaction_id: uuid.UUID


QueryPlanStep = Annotated[SearchStep | DetailStep, Field(discriminator="tool_name")]


class QueryPlan(ContractModel):
    steps: tuple[QueryPlanStep, ...] = Field(min_length=1, max_length=MAX_PLAN_STEPS)

    @property
    def last_search(self) -> SearchStep | None:
        """The search whose results the table shows (and pages through)."""
        searches = [step for step in self.steps if isinstance(step, SearchStep)]
        return searches[-1] if searches else None
