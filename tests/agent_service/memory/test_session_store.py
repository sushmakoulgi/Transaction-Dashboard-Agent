import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import ValidationError

from agent_service.memory.session_store import (
    InMemorySessionStore,
    InvalidSessionIdError,
    SessionEntry,
)
from shared.contracts import QueryPlan

START = datetime(2026, 10, 27, 15, tzinfo=UTC)


class AdjustableClock:
    def __init__(self, moment: datetime) -> None:
        self.moment = moment

    def now(self) -> datetime:
        return self.moment

    def advance(self, **delta: float) -> None:
        self.moment += timedelta(**delta)


def build_store(
    max_entries: int = 10, idle_minutes: int = 120
) -> tuple[InMemorySessionStore, AdjustableClock]:
    clock = AdjustableClock(START)
    return InMemorySessionStore(max_entries, timedelta(minutes=idle_minutes), clock), clock


def customer_plan(customer_id: str) -> QueryPlan:
    return QueryPlan.model_validate(
        {
            "steps": [
                {
                    "tool_name": "search_transactions",
                    "filter": {
                        "kind": "condition",
                        "field": "customer_id",
                        "operator": "eq",
                        "value": customer_id,
                    },
                }
            ]
        }
    )


def entry(question: str, plan: QueryPlan | None = None) -> SessionEntry:
    return SessionEntry(user_question=question, query_plan=plan)


# --- What can be stored ---


def test_entry_holds_only_the_question_and_the_plan() -> None:
    assert set(SessionEntry.model_fields) == {"user_question", "query_plan"}


@pytest.mark.parametrize(
    "extra_field",
    [
        {"assistant_reply": "C-1004 has 7 transactions."},
        {"tool_results": [{"total_count": 7}]},
        {"result_rows": [{"transaction_id": "x"}]},
    ],
)
def test_entry_rejects_replies_and_results(extra_field: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        SessionEntry.model_validate(
            {"user_question": "Transactions for C-1004?", "query_plan": None, **extra_field}
        )


def test_entry_rejects_empty_and_oversized_questions() -> None:
    with pytest.raises(ValidationError):
        entry("")
    with pytest.raises(ValidationError):
        entry("x" * 2001)


async def test_store_accepts_only_session_entries() -> None:
    store, _ = build_store()
    with pytest.raises(TypeError, match="only SessionEntry"):
        await store.append_entry("s1", {"user_question": "hi", "tool_results": []})  # type: ignore[arg-type]


# --- Behaviour ---


async def test_entries_come_back_oldest_first_with_their_plans() -> None:
    store, _ = build_store()
    await store.append_entry("s1", entry("Transactions for C-1004?", customer_plan("C-1004")))
    await store.append_entry("s1", entry("Now their chargebacks"))
    entries = await store.load_recent_entries("s1")
    assert [item.user_question for item in entries] == [
        "Transactions for C-1004?",
        "Now their chargebacks",
    ]
    assert entries[0].query_plan == customer_plan("C-1004")
    assert entries[1].query_plan is None


async def test_keeps_only_the_most_recent_entries() -> None:
    store, _ = build_store(max_entries=3)
    for number in range(1, 6):
        await store.append_entry("s1", entry(f"question {number}"))
    entries = await store.load_recent_entries("s1")
    assert [item.user_question for item in entries] == ["question 3", "question 4", "question 5"]


async def test_sessions_are_isolated() -> None:
    store, _ = build_store()
    await store.append_entry("alice", entry("Alice's question"))
    await store.append_entry("bob", entry("Bob's question"))
    assert [item.user_question for item in await store.load_recent_entries("alice")] == [
        "Alice's question"
    ]
    assert await store.load_recent_entries("nobody") == []


async def test_idle_sessions_expire() -> None:
    store, clock = build_store(idle_minutes=120)
    await store.append_entry("s1", entry("question"))
    clock.advance(minutes=119)
    assert len(await store.load_recent_entries("s1")) == 1  # this read also refreshes it
    clock.advance(minutes=119)
    assert len(await store.load_recent_entries("s1")) == 1
    clock.advance(minutes=121)
    assert await store.load_recent_entries("s1") == []


async def test_delete_session() -> None:
    store, _ = build_store()
    await store.append_entry("s1", entry("question"))
    await store.delete_session("s1")
    await store.delete_session("never-existed")
    assert await store.load_recent_entries("s1") == []


async def test_returned_entries_are_a_copy() -> None:
    store, _ = build_store()
    await store.append_entry("s1", entry("question"))
    (await store.load_recent_entries("s1")).clear()
    assert len(await store.load_recent_entries("s1")) == 1


@pytest.mark.parametrize("session_id", ["", "has space", "x" * 129, "../etc/passwd", "a;b"])
async def test_rejects_malformed_session_ids(session_id: str) -> None:
    store, _ = build_store()
    with pytest.raises(InvalidSessionIdError):
        await store.load_recent_entries(session_id)
    with pytest.raises(InvalidSessionIdError):
        await store.append_entry(session_id, entry("question"))


async def test_concurrent_appends_are_all_recorded() -> None:
    store, _ = build_store(max_entries=50)
    await asyncio.gather(*(store.append_entry("s1", entry(f"q{n}")) for n in range(50)))
    assert len(await store.load_recent_entries("s1")) == 50
