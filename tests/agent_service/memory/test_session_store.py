"""Session stores: every behaviour test runs against the in-memory store and against the
DynamoDB store (on DynamoDB Local, `make dynamodb-local`; skipped when it isn't running)."""

import asyncio
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import boto3
import pytest
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from pydantic import ValidationError

from agent_service.memory.dynamodb_session_store import (
    DynamoDbSessionStore,
    SessionStoreUnavailableError,
    create_session_table,
)
from agent_service.memory.session_store import (
    InMemorySessionStore,
    InvalidSessionIdError,
    SessionEntry,
    SessionStore,
)
from agent_service.settings import SessionSettings
from shared.contracts import QueryPlan

# In the future: DynamoDB Local's TTL sweeper uses the real clock, and would delete items whose
# `ttl_session` (computed from this clock) is already past, losing appends mid-test.
START = datetime(2100, 1, 1, 15, tzinfo=UTC)
DYNAMODB_LOCAL_URL = "http://localhost:8020"


class AdjustableClock:
    def __init__(self, moment: datetime) -> None:
        self.moment = moment

    def now(self) -> datetime:
        return self.moment

    def advance(self, **delta: float) -> None:
        self.moment += timedelta(**delta)


StoreBuilder = Callable[..., tuple[SessionStore, AdjustableClock]]


def dynamodb_local_client() -> Any:
    return boto3.client(
        "dynamodb",
        endpoint_url=DYNAMODB_LOCAL_URL,
        region_name="us-east-1",
        aws_access_key_id="local",
        aws_secret_access_key="local",
        config=Config(connect_timeout=1, retries={"max_attempts": 1}),
    )


def dynamodb_local_or_skip() -> Any:
    client = dynamodb_local_client()
    try:
        client.list_tables()
    except (BotoCoreError, ClientError) as error:
        pytest.skip(f"DynamoDB Local is not reachable at {DYNAMODB_LOCAL_URL}: {error}")
    return client


