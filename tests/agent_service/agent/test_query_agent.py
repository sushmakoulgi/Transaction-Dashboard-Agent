"""QueryAgent with a scripted Claude and scripted tools (no API calls, no servers)."""

import asyncio
import copy
import json
import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from anthropic.types.beta import (
    BetaMessageParam,
    BetaTextBlock,
    BetaTextBlockParam,
    BetaThinkingBlock,
    BetaToolParam,
    BetaToolUseBlock,
)

from agent_service.agent.query_agent import AgentAnswer, InvalidQuestionError, QueryAgent
from agent_service.llm.claude_client import ClaudeReply, ClaudeUnavailableError, ToolUseRequest
from agent_service.mcp_bridge.mcp_tool_gateway import ToolCallOutcome
from agent_service.memory.session_store import InMemorySessionStore, SessionEntry
from shared.clock import FixedClock
from shared.contracts import DetailStep, SearchableEntity, SearchStep

NEW_YORK = ZoneInfo("America/New_York")
CLOCK = FixedClock(datetime(2026, 10, 27, 15, tzinfo=UTC))
SESSION_ID = "session-1"
CUSTOMER_FILTER = {"kind": "condition", "field": "customer_id", "operator": "eq", "value": "C-1004"}
BAD_FILTER = {"kind": "condition", "field": "cust_id", "operator": "eq", "value": "C-1004"}


# --- Scripted Claude ---


def thinking() -> BetaThinkingBlock:
    return BetaThinkingBlock.model_validate(
        {"type": "thinking", "thinking": "", "signature": "sig-1"}
    )


def tool_use(
    tool_name: str, tool_input: dict[str, Any], tool_use_id: str = "toolu_1"
) -> BetaToolUseBlock:
    return BetaToolUseBlock.model_validate(
        {"type": "tool_use", "id": tool_use_id, "name": tool_name, "input": tool_input}
    )


def reply_with(*blocks: Any) -> ClaudeReply:
    tool_uses = [block for block in blocks if isinstance(block, BetaToolUseBlock)]
    return ClaudeReply(
        content=list(blocks),
        stop_reason="tool_use" if tool_uses else "end_turn",
        text="".join(block.text for block in blocks if isinstance(block, BetaTextBlock)),
        tool_use_requests=tuple(
            ToolUseRequest(block.id, block.name, dict(block.input)) for block in tool_uses
        ),
        model="claude-opus-5-5",
        input_tokens=100,
        output_tokens=20,
        cache_read_input_tokens=0,
    )


def final_answer(text: str) -> ClaudeReply:
    return reply_with(BetaTextBlock.model_validate({"type": "text", "text": text}))


class ScriptedClaude:
    def __init__(self, replies: Sequence[ClaudeReply | Exception]) -> None:
        self._replies = list(replies)
        self.requests: list[list[BetaMessageParam]] = []

    async def create_message(
        self,
        system: Sequence[BetaTextBlockParam],
        messages: Sequence[BetaMessageParam],
        tools: Sequence[BetaToolParam],
    ) -> ClaudeReply:
        self.requests.append(copy.copy(list(messages)))  # snapshot of what was sent
        next_reply = self._replies.pop(0) if len(self._replies) > 1 else self._replies[0]
        if isinstance(next_reply, Exception):
            raise next_reply
        return next_reply


# --- Scripted tools ---


