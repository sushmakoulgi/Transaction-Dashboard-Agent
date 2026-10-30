"""Stored plan templates in Chroma: find the nearest one, add new ones, count hits.

One record per template: the id is the template id, the document is the normalized template
text, the embedding comes from the `EmbeddingProvider`, and the metadata holds the slot
names, the plan template (JSON) and the hit counter. Distances are cosine, so
similarity = 1 - distance.

Every Chroma failure is raised as `PlanStoreUnavailableError`; the caller then lets the
agent answer, so a stopped Chroma never fails a question.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import ValidationError

from agent_service.plan_cache.chroma_connection import ChromaConnection, PlanStoreUnavailableError
from agent_service.plan_cache.slot_substitutor import StoredPlanTemplate

TEMPLATES_COLLECTION = "query_plan_templates"
MAX_TEMPLATES_RANKED = 10_000  # top_templates reads at most this many records


@dataclass(frozen=True)
class PlanMatch:
    template: StoredPlanTemplate
    similarity: float  # 1.0 for the same embedding


class ChromaPlanStore:
    def __init__(
        self, connection: ChromaConnection, collection_name: str = TEMPLATES_COLLECTION
    ) -> None:
        self._connection = connection
        self._collection_name = collection_name

    async def nearest(self, embedding: list[float]) -> PlanMatch | None:
        """The most similar stored template, or None while nothing is stored."""
        collection = await self._connection.collection(self._collection_name)
        if await self._connection.run(collection.count, "count") == 0:
            return None
        result = await self._connection.run(
            lambda: collection.query(
                query_embeddings=as_batch(embedding),
                n_results=1,
                include=["documents", "metadatas", "distances"],
            ),
            "query",
        )
        ids, distances = result["ids"][0], (result["distances"] or [[]])[0]
        if not ids:
            return None
        template = template_from_record(
            ids[0], (result["documents"] or [[]])[0][0], (result["metadatas"] or [[]])[0][0]
        )
        return PlanMatch(template=template, similarity=1.0 - float(distances[0]))

    async def add(self, template: StoredPlanTemplate, embedding: list[float]) -> None:
        """Stores the template (replacing one with the same id)."""
        collection = await self._connection.collection(self._collection_name)
        await self._connection.run(
            lambda: collection.upsert(
                ids=[template.template_id],
                embeddings=as_batch(embedding),
                documents=[template.template_text],
                metadatas=[metadata_of(template)],
            ),
            "upsert",
        )

    async def record_hit(self, template_id: str, now: datetime) -> None:
        collection = await self._connection.collection(self._collection_name)
        stored = await self._connection.run(
            lambda: collection.get(ids=[template_id], include=["metadatas"]), "get"
        )
        if not stored["ids"]:
            return  # removed meanwhile: nothing to count
        previous = (stored["metadatas"] or [{}])[0].get("hit_count")
        hit_count = (previous if isinstance(previous, int) else 0) + 1
        await self._connection.run(
            lambda: collection.update(
                ids=[template_id],
                metadatas=[{"hit_count": hit_count, "last_used_at": now.isoformat()}],
            ),
            "update",
        )

    async def top_templates(self, limit: int) -> list[StoredPlanTemplate]:
        """The most used templates, most hits first."""
        collection = await self._connection.collection(self._collection_name)
        stored = await self._connection.run(
            lambda: collection.get(include=["documents", "metadatas"], limit=MAX_TEMPLATES_RANKED),
            "get",
        )
        templates = [
            template_from_record(template_id, document, metadata)
            for template_id, document, metadata in zip(
                stored["ids"], stored["documents"] or [], stored["metadatas"] or [], strict=True
            )
        ]
        templates.sort(key=lambda template: (-template.hit_count, template.template_text))
        return templates[:limit]


def as_batch(embedding: list[float]) -> list[Sequence[float]]:
    return [embedding]


def metadata_of(template: StoredPlanTemplate) -> dict[str, str | int]:
    return {
        "slot_names": json.dumps(list(template.slot_names)),
        "plan_template": json.dumps(template.plan_template, separators=(",", ":")),
        "created_at": template.created_at.isoformat(),
        "hit_count": template.hit_count,
        "last_used_at": template.last_used_at.isoformat() if template.last_used_at else "",
    }


def template_from_record(
    template_id: str, document: str | None, metadata: Any
) -> StoredPlanTemplate:
    try:
        return StoredPlanTemplate(
            template_id=template_id,
            template_text=document or "",
            slot_names=tuple(json.loads(metadata["slot_names"])),
            plan_template=json.loads(metadata["plan_template"]),
            created_at=datetime.fromisoformat(metadata["created_at"]),
            hit_count=int(metadata.get("hit_count", 0)),
            last_used_at=(
                datetime.fromisoformat(metadata["last_used_at"])
                if metadata.get("last_used_at")
                else None
            ),
        )
    except (KeyError, TypeError, ValueError, ValidationError) as error:
        raise PlanStoreUnavailableError(f"stored template {template_id} is malformed") from error
