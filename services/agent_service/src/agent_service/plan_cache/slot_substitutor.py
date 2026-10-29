"""Between a concrete query plan and a reusable one with {"$slot": ...} markers.

`template_plan` (learning, after the agent answered):
    plan  search_transactions customer_id eq "10001641"
    slots {"customer_id": "10001641"}
    -> plan template with value {"$slot": "customer_id", "as": "text"}

`fill_plan_template` (a cache hit): puts the new question's slot values back in and
validates the result as a `QueryPlan`.

Markers remember how the plan's value was written relative to what the user typed ("as"):
the text as typed, upper/lower/title case ("rogers" -> "Rogers"), or a number parsed from
"$1,500". A template is refused when a slot can't be found in the plan, or when a value the
question mentions is still written into the plan: reusing it for another question would
quietly search for the old value.
"""

import hashlib
import re
from collections.abc import Callable, Mapping
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from shared.contracts import QueryPlan

SlotForm = Literal["text", "upper", "lower", "title", "number", "number_text"]

# Keys whose values are structure, never user-typed values.
STRUCTURAL_KEYS = frozenset(
    {"tool_name", "kind", "field", "operator", "logic", "negate", "relative", "sort"}
)
TEXT_FORMS: dict[SlotForm, Callable[[str], str]] = {
    "text": lambda value: value,
    "upper": str.upper,
    "lower": str.lower,
    "title": str.title,
}
NUMBER_PATTERN = re.compile(
    r"^\s*(?:[a-z]{0,3}\$|[€£])?\s*(-?[0-9][0-9,]*(?:\.[0-9]+)?)\s*(?:[a-z]{3})?\s*$",
    re.IGNORECASE,
)


class StoredPlanTemplate(BaseModel):
    template_id: str  # sha256 of the normalized template text
    template_text: str
    slot_names: tuple[str, ...]  # sorted
    plan_template: dict[str, Any]  # QueryPlan JSON whose values may be slot markers
    created_at: datetime
    hit_count: int = 0
    last_used_at: datetime | None = None


class UnslottablePlanError(Exception):
    """The plan can't be made reusable safely; it is not stored."""


class PlanTemplateFillError(Exception):
    """A stored template couldn't be filled with these slot values (the agent answers)."""


def template_id_of(template_text: str) -> str:
    return hashlib.sha256(template_text.encode()).hexdigest()


def template_plan(
    template_text: str,
    plan: QueryPlan,
    slot_values: Mapping[str, str],
    question: str,
    now: datetime,
) -> StoredPlanTemplate:
    used_slots: set[str] = set()

    def to_marker(value: Any) -> Any:
        for name, slot_value in slot_values.items():
            form = form_of(value, slot_value)
            if form is not None:
                used_slots.add(name)
                return {"$slot": name, "as": form}
        return value

    plan_json = map_values(plan.model_dump(mode="json", exclude_none=True), to_marker)
    unused = set(slot_values) - used_slots
    if unused:
        raise UnslottablePlanError(f"slots not found in the plan: {sorted(unused)}")
    baked_in = [value for value in literal_values(plan_json) if mentions(question, value)]
    if baked_in:
        raise UnslottablePlanError(f"plan values from the question are not slots: {baked_in}")
    return StoredPlanTemplate(
        template_id=template_id_of(template_text),
        template_text=template_text,
        slot_names=tuple(sorted(slot_values)),
        plan_template=plan_json,
        created_at=now,
    )


def fill_plan_template(template: StoredPlanTemplate, slot_values: Mapping[str, str]) -> QueryPlan:
    missing = set(template.slot_names) - set(slot_values)
    if missing:
        raise PlanTemplateFillError(f"no value for slots {sorted(missing)}")

    def from_marker(value: Any) -> Any:
        if not is_marker(value):
            return value
        return slot_in_form(slot_values[value["$slot"]], value["as"])

    try:
        return QueryPlan.model_validate(map_values(template.plan_template, from_marker))
    except (ValidationError, ValueError) as error:
        raise PlanTemplateFillError(f"the filled plan is invalid: {error}") from error


def form_of(plan_value: Any, slot_value: str) -> SlotForm | None:
    """How the plan wrote this slot's value, or None if the plan value isn't the slot."""
    if isinstance(plan_value, bool):
        return None
    if isinstance(plan_value, int | float):
        number = parse_number(slot_value)
        return "number" if number is not None and number == Decimal(str(plan_value)) else None
    if not isinstance(plan_value, str):
        return None
    for form, transform in TEXT_FORMS.items():
        if transform(slot_value) == plan_value:
            return form
    number = parse_number(slot_value)
    if number is not None and parse_number(plan_value) == number:
        return "number_text"
    return None


def slot_in_form(slot_value: str, form: SlotForm) -> Any:
    if form in TEXT_FORMS:
        return TEXT_FORMS[form](slot_value)
    number = parse_number(slot_value)
    if number is None:
        raise PlanTemplateFillError(f"{slot_value!r} is not a number")
    if form == "number_text":
        return str(number)
    return int(number) if number == number.to_integral_value() else float(number)


def parse_number(text: str) -> Decimal | None:
    match = NUMBER_PATTERN.match(text)
    if match is None:
        return None
    try:
        return Decimal(match.group(1).replace(",", ""))
    except InvalidOperation:
        return None


def is_marker(value: Any) -> bool:
    return isinstance(value, dict) and set(value) == {"$slot", "as"}


def map_values(node: Any, transform: Callable[[Any], Any]) -> Any:
    """Applies `transform` to every value leaf (and list item), skipping structural keys."""
    if is_marker(node):
        return transform(node)
    if isinstance(node, dict):
        return {
            key: value if key in STRUCTURAL_KEYS else map_values(value, transform)
            for key, value in node.items()
        }
    if isinstance(node, list):
        return [map_values(item, transform) for item in node]
    return transform(node)


def literal_values(node: Any) -> list[str | int | float]:
    """Plain values still written into a plan template (not markers, not structure)."""
    found: list[str | int | float] = []

    def collect(value: Any) -> Any:
        is_literal = isinstance(value, str | int | float) and not isinstance(value, bool)
        if is_literal and not is_marker(value):
            found.append(value)
        return value

    map_values(node, collect)
    return found


def mentions(question: str, value: str | int | float) -> bool:
    """Whether the question contains this value as a whole word (case-insensitive)."""
    text = str(value).strip()
    if not text:
        return False
    variants = {text, text.replace("_", " ")}
    return any(
        re.search(rf"(?<![\w]){re.escape(variant)}(?![\w])", question, re.IGNORECASE)
        for variant in variants
    )
