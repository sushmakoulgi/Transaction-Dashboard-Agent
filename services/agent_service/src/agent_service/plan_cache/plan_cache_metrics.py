"""How the plan cache is doing: hits (exact vs similar), misses by reason, what was learned.

The counters live in Chroma (collection `plan_cache_metrics`, one record), so they build up
across restarts. Each `record` adds to the stored totals. While Chroma can't be reached,
increments wait in memory and are added on the next successful write, so totals are never
lost and a fresh process never overwrites them with smaller numbers.

`exact` hits are lookups whose nearest template has the very same normalized text: what a
Redis exact-key cache would have caught. Their share decides whether Redis is worth adding.
"""

import asyncio
import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from agent_service.plan_cache.chroma_connection import ChromaConnection, PlanStoreUnavailableError
from agent_service.plan_cache.chroma_plan_store import as_batch
from shared.clock import Clock

logger = logging.getLogger(__name__)

METRICS_COLLECTION = "plan_cache_metrics"
COUNTERS_RECORD_ID = "counters"
PLACEHOLDER_EMBEDDING = [1.0]  # Chroma records need one; the counters are never searched
SINCE_KEY = "since"


class PlanCacheOutcome(StrEnum):
    # Lookups: one per question while the cache is active
    HIT_EXACT = "hit_exact"
    HIT_SIMILAR = "hit_similar"
    MISS_NO_TEMPLATES = "miss_no_templates"
    MISS_BELOW_THRESHOLD = "miss_below_threshold"
    MISS_FOLLOW_UP = "miss_follow_up"
    MISS_SLOT_MISMATCH = "miss_slot_mismatch"
    MISS_INVALID_PLAN = "miss_invalid_plan"
    MISS_UNAVAILABLE = "miss_unavailable"
    # Learning: after the agent answered a miss
    STORED = "stored"
    SKIPPED_SIMILAR = "skipped_similar"
    SKIPPED_UNSUCCESSFUL = "skipped_unsuccessful"
    SKIPPED_UNSLOTTABLE = "skipped_unslottable"

    @property
    def is_lookup(self) -> bool:
        return self.value.startswith(("hit_", "miss_"))


@dataclass(frozen=True)
class PlanCacheMetricsSnapshot:
    counts: dict[PlanCacheOutcome, int]
    since: datetime | None  # when the first outcome was recorded
    store_available: bool  # False: Chroma unreachable, counts are this process's only

    @property
    def lookups(self) -> int:
        return sum(count for outcome, count in self.counts.items() if outcome.is_lookup)

    @property
    def hits(self) -> int:
        return self.counts[PlanCacheOutcome.HIT_EXACT] + self.counts[PlanCacheOutcome.HIT_SIMILAR]

    @property
    def hit_rate(self) -> float:
        return round(self.hits / self.lookups, 3) if self.lookups else 0.0

    @property
    def exact_match_rate(self) -> float:
        exact = self.counts[PlanCacheOutcome.HIT_EXACT]
        return round(exact / self.lookups, 3) if self.lookups else 0.0


@dataclass
class _Totals:
    counts: Counter[PlanCacheOutcome] = field(default_factory=Counter)
    since: datetime | None = None


class PlanCacheMetrics:
    def __init__(
        self,
        connection: ChromaConnection,
        clock: Clock,
        collection_name: str = METRICS_COLLECTION,
    ) -> None:
        self._connection = connection
        self._clock = clock
        self._collection_name = collection_name
        self._pending = _Totals()  # not yet written to Chroma
        self._lock = asyncio.Lock()

    async def record(self, outcome: PlanCacheOutcome) -> None:
        """Counts one outcome. Never raises: metrics must not fail a question."""
        async with self._lock:
            self._pending.counts[outcome] += 1
            self._pending.since = self._pending.since or self._clock.now()
            try:
                stored = await self._load()
                await self._save(combined(stored, self._pending))
                self._pending = _Totals()
            except PlanStoreUnavailableError as error:
                logger.warning("plan cache metrics kept in memory: %s", error)

    async def snapshot(self) -> PlanCacheMetricsSnapshot:
        async with self._lock:
            try:
                totals = combined(await self._load(), self._pending)
                store_available = True
            except PlanStoreUnavailableError:
                totals, store_available = self._pending, False
        counts = {outcome: totals.counts[outcome] for outcome in PlanCacheOutcome}
        return PlanCacheMetricsSnapshot(counts, totals.since, store_available)

    async def _load(self) -> _Totals:
        collection = await self._connection.collection(self._collection_name)
        stored = await self._connection.run(
            lambda: collection.get(ids=[COUNTERS_RECORD_ID], include=["metadatas"]), "get"
        )
        if not stored["ids"]:
            return _Totals()
        metadata = (stored["metadatas"] or [{}])[0]
        counts: Counter[PlanCacheOutcome] = Counter()
        for outcome in PlanCacheOutcome:
            value = metadata.get(outcome.value)
            counts[outcome] = value if isinstance(value, int) else 0
        since = metadata.get(SINCE_KEY)
        return _Totals(counts, datetime.fromisoformat(since) if isinstance(since, str) else None)

    async def _save(self, totals: _Totals) -> None:
        metadata: dict[str, str | int] = {
            outcome.value: totals.counts[outcome] for outcome in PlanCacheOutcome
        }
        if totals.since is not None:
            metadata[SINCE_KEY] = totals.since.isoformat()
        collection = await self._connection.collection(self._collection_name)
        await self._connection.run(
            lambda: collection.upsert(
                ids=[COUNTERS_RECORD_ID],
                embeddings=as_batch(PLACEHOLDER_EMBEDDING),
                metadatas=[metadata],
            ),
            "upsert",
        )


def combined(stored: _Totals, pending: _Totals) -> _Totals:
    starts = [moment for moment in (stored.since, pending.since) if moment is not None]
    return _Totals(stored.counts + pending.counts, min(starts) if starts else None)
