"""One lazily opened connection to Chroma, shared by the plan store and the metrics.

Nothing connects at startup: the first call opens the client, and a failed attempt is
retried on the next call, so the agent never waits for Chroma. The Chroma client sets no
request timeout of its own, so callers run each call under `run`, which applies
`timeout_seconds` and turns every failure into `PlanStoreUnavailableError`.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

import chromadb
from chromadb.api.async_api import AsyncClientAPI
from chromadb.api.models.AsyncCollection import AsyncCollection
from chromadb.config import Settings as ChromaSettings
from pydantic import AnyHttpUrl

T = TypeVar("T")


class PlanStoreUnavailableError(Exception):
    """Chroma could not be reached or answered unexpectedly."""


class ChromaConnection:
    def __init__(self, url: AnyHttpUrl, timeout_seconds: float = 5.0) -> None:
        self._url = url
        self._timeout_seconds = timeout_seconds
        self._client: AsyncClientAPI | None = None
        self._collections: dict[str, AsyncCollection] = {}

    async def run(self, operation: Callable[[], Awaitable[T]], action: str) -> T:
        """Runs one Chroma call under the timeout; any failure is PlanStoreUnavailableError."""
        try:
            async with asyncio.timeout(self._timeout_seconds):
                return await operation()
        except PlanStoreUnavailableError:
            raise
        except Exception as error:  # the client raises many types; all mean "not usable"
            raise PlanStoreUnavailableError(f"Chroma {action} failed: {error!r}") from error

    async def client(self) -> AsyncClientAPI:
        if self._client is None:
            self._client = await self.run(
                lambda: chromadb.AsyncHttpClient(
                    host=self._url.host or "localhost",
                    port=self._url.port or 8000,
                    ssl=self._url.scheme == "https",
                    settings=ChromaSettings(anonymized_telemetry=False),
                ),
                "connect",
            )
        return self._client

    async def collection(self, name: str) -> AsyncCollection:
        """The named collection (cosine distance), created on first use."""
        if name not in self._collections:
            client = await self.client()
            self._collections[name] = await self.run(
                lambda: client.get_or_create_collection(
                    name,
                    configuration={"hnsw": {"space": "cosine"}},
                    embedding_function=None,  # embeddings come from Voyage
                ),
                f"open collection {name}",
            )
        return self._collections[name]

    async def delete_collection(self, name: str) -> None:
        client = await self.client()
        await self.run(lambda: client.delete_collection(name), f"delete collection {name}")
        self._collections.pop(name, None)
