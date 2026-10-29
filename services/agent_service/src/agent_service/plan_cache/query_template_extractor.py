"""Turns a question into a reusable template plus the concrete values it mentions.

    "Chargebacks for customer 10001641 this month?"
      -> template "chargebacks for customer {customer_id} this month"
         slots    {"customer_id": "10001641"}

Questions with the same template share one stored plan (the slots are substituted in).
A small Haiku call with structured output does the extraction. Its answer is checked before
use: every slot value must appear in the question as typed, and the template must name
exactly the slots returned. Any failure raises `TemplateExtractionError`, and the caller
falls back to the full agent.
"""

import re
from collections.abc import Sequence

import anthropic
from pydantic import BaseModel, Field, ValidationError

from agent_service.llm.turn_input import render_question_with_context
from agent_service.memory.session_store import SessionEntry
from agent_service.settings import Effort

SLOT_NAME_PATTERN = r"^[a-z][a-z0-9_]{0,39}$"
SLOT_PLACEHOLDER = re.compile(r"\{([a-z][a-z0-9_]{0,39})\}")
TRAILING_PUNCTUATION = re.compile(r"[\s.?!,;:]+$")

EXTRACTION_SYSTEM_PROMPT = """\
You turn questions about payment transactions, refunds and chargebacks into reusable \
templates. Two questions with the same template must be answerable by the same search with \
only the slot values changed.

Replace every concrete value the user typed with a {slot} placeholder and return it as a \
slot. Slot names are lowercase field names such as customer_id, transaction_id, payment_id, \
merchant_name, amount, min_amount, max_amount, date, start_date, end_date, card_brand, \
payment_status, chargeback_status, reason_code. Use each name once; number repeats \
(merchant_name_2). Slot values are copied exactly as the user typed them.

Do not turn relative dates into slots: keep "today", "yesterday", "this month", \
"last month", "last 7 days" as words, because they are resolved when the search runs.

Keep the rest of the wording short and neutral: drop greetings and filler ("please", \
"can you show me"), keep words that change the meaning (status words, "not", "over", \
"refunded", "chargeback", "this month").

is_self_contained is false when the question only makes sense with the earlier questions, \
for example "does it have a refund?", "what about last month?", "sort those by amount", \
"show the first one". It is true when the question can be answered on its own.

Examples:
- "Show me all transactions for customer 10001641 please" -> \
template "transactions for customer {customer_id}", slots [customer_id=10001641], \
self-contained.
- "Which chargebacks were opened this month?" -> \
template "chargebacks opened this month", no slots, self-contained.
- "Rogers transactions over $500 that were refunded" -> \
template "{merchant_name} transactions over {amount} that were refunded", \
slots [merchant_name=Rogers, amount=$500], self-contained.
- "does it have a refund?" (after a question about one payment) -> \
template "does it have a refund", no slots, not self-contained."""


class TemplateSlot(BaseModel):
    name: str = Field(pattern=SLOT_NAME_PATTERN)
    value: str = Field(min_length=1, max_length=200)


class _ExtractionOutput(BaseModel):
    """What Haiku returns (structured output). Slots are a list: schemas need fixed keys."""

    template_text: str = Field(min_length=1, max_length=500)
    slots: list[TemplateSlot]
    is_self_contained: bool


class ExtractedQuery(BaseModel):
    template_text: str  # normalized
    slot_values: dict[str, str]
    is_self_contained: bool


class TemplateExtractionError(Exception):
    """No usable template: the call failed, or its answer did not pass the checks."""


def normalize_template(text: str) -> str:
    """Lowercase, single spaces, no trailing punctuation: equal templates compare equal."""
    return TRAILING_PUNCTUATION.sub("", " ".join(text.lower().split()))


class QueryTemplateExtractor:
    def __init__(
        self,
        anthropic_client: anthropic.AsyncAnthropic,
        model: str = "claude-haiku-5-5",
        effort: Effort = "low",
        max_tokens: int = 4000,
    ) -> None:
        self._anthropic = anthropic_client
        self._model = model
        self._effort = effort
        self._max_tokens = max_tokens

    async def extract(
        self, question: str, previous_entries: Sequence[SessionEntry]
    ) -> ExtractedQuery:
        try:
            response = await self._anthropic.messages.parse(
                model=self._model,
                max_tokens=self._max_tokens,
                system=EXTRACTION_SYSTEM_PROMPT,
                messages=[
                    {
                        "role": "user",
                        "content": render_question_with_context(previous_entries, question),
                    }
                ],
                output_format=_ExtractionOutput,
                output_config={"effort": self._effort},
            )
        except (anthropic.AnthropicError, ValidationError) as error:
            raise TemplateExtractionError(f"template extraction call failed: {error}") from error
        except TypeError as error:  # the SDK found no credentials at all
            raise TemplateExtractionError(f"template extraction call failed: {error}") from error
        if response.stop_reason != "end_turn" or response.parsed_output is None:
            raise TemplateExtractionError(
                f"template extraction stopped with {response.stop_reason!r}"
            )
        return checked_extraction(response.parsed_output, question)


def checked_extraction(output: _ExtractionOutput, question: str) -> ExtractedQuery:
    template_text = normalize_template(output.template_text)
    slot_values = {slot.name: slot.value.strip() for slot in output.slots}
    if len(slot_values) != len(output.slots):
        raise TemplateExtractionError("the same slot name was returned twice")
    if set(SLOT_PLACEHOLDER.findall(template_text)) != set(slot_values):
        raise TemplateExtractionError("the template and the slots name different slots")
    lowered_question = question.lower()
    for name, value in slot_values.items():
        if not value or value.lower() not in lowered_question:
            raise TemplateExtractionError(f"slot {name!r} has a value the question doesn't contain")
    return ExtractedQuery(
        template_text=template_text,
        slot_values=slot_values,
        is_self_contained=output.is_self_contained,
    )
