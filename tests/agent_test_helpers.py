"""A scripted stand-in for Claude, returning real SDK content blocks."""

import copy
from collections.abc import Sequence
from typing import Any

from anthropic.types.beta import (
    BetaMessageParam,
    BetaTextBlock,
    BetaTextBlockParam,
    BetaThinkingBlock,
    BetaToolParam,
    BetaToolUseBlock,
)

from agent_service.llm.claude_client import ClaudeReply, ToolUseRequest


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
