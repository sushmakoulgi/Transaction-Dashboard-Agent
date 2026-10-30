"""PlanCacheMetrics against a real Chroma (throwaway collection); skipped when absent."""

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from typing import TypeVar

import pytest

from agent_service.plan_cache.chroma_connection import ChromaConnection, PlanStoreUnavailableError
from agent_service.plan_cache.plan_cache_metrics import (
    PlanCacheMetrics,
    PlanCacheMetricsSnapshot,
    PlanCacheOutcome,
)
from agent_service.settings import PlanCacheSettings
from shared.clock import FixedClock

T = TypeVar("T")
FIRST_DAY = datetime(2026, 10, 29, 14, tzinfo=UTC)
CLOCK = FixedClock(FIRST_DAY)


class SwitchableConnection(ChromaConnection):
    """A real connection whose calls can be made to fail, as if Chroma were down."""

    is_down = False

    async def run(self, operation: Callable[[], Awaitable[T]], action: str) -> T:
        if self.is_down:
            raise PlanStoreUnavailableError("Chroma is down (test)")
        return await super().run(operation, action)


@pytest.fixture
async def connection() -> AsyncIterator[tuple[SwitchableConnection, str]]:
    settings = PlanCacheSettings()
    chroma = SwitchableConnection(settings.chroma_url, 5.0)
    collection_name = f"test_metrics_{uuid.uuid4().hex[:12]}"
    try:
        await chroma.collection(collection_name)
    except PlanStoreUnavailableError as error:
        pytest.skip(f"Chroma is not reachable at {settings.chroma_url}: {error}")
    yield chroma, collection_name
    chroma.is_down = False
    await chroma.delete_collection(collection_name)


async def test_counts_build_up_and_survive_a_restart(
    connection: tuple[SwitchableConnection, str],
) -> None:
    chroma, collection_name = connection
    metrics = PlanCacheMetrics(chroma, CLOCK, collection_name)
    for outcome in (
        PlanCacheOutcome.HIT_EXACT,
        PlanCacheOutcome.HIT_EXACT,
        PlanCacheOutcome.HIT_SIMILAR,
        PlanCacheOutcome.MISS_BELOW_THRESHOLD,
        PlanCacheOutcome.STORED,
    ):
        await metrics.record(outcome)

    # A new process (a restart) reads the same totals.
    after_restart = PlanCacheMetrics(chroma, FixedClock(FIRST_DAY.replace(day=30)), collection_name)
    await after_restart.record(PlanCacheOutcome.MISS_FOLLOW_UP)
    snapshot = await after_restart.snapshot()

    assert snapshot.store_available is True
    assert snapshot.since == FIRST_DAY  # the first ever outcome, not the restart
    assert snapshot.counts[PlanCacheOutcome.HIT_EXACT] == 2
    assert snapshot.counts[PlanCacheOutcome.MISS_FOLLOW_UP] == 1
    assert snapshot.lookups == 5  # STORED is learning, not a lookup
    assert (snapshot.hit_rate, snapshot.exact_match_rate) == (0.6, 0.4)


async def test_counts_wait_in_memory_while_chroma_is_down(
    connection: tuple[SwitchableConnection, str],
) -> None:
    chroma, collection_name = connection
    metrics = PlanCacheMetrics(chroma, CLOCK, collection_name)
    await metrics.record(PlanCacheOutcome.HIT_SIMILAR)

    chroma.is_down = True
    await metrics.record(PlanCacheOutcome.MISS_UNAVAILABLE)  # never raises
    while_down = await metrics.snapshot()
    assert while_down.store_available is False
    assert while_down.lookups == 1  # only what this process still holds

    chroma.is_down = False
    await metrics.record(PlanCacheOutcome.HIT_EXACT)  # writes the waiting count too
    snapshot = await PlanCacheMetrics(chroma, CLOCK, collection_name).snapshot()
    assert snapshot.counts[PlanCacheOutcome.HIT_SIMILAR] == 1
    assert snapshot.counts[PlanCacheOutcome.MISS_UNAVAILABLE] == 1
    assert snapshot.counts[PlanCacheOutcome.HIT_EXACT] == 1


def test_rates_are_zero_before_any_lookup() -> None:
    empty = PlanCacheMetricsSnapshot(
        {outcome: 0 for outcome in PlanCacheOutcome}, since=None, store_available=True
    )
    assert (empty.lookups, empty.hit_rate, empty.exact_match_rate) == (0, 0.0, 0.0)


def test_lookup_outcomes_are_hits_and_misses_only() -> None:
    lookups = {outcome for outcome in PlanCacheOutcome if outcome.is_lookup}
    assert PlanCacheOutcome.STORED not in lookups
    assert PlanCacheOutcome.SKIPPED_SIMILAR not in lookups
    assert len(lookups) == 8
