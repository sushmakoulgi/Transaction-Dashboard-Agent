"""Text embeddings for the plan cache.

Stored templates and new questions are both short templates compared with each other, so
both are embedded the same way (`input_type="query"`): identical text then has similarity 1.
Voyage is called over its REST API with httpx, like every other HTTP client in the project.
"""

import asyncio
from typing import Protocol

import httpx
from pydantic import BaseModel, SecretStr, ValidationError

RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
VOYAGE_INPUT_TYPE = "query"


class EmbeddingProvider(Protocol):
    async def embed(self, text: str) -> list[float]: ...


class EmbeddingError(Exception):
    """The embedding request was rejected (bad key, bad model, malformed reply)."""


class EmbeddingUnavailableError(EmbeddingError):
    """The provider could not be reached, or stayed unavailable after every retry."""


class _VoyageEmbedding(BaseModel):
    embedding: list[float]
    index: int


class _VoyageEmbeddingsResponse(BaseModel):
    data: list[_VoyageEmbedding]


class VoyageEmbeddingProvider:
    def __init__(
        self,
        api_key: SecretStr,
        model: str,
        base_url: str = "https://api.voyageai.com/v1",
        timeout_seconds: float = 10.0,
        max_retries: int = 2,
        retry_backoff_seconds: float = 0.5,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._http_client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key.get_secret_value()}"},
            timeout=timeout_seconds,
            transport=transport,
        )
        self._model = model
        self._max_retries = max_retries
        self._retry_backoff_seconds = retry_backoff_seconds

    async def embed(self, text: str) -> list[float]:
        if not text.strip():
            raise ValueError("cannot embed empty text")
        body = {"input": [text], "model": self._model, "input_type": VOYAGE_INPUT_TYPE}
        last_failure = ""
        for attempt in range(self._max_retries + 1):
            if attempt > 0:
                await asyncio.sleep(self._retry_backoff_seconds * 2 ** (attempt - 1))
            try:
                response = await self._http_client.post("/embeddings", json=body)
            except httpx.TransportError as error:
                last_failure = f"{type(error).__name__}: {error}"
                continue
            if response.status_code in RETRYABLE_STATUS_CODES:
                last_failure = f"HTTP {response.status_code}"
                continue
            if response.is_error:
                raise EmbeddingError(f"Voyage rejected the request: HTTP {response.status_code}")
            return embedding_from(response)
        raise EmbeddingUnavailableError(
            f"Voyage unavailable after {self._max_retries + 1} attempts ({last_failure})"
        )

    async def aclose(self) -> None:
        await self._http_client.aclose()


def embedding_from(response: httpx.Response) -> list[float]:
    try:
        parsed = _VoyageEmbeddingsResponse.model_validate(response.json())
    except (ValueError, ValidationError) as error:
        raise EmbeddingError("Voyage returned an unexpected response") from error
    if len(parsed.data) != 1 or not parsed.data[0].embedding:
        raise EmbeddingError("Voyage returned no embedding")
    return parsed.data[0].embedding
