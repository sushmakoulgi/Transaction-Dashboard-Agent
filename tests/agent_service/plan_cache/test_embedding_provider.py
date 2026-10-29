"""Unit tests for VoyageEmbeddingProvider, using httpx's mock transport (no real Voyage calls)."""

import json

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from agent_service.plan_cache.embedding_provider import (
    EmbeddingError,
    EmbeddingUnavailableError,
    VoyageEmbeddingProvider,
)
from agent_service.settings import PlanCacheSettings

API_KEY = SecretStr("voyage-test-key")
EMBEDDING = [0.12, -0.5, 0.33]


def embeddings_body(*vectors: list[float]) -> dict[str, object]:
    return {
        "object": "list",
        "data": [
            {"object": "embedding", "embedding": vector, "index": n}
            for n, vector in enumerate(vectors)
        ],
        "model": "voyage-4-lite",
        "usage": {"total_tokens": 7},
    }


class RecordingTransport(httpx.MockTransport):
    """Answers each request with the next queued response and remembers every request."""

    def __init__(self, *responses: httpx.Response | Exception) -> None:
        self.requests: list[httpx.Request] = []
        queued = list(responses)

        def respond(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            next_response = queued.pop(0) if len(queued) > 1 else queued[0]
            if isinstance(next_response, Exception):
                raise next_response
            return next_response

        super().__init__(respond)


def provider_with(transport: RecordingTransport) -> VoyageEmbeddingProvider:
    return VoyageEmbeddingProvider(
        API_KEY,
        "voyage-4-lite",
        base_url="https://voyage.test/v1/",
        max_retries=2,
        retry_backoff_seconds=0,
        transport=transport,
    )


async def test_embeds_one_text_as_a_query() -> None:
    transport = RecordingTransport(httpx.Response(200, json=embeddings_body(EMBEDDING)))
    provider = provider_with(transport)

    assert await provider.embed("chargebacks opened this month") == EMBEDDING

    (request,) = transport.requests
    assert str(request.url) == "https://voyage.test/v1/embeddings"
    assert request.headers["Authorization"] == "Bearer voyage-test-key"
    assert json.loads(request.content) == {
        "input": ["chargebacks opened this month"],
        "model": "voyage-4-lite",
        "input_type": "query",  # the same for stored templates and new questions
    }
    await provider.aclose()


async def test_retries_rate_limits_server_errors_and_network_failures() -> None:
    transport = RecordingTransport(
        httpx.Response(429, json={"detail": "rate limited"}),
        httpx.ConnectError("connection refused"),
        httpx.Response(200, json=embeddings_body(EMBEDDING)),
    )
    assert await provider_with(transport).embed("x") == EMBEDDING
    assert len(transport.requests) == 3


async def test_gives_up_after_the_last_retry() -> None:
    transport = RecordingTransport(httpx.Response(503))
    with pytest.raises(EmbeddingUnavailableError, match="after 3 attempts"):
        await provider_with(transport).embed("x")
    assert len(transport.requests) == 3


async def test_client_errors_are_not_retried_and_never_echo_the_key() -> None:
    transport = RecordingTransport(httpx.Response(401, json={"detail": "invalid key"}))
    with pytest.raises(EmbeddingError) as error:
        await provider_with(transport).embed("x")
    assert not isinstance(error.value, EmbeddingUnavailableError)
    assert "HTTP 401" in str(error.value)
    assert "voyage-test-key" not in str(error.value)
    assert len(transport.requests) == 1


@pytest.mark.parametrize(
    "body",
    [
        {"unexpected": True},
        embeddings_body(),  # no embedding
        embeddings_body([]),  # empty vector
        embeddings_body(EMBEDDING, EMBEDDING),  # more than asked for
    ],
)
async def test_unexpected_replies_are_errors(body: dict[str, object]) -> None:
    transport = RecordingTransport(httpx.Response(200, json=body))
    with pytest.raises(EmbeddingError):
        await provider_with(transport).embed("x")


async def test_empty_text_is_rejected_without_a_request() -> None:
    transport = RecordingTransport(httpx.Response(200, json=embeddings_body(EMBEDDING)))
    with pytest.raises(ValueError, match="empty"):
        await provider_with(transport).embed("   ")
    assert transport.requests == []


def test_plan_cache_is_off_by_default_and_needs_a_key_when_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("PLAN_CACHE_ENABLED", "VOYAGE_API_KEY", "PLAN_HIT_THRESHOLD"):
        monkeypatch.delenv(name, raising=False)
    defaults = PlanCacheSettings(_env_file=None)  # type: ignore[call-arg]
    assert defaults.plan_cache_enabled is False
    assert defaults.plan_hit_threshold == 0.90
    assert defaults.embedding_model == "voyage-4-lite"

    with pytest.raises(ValidationError, match="VOYAGE_API_KEY is required"):
        PlanCacheSettings(_env_file=None, plan_cache_enabled=True, voyage_api_key="  ")  # type: ignore[call-arg]

    enabled = PlanCacheSettings(_env_file=None, plan_cache_enabled=True, voyage_api_key="k")  # type: ignore[call-arg]
    assert enabled.voyage_api_key is not None
