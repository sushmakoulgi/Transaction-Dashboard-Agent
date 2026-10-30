"""ChromaPlanStore against a real Chroma (the stack's `chroma` service); skipped when absent.

Each test uses its own throwaway collection, so stored plan templates are never touched.
"""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from pydantic import AnyHttpUrl

from agent_service.plan_cache.chroma_plan_store import ChromaPlanStore, PlanStoreUnavailableError
from agent_service.plan_cache.slot_substitutor import StoredPlanTemplate, template_id_of
from agent_service.settings import PlanCacheSettings

NOW = datetime(2026, 10, 29, 14, tzinfo=UTC)
CHARGEBACKS_THIS_MONTH = [1.0, 0.0, 0.0]
NEARLY_THE_SAME = [0.99, 0.14, 0.0]  # cosine similarity ~0.99
UNRELATED = [0.0, 0.0, 1.0]


def stored_template(text: str, hit_count: int = 0) -> StoredPlanTemplate:
    return StoredPlanTemplate(
        template_id=template_id_of(text),
        template_text=text,
        slot_names=("customer_id",),
        plan_template={
            "steps": [
                {
                    "tool_name": "search_chargebacks",
                    "filter": {
                        "kind": "condition",
                        "field": "customer_id",
                        "operator": "eq",
                        "value": {"$slot": "customer_id", "as": "text"},
                    },
                }
            ]
        },
        created_at=NOW,
        hit_count=hit_count,
    )


@pytest.fixture
async def store() -> AsyncIterator[ChromaPlanStore]:
    settings = PlanCacheSettings()
    collection_name = f"test_templates_{uuid.uuid4().hex[:12]}"
    try:
        plan_store = await ChromaPlanStore.connect(settings.chroma_url, 5.0, collection_name)
        await plan_store.nearest(CHARGEBACKS_THIS_MONTH)  # creates the collection
    except PlanStoreUnavailableError as error:
        pytest.skip(f"Chroma is not reachable at {settings.chroma_url}: {error}")
    yield plan_store
    await plan_store._client.delete_collection(collection_name)


async def test_an_empty_store_has_no_nearest_template(store: ChromaPlanStore) -> None:
    assert await store.nearest(CHARGEBACKS_THIS_MONTH) is None


async def test_finds_the_nearest_template_with_its_similarity(store: ChromaPlanStore) -> None:
    template = stored_template("chargebacks for customer {customer_id} this month")
    await store.add(template, CHARGEBACKS_THIS_MONTH)
    await store.add(stored_template("refunds over {amount}"), UNRELATED)

    exact = await store.nearest(CHARGEBACKS_THIS_MONTH)
    assert exact is not None
    assert exact.template == template  # everything round-trips through the metadata
    assert exact.similarity == pytest.approx(1.0, abs=1e-5)

    similar = await store.nearest(NEARLY_THE_SAME)
    assert similar is not None and similar.template.template_id == template.template_id
    assert 0.95 < similar.similarity < 1.0


async def test_adding_the_same_template_again_replaces_it(store: ChromaPlanStore) -> None:
    text = "chargebacks for customer {customer_id} this month"
    await store.add(stored_template(text), CHARGEBACKS_THIS_MONTH)
    await store.add(stored_template(text, hit_count=7), CHARGEBACKS_THIS_MONTH)
    match = await store.nearest(CHARGEBACKS_THIS_MONTH)
    assert match is not None and match.template.hit_count == 7


async def test_hits_are_counted_with_the_time_of_use(store: ChromaPlanStore) -> None:
    template = stored_template("chargebacks for customer {customer_id} this month")
    await store.add(template, CHARGEBACKS_THIS_MONTH)

    await store.record_hit(template.template_id, NOW)
    await store.record_hit(template.template_id, NOW.replace(hour=15))
    await store.record_hit("unknown-template", NOW)  # removed meanwhile: ignored

    match = await store.nearest(CHARGEBACKS_THIS_MONTH)
    assert match is not None
    assert match.template.hit_count == 2
    assert match.template.last_used_at == NOW.replace(hour=15)


async def test_an_unreachable_chroma_is_reported_not_raised_raw() -> None:
    with pytest.raises(PlanStoreUnavailableError):
        plan_store = await ChromaPlanStore.connect(AnyHttpUrl("http://127.0.0.1:9"), 1.0)
        await plan_store.nearest(CHARGEBACKS_THIS_MONTH)
