import json
import uuid

from agent_service.agent.query_plan_recorder import QueryPlanRecorder
from agent_service.agent.tool_result_compactor import ToolResultCompactor
from agent_service.mcp_bridge.mcp_tool_gateway import ToolCallOutcome
from shared.contracts import DetailStep, SearchStep

COMPACTOR = ToolResultCompactor(preview_rows=2)
PAGE = {
    "items": [{"id": n} for n in range(5)],
    "page": 1,
    "page_size": 25,
    "total_count": 5,
    "total_pages": 1,
}


def success(content: dict[str, object]) -> ToolCallOutcome:
    return ToolCallOutcome(is_error=False, text="ignored", structured_content=content)


def test_search_results_are_trimmed() -> None:
    result = COMPACTOR.to_tool_result("toolu_1", success(PAGE))
    shown = json.loads(str(result["content"]))
    assert shown["rows"] == [{"id": 0}, {"id": 1}]
    assert (shown["total_count"], shown["rows_on_this_page"]) == (5, 5)
    assert "is_error" not in result


def test_small_pages_have_no_note() -> None:
    page = {**PAGE, "items": PAGE["items"][:2], "total_count": 2}  # type: ignore[index]
    assert "note" not in json.loads(str(COMPACTOR.to_tool_result("t", success(page))["content"]))


def test_other_results_pass_through_whole() -> None:
    detail = {"transaction_id": "x", "refunds": [1, 2, 3]}
    assert json.loads(str(COMPACTOR.to_tool_result("t", success(detail))["content"])) == detail


def test_errors_pass_through_verbatim() -> None:
    result = COMPACTOR.to_tool_result("t", ToolCallOutcome(is_error=True, text="boom"))
    assert (result["content"], result["is_error"]) == ("boom", True)
    empty = COMPACTOR.to_tool_result("t", ToolCallOutcome(is_error=True, text=""))
    assert empty["content"] == "the tool failed without a message"


def test_recorder_keeps_only_successful_plan_steps_in_order() -> None:
    recorder = QueryPlanRecorder()
    transaction_id = uuid.uuid4()
    recorder.record("list_filterable_fields", {"entity": "transactions"}, success({}))
    recorder.record("search_transactions", {"page": 1}, ToolCallOutcome(is_error=True, text="x"))
    recorder.record("search_chargebacks", {"page": 3, "page_size": 10}, success(PAGE))
    recorder.record("get_transaction_detail", {"transaction_id": str(transaction_id)}, success({}))
    plan = recorder.build_plan()
    assert plan is not None
    search, detail = plan.steps
    assert isinstance(search, SearchStep) and search.tool_name == "search_chargebacks"
    assert search.filter is None  # paging arguments are not kept
    assert isinstance(detail, DetailStep) and detail.transaction_id == transaction_id
    assert recorder.last_result_page == PAGE


def test_recorder_without_steps_has_no_plan() -> None:
    assert QueryPlanRecorder().build_plan() is None