@pytest.fixture(params=["memory", "dynamodb"])
def build_store(request: pytest.FixtureRequest) -> Iterator[StoreBuilder]:
    """Builds a store of the parametrized kind: (store, clock). DynamoDB: a fresh table."""
    client = dynamodb_local_or_skip() if request.param == "dynamodb" else None
    tables: list[str] = []

    def build(
        max_entries: int = 10, idle_minutes: int = 120
    ) -> tuple[SessionStore, AdjustableClock]:
        clock = AdjustableClock(START)
        idle_timeout = timedelta(minutes=idle_minutes)
        if client is None:
            return InMemorySessionStore(max_entries, idle_timeout, clock), clock
        table = f"sessions-{uuid.uuid4().hex[:12]}"
        create_session_table(client, table)
        tables.append(table)
        return DynamoDbSessionStore(client, table, max_entries, idle_timeout, clock), clock

    yield build
    for table in tables:
        client.delete_table(TableName=table)  # type: ignore[union-attr]


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
        {"assistant_reply": "10001641 has 7 transactions."},
        {"tool_results": [{"total_count": 7}]},
        {"result_rows": [{"transaction_id": "x"}]},
    ],
)
def test_entry_rejects_replies_and_results(extra_field: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        SessionEntry.model_validate(
            {"user_question": "Transactions for 10001641?", "query_plan": None, **extra_field}
        )


def test_entry_rejects_empty_and_oversized_questions() -> None:
    with pytest.raises(ValidationError):
        entry("")
    with pytest.raises(ValidationError):
        entry("x" * 2001)


async def test_store_accepts_only_session_entries(build_store: StoreBuilder) -> None:
    store, _ = build_store()
    with pytest.raises(TypeError, match="only SessionEntry"):
        await store.append_entry("s1", {"user_question": "hi", "tool_results": []})  # type: ignore[arg-type]


# --- Behaviour ---


async def test_entries_come_back_oldest_first_with_their_plans(build_store: StoreBuilder) -> None:
    store, _ = build_store()
    await store.append_entry("s1", entry("Transactions for 10001641?", customer_plan("10001641")))
    await store.append_entry("s1", entry("Now their chargebacks"))
    entries = await store.load_recent_entries("s1")
    assert [item.user_question for item in entries] == [
        "Transactions for 10001641?",
        "Now their chargebacks",
    ]
    assert entries[0].query_plan == customer_plan("10001641")
    assert entries[1].query_plan is None


async def test_keeps_only_the_most_recent_entries(build_store: StoreBuilder) -> None:
    store, _ = build_store(max_entries=3)
    for number in range(1, 6):
        await store.append_entry("s1", entry(f"question {number}"))
    entries = await store.load_recent_entries("s1")
    assert [item.user_question for item in entries] == ["question 3", "question 4", "question 5"]


async def test_sessions_are_isolated(build_store: StoreBuilder) -> None:
    store, _ = build_store()
    await store.append_entry("alice", entry("Alice's question"))
    await store.append_entry("bob", entry("Bob's question"))
    assert [item.user_question for item in await store.load_recent_entries("alice")] == [
        "Alice's question"
    ]
    assert await store.load_recent_entries("nobody") == []


async def test_idle_sessions_expire(build_store: StoreBuilder) -> None:
    store, clock = build_store(idle_minutes=120)
    await store.append_entry("s1", entry("question"))
    clock.advance(minutes=119)
    assert len(await store.load_recent_entries("s1")) == 1  # this read also refreshes it
    clock.advance(minutes=119)
    assert len(await store.load_recent_entries("s1")) == 1
    clock.advance(minutes=121)
    assert await store.load_recent_entries("s1") == []


async def test_delete_session(build_store: StoreBuilder) -> None:
    store, _ = build_store()
    await store.append_entry("s1", entry("question"))
    await store.delete_session("s1")
    await store.delete_session("never-existed")
    assert await store.load_recent_entries("s1") == []


async def test_returned_entries_are_a_copy(build_store: StoreBuilder) -> None:
    store, _ = build_store()
    await store.append_entry("s1", entry("question"))
    (await store.load_recent_entries("s1")).clear()
    assert len(await store.load_recent_entries("s1")) == 1


@pytest.mark.parametrize("session_id", ["", "has space", "x" * 129, "../etc/passwd", "a;b"])
async def test_rejects_malformed_session_ids(build_store: StoreBuilder, session_id: str) -> None:
    store, _ = build_store()
    with pytest.raises(InvalidSessionIdError):
        await store.load_recent_entries(session_id)
    with pytest.raises(InvalidSessionIdError):
        await store.append_entry(session_id, entry("question"))


async def test_concurrent_appends_are_all_recorded(build_store: StoreBuilder) -> None:
    """DynamoDB: optimistic writes retry on conflict, so no append overwrites another."""
    store, _ = build_store(max_entries=50)
    count = 50 if isinstance(store, InMemorySessionStore) else 10
    await asyncio.gather(*(store.append_entry("s1", entry(f"q{n}")) for n in range(count)))
    entries = await store.load_recent_entries("s1")
    assert sorted(item.user_question for item in entries) == sorted(f"q{n}" for n in range(count))


# --- DynamoDB specifics ---


async def test_dynamodb_expiry_is_checked_even_before_ttl_deletes_the_item() -> None:
    """DynamoDB's TTL deletes items only eventually; the store must not serve them meanwhile."""
    client = dynamodb_local_or_skip()
    table = f"sessions-{uuid.uuid4().hex[:12]}"
    create_session_table(client, table)
    try:
        clock = AdjustableClock(START)
        store = DynamoDbSessionStore(client, table, 10, timedelta(minutes=120), clock)
        await store.append_entry("s1", entry("question"))
        clock.advance(minutes=121)
        assert await store.load_recent_entries("s1") == []
        assert "Item" in client.get_item(TableName=table, Key={"sessionId": {"S": "s1"}})
        await store.append_entry("s1", entry("new question"))  # an expired session restarts
        assert [e.user_question for e in await store.load_recent_entries("s1")] == ["new question"]
    finally:
        client.delete_table(TableName=table)


async def test_dynamodb_failures_become_session_store_unavailable() -> None:
    """A missing table: one clear error (503 in the API), not a raw boto3 exception."""
    client = dynamodb_local_or_skip()
    store = DynamoDbSessionStore(
        client, "no-such-table", 10, timedelta(minutes=120), AdjustableClock(START)
    )
    with pytest.raises(SessionStoreUnavailableError):
        await store.load_recent_entries("s1")


def test_dynamodb_store_needs_a_table_name() -> None:
    with pytest.raises(ValidationError, match="SESSION_TABLE_NAME"):
        SessionSettings(_env_file=None, session_store="dynamodb")  # type: ignore[call-arg]
    assert SessionSettings(_env_file=None).session_store == "memory"  # type: ignore[call-arg]
