"""The messages that start each turn: the question with its session context, then the date.

    [user:   <previous_questions>...</previous_questions><current_question>...</current_question>,
     system: Today is Tuesday, 2026-10-27 (America/New_York) ...]

The date is a mid-conversation system message (not part of the fixed system prompt). It
follows the user message, and later turns of the tool loop follow it with assistant messages.
"""

from collections.abc import Sequence
from html import escape
from zoneinfo import ZoneInfo

from anthropic.types.beta import BetaMessageParam

from agent_service.memory.session_store import SessionEntry
from shared.clock import Clock


def build_turn_messages(
    previous_entries: Sequence[SessionEntry],
    current_question: str,
    clock: Clock,
    business_timezone: ZoneInfo,
) -> list[BetaMessageParam]:
    return [
        {
            "role": "user",
            "content": render_question_with_context(previous_entries, current_question),
        },
        {"role": "system", "content": render_date_context(clock, business_timezone)},
    ]


def render_question_with_context(
    previous_entries: Sequence[SessionEntry], current_question: str
) -> str:
    """Earlier questions (oldest first) with their plans, then the current question.

    Question text is escaped so it can't close or open these tags.
    """
    sections: list[str] = []
    if previous_entries:
        sections.append("<previous_questions>")
        for number, entry in enumerate(previous_entries, start=1):
            sections.append(f'<question number="{number}">{escape(entry.user_question)}</question>')
            if entry.query_plan is None:
                sections.append("<query_plan>none (no search was run)</query_plan>")
            else:
                plan_json = entry.query_plan.model_dump_json(exclude_none=True)
                sections.append(f"<query_plan>{escape(plan_json, quote=False)}</query_plan>")
        sections.append("</previous_questions>")
    sections.append(f"<current_question>{escape(current_question)}</current_question>")
    return "\n".join(sections)


def render_date_context(clock: Clock, business_timezone: ZoneInfo) -> str:
    today = clock.now().astimezone(business_timezone).date()
    return (
        f"Today is {today:%A}, {today.isoformat()} ({business_timezone.key}). "
        f"Only {today.year} can be searched."
    )
