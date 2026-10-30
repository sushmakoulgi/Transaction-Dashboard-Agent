"""Stored plan templates in Chroma: find the nearest one, add new ones, count hits.

One record per template: the id is the template id, the document is the normalized template
text, the embedding comes from the `EmbeddingProvider`, and the metadata holds the slot
names, the plan template (JSON) and the hit counter. Distances are cosine, so
similarity = 1 - distance.

Every Chroma failure is raised as `PlanStoreUnavailableError`; the caller then lets the
agent answer, so a stopped Chroma never fails a question. The Chroma client sets no request
timeout of its own, so every call here runs under `timeout_seconds`.
"""

import asyncio
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Self

import chromadb
from chromadb.api.async_api import AsyncClientAPI
from chromadb.api.models.AsyncCollection import AsyncCollection
from chromadb.config import Settings as ChromaSettings
from pydantic import AnyHttpUrl, ValidationError

from agent_service.plan_cache.slot_substitutor import StoredPlanTemplate

TEMPLATES_COLLECTION = "query_plan_templates"


@dataclass(frozen=True)
class PlanMatch:
    template: StoredPlanTemplate
    similarity: float  # 1.0 for the same embedding


class PlanStoreUnavailableError(Exception):
    """Chroma could not be reached or answered unexpectedly."""


class ChromaPlanStore:
    def __init__(
        self,
        client: AsyncClientAPI,
        timeout_seconds: float = 5.0,
        collection_name: str = TEMPLATES_COLLECTION,
    ) -> None:
        self._client = client
        self._timeout_seconds = timeout_seconds
        self._collection_name = collection_name
        self._collection: AsyncCollection | None = None

    @classmethod
    async def connect(
        cls, url: AnyHttpUrl, timeout_seconds: float, collection_name: str = TEMPLATES_COLLECTION
    ) -> Self:
        try:
            async with asyncio.timeout(timeout_seconds):
                client = await chromadb.AsyncHttpClient(
                    host=url.host or "localhost",
                    port=url.port or 8000,
                    ssl=url.scheme == "https",
                    settings=ChromaSettings(anonymized_telemetry=False),
                )
        except Exception as error:  # the client raises many types; all mean "not usable"
            raise PlanStoreUnavailableError(f"Chroma is not reachable: {error!r}") from error
        return cls(client, timeout_seconds, collection_name)

    async def nearest(self, embedding: list[float]) -> PlanMatch | None:
        """The most similar stored template, or None while nothing is stored."""
        collection = await self._templates()
        try:
            async with asyncio.timeout(self._timeout_seconds):
                if await collection.count() == 0:
                    return None
                result = await collection.query(
                    query_embeddings=as_batch(embedding),
                    n_results=1,
                    include=["documents", "metadatas", "distances"],
                )
        except Exception as error:
            raise PlanStoreUnavailableError(f"Chroma query failed: {error!r}") from error
        ids, distances = result["ids"][0], (result["distances"] or [[]])[0]
        if not ids:
            return None
        template = template_from_record(
            ids[0], (result["documents"] or [[]])[0][0], (result["metadatas"] or [[]])[0][0]
        )
        return PlanMatch(template=template, similarity=1.0 - float(distances[0]))

    async def add(self, template: StoredPlanTemplate, embedding: list[float]) -> None:
        """Stores the template (replacing one with the same id)."""
        collection = await self._templates()
        try:
            async with asyncio.timeout(self._timeout_seconds):
                await collection.upsert(
                    ids=[template.template_id],
                    embeddings=as_batch(embedding),
                    documents=[template.template_text],
                    metadatas=[metadata_of(template)],
                )
        except Exception as error:
            raise PlanStoreUnavailableError(f"Chroma upsert failed: {error!r}") from error

    async def record_hit(self, template_id: str, now: datetime) -> None:
        collection = await self._templates()
        try:
            async with asyncio.timeout(self._timeout_seconds):
                stored = await collection.get(ids=[template_id], include=["metadatas"])
                if not stored["ids"]:
                    return  # removed meanwhile: nothing to count
                previous = (stored["metadatas"] or [{}])[0].get("hit_count")
                hit_count = (previous if isinstance(previous, int) else 0) + 1
                await collection.update(
                    ids=[template_id],
                    metadatas=[{"hit_count": hit_count, "last_used_at": now.isoformat()}],
                )
        except Exception as error:
            raise PlanStoreUnavailableError(f"Chroma update failed: {error!r}") from error

    async def _templates(self) -> AsyncCollection:
        if self._collection is None:
            try:
                async with asyncio.timeout(self._timeout_seconds):
                    self._collection = await self._client.get_or_create_collection(
                        self._collection_name,
                        configuration={"hnsw": {"space": "cosine"}},
                        embedding_function=None,  # embeddings come from Voyage
                    )
            except Exception as error:
                raise PlanStoreUnavailableError(f"Chroma is not usable: {error!r}") from error
        return self._collection


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
