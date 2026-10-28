"""Answers one question: the Claude tool-use loop over the MCP tools.

1. Load the session's earlier questions + plans and build the turn's opening messages.
2. Call Claude. While it asks for tools: run them (concurrently), and send every result back
   in one user message; Claude's own content goes back unmodified.
3. Stop on a final answer or after `max_tool_rounds` Claude calls.
4. Save only the question and its query plan; return the reply, plan and full result page.

The turn's messages (tool calls, results, replies) are discarded when the turn ends.
"""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from anthropic.types.beta import (
    BetaMessageParam,
    BetaTextBlockParam,
    BetaToolParam,
    BetaToolResultBlockParam,
)

from agent_service.agent.query_plan_recorder import QueryPlanRecorder
from agent_service.agent.tool_result_compactor import ToolResultCompactor
from agent_service.llm.claude_client import ClaudeReply
from agent_service.llm.system_prompt import system_blocks
from agent_service.llm.turn_input import build_turn_messages
from agent_service.mcp_bridge.mcp_tool_gateway import ToolCallOutcome
from agent_service.memory.session_store import MAX_QUESTION_LENGTH, SessionEntry, SessionStore
from shared.clock import Clock
from shared.contracts import QueryPlan, SearchableEntity

NO_ANSWER_TEXT = "I couldn't produce an answer to that question. Please try rephrasing it."


class ClaudeMessenger(Protocol):
    async def create_message(
        self,
        system: Sequence[BetaTextBlockParam],
        messages: Sequence[BetaMessageParam],
        tools: Sequence[BetaToolParam],
    ) -> ClaudeReply: ...


class ToolGateway(Protocol):
    @property
    def tool_definitions(self) -> tuple[BetaToolParam, ...]: ...

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> ToolCallOutcome: ...


class InvalidQuestionError(ValueError):
    pass


@dataclass(frozen=True)
class AgentAnswer:
    reply: str
    query_plan: QueryPlan | None
    result_page: dict[str, Any] | None  # the last successful search's full page, for the UI
    result_entity: SearchableEntity | None
    claude_calls: int
    tool_calls: int
    reached_round_limit: bool


class QueryAgent:
    def __init__(
        self,
        claude: ClaudeMessenger,
        tools: ToolGateway,
        sessions: SessionStore,
        clock: Clock,
        business_timezone: ZoneInfo,
        max_tool_rounds: int,
        tool_result_preview_rows: int,
    ) -> None:
        self._claude = claude
        self._tools = tools
        self._sessions = sessions
        self._clock = clock
        self._business_timezone = business_timezone
        self._max_tool_rounds = max_tool_rounds
        self._compactor = ToolResultCompactor(tool_result_preview_rows)

    async def answer(self, session_id: str, question: str) -> AgentAnswer:
        """Raises `InvalidQuestionError`, `InvalidSessionIdError` or a `ClaudeError`;
        nothing is saved to the session unless an answer is produced."""
        question = question.strip()
        if not question or len(question) > MAX_QUESTION_LENGTH:
            raise InvalidQuestionError(
                f"the question must be 1-{MAX_QUESTION_LENGTH} characters long"
            )
        previous_entries = await self._sessions.load_recent_entries(session_id)
        messages = build_turn_messages(
            previous_entries, question, self._clock, self._business_timezone
        )
        recorder = QueryPlanRecorder()
        claude_calls = tool_calls = 0
        reply_text: str | None = None

        while claude_calls < self._max_tool_rounds:
            reply = await self._claude.create_message(
                system_blocks(), messages, self._tools.tool_definitions
            )
            claude_calls += 1
            messages.append({"role": "assistant", "content": reply.content})
            if not reply.wants_tools:
                reply_text = reply.text.strip() or NO_ANSWER_TEXT
                break
            messages.append({"role": "user", "content": await self._run_tools(reply, recorder)})
            tool_calls += len(reply.tool_use_requests)

        reached_round_limit = reply_text is None
        if reply_text is None:
            reply_text = (
                f"I couldn't finish answering within {self._max_tool_rounds} steps. "
                "The table shows the last results I found; try a more specific question."
            )
        query_plan = recorder.build_plan()
        await self._sessions.append_entry(
            session_id, SessionEntry(user_question=question, query_plan=query_plan)
        )
        return AgentAnswer(
            reply=reply_text,
            query_plan=query_plan,
            result_page=recorder.last_result_page,
            result_entity=recorder.last_result_entity,
            claude_calls=claude_calls,
            tool_calls=tool_calls,
            reached_round_limit=reached_round_limit,
        )

    async def _run_tools(
        self, reply: ClaudeReply, recorder: QueryPlanRecorder
    ) -> list[BetaToolResultBlockParam]:
        """Runs every requested tool concurrently; results keep Claude's request order."""
        requests = reply.tool_use_requests
        outcomes = await asyncio.gather(
            *(self._tools.call_tool(request.tool_name, request.tool_input) for request in requests)
        )
        results: list[BetaToolResultBlockParam] = []
        for request, outcome in zip(requests, outcomes, strict=True):
            recorder.record(request.tool_name, request.tool_input, outcome)
            results.append(self._compactor.to_tool_result(request.tool_use_id, outcome))
        return results
