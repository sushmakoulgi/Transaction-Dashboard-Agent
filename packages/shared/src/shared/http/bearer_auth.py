"""Static bearer-token authentication for service-to-service calls.

`require_bearer_token` protects FastAPI routes; `BearerTokenMiddleware` protects any ASGI app
(such as the MCP server). Both compare tokens in constant time.
"""

import hmac
from collections.abc import Callable, Collection

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import SecretStr
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from shared.contracts import ErrorResponse

bearer_scheme = HTTPBearer(auto_error=False)
UNAUTHORIZED_MESSAGE = "missing or invalid bearer token"


def tokens_match(presented_token: str | None, expected_token: SecretStr) -> bool:
    presented = (presented_token or "").encode()
    return hmac.compare_digest(presented, expected_token.get_secret_value().encode())


def bearer_token_from_header(authorization_header: str | None) -> str | None:
    scheme, _, token = (authorization_header or "").partition(" ")
    return token.strip() if scheme.lower() == "bearer" and token.strip() else None


def require_bearer_token(expected_token: SecretStr) -> Callable[..., None]:
    """A FastAPI dependency rejecting requests without exactly this token (401)."""

    def verify_bearer_token(
        credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),  # noqa: B008
    ) -> None:
        presented = credentials.credentials if credentials is not None else None
        if not tokens_match(presented, expected_token):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=UNAUTHORIZED_MESSAGE,
                headers={"WWW-Authenticate": "Bearer"},
            )

    return verify_bearer_token


class BearerTokenMiddleware:
    """ASGI middleware: every HTTP request outside `exempt_paths` needs the bearer token."""

    def __init__(
        self, app: ASGIApp, expected_token: SecretStr, exempt_paths: Collection[str] = ()
    ) -> None:
        self._app = app
        self._expected_token = expected_token
        self._exempt_paths = frozenset(exempt_paths)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] in self._exempt_paths:
            await self._app(scope, receive, send)
            return
        headers = {name.decode().lower(): value.decode() for name, value in scope["headers"]}
        presented = bearer_token_from_header(headers.get("authorization"))
        if tokens_match(presented, self._expected_token):
            await self._app(scope, receive, send)
            return
        response = JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content=ErrorResponse(error="UNAUTHORIZED", message=UNAUTHORIZED_MESSAGE).model_dump(
                mode="json"
            ),
            headers={"WWW-Authenticate": "Bearer"},
        )
        await response(scope, receive, send)
