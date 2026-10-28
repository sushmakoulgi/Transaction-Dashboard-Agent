"""Records which successful tool calls answered a question, as a `QueryPlan`.

Only search and detail calls are part of the plan; discovery calls (list_filterable_fields,
validate_filters) and failed calls are not. The last successful search's page is kept for
the UI, but it never goes into the plan.
"""

from typing import Any

from pydantic import ValidationError

from agent_service.mcp_bridge.mcp_tool_gateway import ToolCallOutcome
from shared.contracts import QueryPlan, SearchableEntity, SearchStep
from shared.contracts.query_plan import ENTITY_BY_SEARCH_TOOL, MAX_PLAN_STEPS, DetailStep

PLAN_TOOL_NAMES = frozenset({*ENTITY_BY_SEARCH_TOOL, "get_transaction_detail"})


class QueryPlanRecorder:
    def __init__(self) -> None:
        self._steps: list[SearchStep | DetailStep] = []
        self.last_result_page: dict[str, Any] | None = None
        self.last_result_entity: SearchableEntity | None = None

    def record(self, tool_name: str, tool_input: dict[str, Any], outcome: ToolCallOutcome) -> None:
        if outcome.is_error or tool_name not in PLAN_TOOL_NAMES:
            return
        step = to_plan_step(tool_name, tool_input)
        if step is None:
            return
        if not self._steps or self._steps[-1] != step:
            self._steps.append(step)
        if isinstance(step, SearchStep) and outcome.structured_content is not None:
            self.last_result_page = outcome.structured_content
            self.last_result_entity = step.entity

    def build_plan(self) -> QueryPlan | None:
        if not self._steps:
            return None
        return QueryPlan(steps=tuple(self._steps[-MAX_PLAN_STEPS:]))


def to_plan_step(tool_name: str, tool_input: dict[str, Any]) -> SearchStep | DetailStep | None:
    """The step for a successful call, keeping only what re-runs it (no paging, no results)."""
    try:
        if tool_name == "get_transaction_detail":
            return DetailStep(
                tool_name="get_transaction_detail", transaction_id=tool_input["transaction_id"]
            )
        return SearchStep.model_validate(
            {
                "tool_name": tool_name,
                "filter": tool_input.get("filter"),
                "sort": tool_input.get("sort"),
            }
        )
    except (KeyError, ValidationError):
        return None  # the server accepted it, so this shouldn't happen; never fail the turn