def search_page(total_count: int, rows: int) -> dict[str, Any]:
    return {
        "items": [{"transaction_id": f"t-{n}", "customer_id": "C-1004"} for n in range(rows)],
        "page": 1,
        "page_size": 25,
        "total_count": total_count,
        "total_pages": max(1, -(-total_count // 25)),
    }


class ScriptedTools:
    def __init__(self, respond: Callable[[str, dict[str, Any]], ToolCallOutcome]) -> None:
        self._respond = respond
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.concurrently_running = 0
        self.most_concurrent = 0

    @property
    def tool_definitions(self) -> tuple[BetaToolParam, ...]:
        return tuple(
            {"name": name, "description": name, "input_schema": {"type": "object"}}
            for name in ("get_transaction_detail", "list_filterable_fields", "search_transactions")
        )

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> ToolCallOutcome:
        self.calls.append((tool_name, arguments))
        self.concurrently_running += 1
        self.most_concurrent = max(self.most_concurrent, self.concurrently_running)
        await asyncio.sleep(0.01)
        self.concurrently_running -= 1
        return self._respond(tool_name, arguments)


def search_succeeds(total_count: int = 15, rows: int = 15) -> ScriptedTools:
    def respond(tool_name: str, arguments: dict[str, Any]) -> ToolCallOutcome:
        if arguments.get("filter") == BAD_FILTER:
            return ToolCallOutcome(
                is_error=True,
                text="Error executing tool search_transactions: "
                '{"error":"FILTER_VALIDATION_FAILED",'
                '"issues":[{"path":"filter.field","suggestions":["customer_id"]}]}',
            )
        if tool_name == "search_transactions":
            page = search_page(total_count, rows)
            return ToolCallOutcome(is_error=False, text=json.dumps(page), structured_content=page)
        if tool_name == "get_transaction_detail":
            detail = {"transaction_id": arguments["transaction_id"], "payment": {}}
            return ToolCallOutcome(is_error=False, text="{}", structured_content=detail)
        fields = {"entity": "transactions", "fields": [{"name": "customer_id"}]}
        return ToolCallOutcome(is_error=False, text="{}", structured_content=fields)

    return ScriptedTools(respond)


def build_agent(
    claude: ScriptedClaude, tools: ScriptedTools, max_tool_rounds: int = 8
) -> tuple[QueryAgent, InMemorySessionStore]:
    sessions = InMemorySessionStore(10, timedelta(hours=2), CLOCK)
    agent = QueryAgent(
        claude, tools, sessions, CLOCK, NEW_YORK, max_tool_rounds, tool_result_preview_rows=10
    )
    return agent, sessions


def tool_results_in(message: BetaMessageParam) -> list[dict[str, Any]]:
    assert message["role"] == "user"
    return [block for block in message["content"] if isinstance(block, dict)]  # type: ignore[union-attr]


# --- A normal question ---


async def test_answers_with_one_search() -> None:
    claude = ScriptedClaude(
        [
            reply_with(thinking(), tool_use("search_transactions", {"filter": CUSTOMER_FILTER})),
            final_answer("C-1004 has 15 transactions."),
        ]
    )
    agent, _ = build_agent(claude, search_succeeds())
    answer = await agent.answer(SESSION_ID, "Transactions for C-1004?")

    assert answer.reply == "C-1004 has 15 transactions."
    assert answer.query_plan is not None
    (step,) = answer.query_plan.steps
    assert isinstance(step, SearchStep)
    assert step.filter is not None and step.filter.model_dump()["value"] == "C-1004"
    assert answer.result_entity is SearchableEntity.TRANSACTIONS
    assert answer.result_page is not None and len(answer.result_page["items"]) == 15
    assert (answer.claude_calls, answer.tool_calls, answer.reached_round_limit) == (2, 1, False)


async def test_claude_sees_a_trimmed_page_but_the_ui_gets_all_rows() -> None:
    claude = ScriptedClaude(
        [
            reply_with(tool_use("search_transactions", {"filter": CUSTOMER_FILTER})),
            final_answer("done"),
        ]
    )
    agent, _ = build_agent(claude, search_succeeds(total_count=40, rows=25))
    answer = await agent.answer(SESSION_ID, "Transactions for C-1004?")

    (tool_result,) = tool_results_in(claude.requests[1][-1])
    shown = json.loads(tool_result["content"])
    assert shown["total_count"] == 40
    assert (shown["rows_on_this_page"], len(shown["rows"])) == (25, 10)
    assert "the user sees all of them" in shown["note"]
    assert answer.result_page is not None and len(answer.result_page["items"]) == 25


async def test_claude_content_goes_back_unmodified() -> None:
    first_reply = reply_with(
        thinking(), tool_use("search_transactions", {"filter": CUSTOMER_FILTER})
    )
    claude = ScriptedClaude([first_reply, final_answer("done")])
    agent, _ = build_agent(claude, search_succeeds())
    await agent.answer(SESSION_ID, "Transactions for C-1004?")

    user, date_context, assistant, tool_results = claude.requests[1]
    assert [user["role"], date_context["role"], assistant["role"], tool_results["role"]] == [
        "user",
        "system",
        "assistant",
        "user",
    ]
    assert assistant["content"] is first_reply.content  # the very same block objects
    assert assistant["content"][0].signature == "sig-1"  # type: ignore[index, union-attr]


# --- Errors and retries ---


async def test_validation_error_goes_back_to_claude_and_is_not_in_the_plan() -> None:
    claude = ScriptedClaude(
        [
            reply_with(tool_use("search_transactions", {"filter": BAD_FILTER}, "toolu_bad")),
            reply_with(tool_use("search_transactions", {"filter": CUSTOMER_FILTER}, "toolu_ok")),
            final_answer("C-1004 has 15 transactions."),
        ]
    )
    agent, _ = build_agent(claude, search_succeeds())
    answer = await agent.answer(SESSION_ID, "Transactions for C-1004?")

    (error_result,) = tool_results_in(claude.requests[1][-1])
    assert error_result["tool_use_id"] == "toolu_bad"
    assert error_result["is_error"] is True
    assert "FILTER_VALIDATION_FAILED" in error_result["content"]
    assert "customer_id" in error_result["content"]
    assert answer.query_plan is not None and len(answer.query_plan.steps) == 1
    assert answer.tool_calls == 2


async def test_stops_after_the_round_limit() -> None:
    claude = ScriptedClaude(
        [reply_with(tool_use("search_transactions", {"filter": CUSTOMER_FILTER}))]
    )
    agent, sessions = build_agent(claude, search_succeeds(), max_tool_rounds=3)
    answer = await agent.answer(SESSION_ID, "Transactions for C-1004?")

    assert (answer.claude_calls, answer.reached_round_limit) == (3, True)
    assert "within 3 steps" in answer.reply
    assert answer.query_plan is not None and len(answer.query_plan.steps) == 1  # deduplicated
    assert answer.result_page is not None
    assert len(await sessions.load_recent_entries(SESSION_ID)) == 1


async def test_claude_failure_is_raised_and_nothing_is_saved() -> None:
    claude = ScriptedClaude([ClaudeUnavailableError("overloaded")])
    agent, sessions = build_agent(claude, search_succeeds())
    with pytest.raises(ClaudeUnavailableError):
        await agent.answer(SESSION_ID, "Transactions for C-1004?")
    assert await sessions.load_recent_entries(SESSION_ID) == []


@pytest.mark.parametrize("question", ["", "   ", "x" * 2001])
async def test_invalid_questions_never_reach_claude(question: str) -> None:
    claude = ScriptedClaude([final_answer("unused")])
    agent, _ = build_agent(claude, search_succeeds())
    with pytest.raises(InvalidQuestionError):
        await agent.answer(SESSION_ID, question)
    assert claude.requests == []


# --- Several tools, and what goes into the plan ---


async def test_parallel_tool_calls_run_together_and_return_in_one_message() -> None:
    transaction_id = str(uuid.uuid4())
    claude = ScriptedClaude(
        [
            reply_with(
                tool_use("list_filterable_fields", {"entity": "transactions"}, "toolu_a"),
                tool_use("search_transactions", {"filter": CUSTOMER_FILTER}, "toolu_b"),
                tool_use("get_transaction_detail", {"transaction_id": transaction_id}, "toolu_c"),
            ),
            final_answer("done"),
        ]
    )
    tools = search_succeeds()
    agent, _ = build_agent(claude, tools)
    answer = await agent.answer(SESSION_ID, "Show C-1004's transactions and the first one")

    assert tools.most_concurrent == 3
    results = tool_results_in(claude.requests[1][-1])
    assert [result["tool_use_id"] for result in results] == ["toolu_a", "toolu_b", "toolu_c"]
    assert answer.query_plan is not None
    search, detail = answer.query_plan.steps  # list_filterable_fields is not part of the plan
    assert isinstance(search, SearchStep) and isinstance(detail, DetailStep)
    assert str(detail.transaction_id) == transaction_id


async def test_a_clarifying_question_has_no_plan() -> None:
    claude = ScriptedClaude([final_answer("Which customer do you mean?")])
    agent, sessions = build_agent(claude, search_succeeds())
    answer = await agent.answer(SESSION_ID, "Show me their stuff")
    assert (answer.query_plan, answer.result_page, answer.tool_calls) == (None, None, 0)
    (saved,) = await sessions.load_recent_entries(SESSION_ID)
    assert saved.query_plan is None


# --- Session memory ---


async def test_saves_only_the_question_and_plan() -> None:
    claude = ScriptedClaude(
        [
            reply_with(tool_use("search_transactions", {"filter": CUSTOMER_FILTER})),
            final_answer("C-1004 has 15 transactions."),
        ]
    )
    agent, sessions = build_agent(claude, search_succeeds())
    answer: AgentAnswer = await agent.answer(SESSION_ID, "  Transactions for C-1004?  ")

    (saved,) = await sessions.load_recent_entries(SESSION_ID)
    assert saved == SessionEntry(
        user_question="Transactions for C-1004?", query_plan=answer.query_plan
    )
    stored_json = saved.model_dump_json()
    for leaked in ("C-1004 has 15 transactions", "t-0", "total_count", "items", "sig-1"):
        assert leaked not in stored_json


async def test_follow_up_question_carries_the_earlier_question_and_plan() -> None:
    first_claude = ScriptedClaude(
        [
            reply_with(tool_use("search_transactions", {"filter": CUSTOMER_FILTER})),
            final_answer("C-1004 has 15 transactions."),
        ]
    )
    agent, sessions = build_agent(first_claude, search_succeeds())
    await agent.answer(SESSION_ID, "Transactions for C-1004?")

    follow_up_claude = ScriptedClaude([final_answer("Here are their chargebacks.")])
    follow_up_agent = QueryAgent(
        follow_up_claude, search_succeeds(), sessions, CLOCK, NEW_YORK, 8, 10
    )
    await follow_up_agent.answer(SESSION_ID, "Now their chargebacks")

    opening_user_message = str(follow_up_claude.requests[0][0]["content"])
    assert '<question number="1">Transactions for C-1004?</question>' in opening_user_message
    assert '"value":"C-1004"' in opening_user_message
    assert "<current_question>Now their chargebacks</current_question>" in opening_user_message
    assert "C-1004 has 15 transactions." not in opening_user_message  # replies are never kept
