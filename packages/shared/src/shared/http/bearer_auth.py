"""Static bearer-token authentication for service-to-service calls.

`require_bearer_token` protects FastAPI routes; `BearerTokenMiddleware` protects any ASGI app
(such as the MCP server). Both compare tokens in constant time. `bearer_token_dependencies`
and `protect_with_bearer_token` apply them only while AUTH_ENABLED is true.
"""

import hmac
import logging
from collections.abc import Callable, Collection

from fastapi import Depends, HTTPException, status
from fastapi.params import Depends as DependsParam
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import SecretStr
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from shared.contracts import ErrorResponse

logger = logging.getLogger(__name__)

bearer_scheme = HTTPBearer(auto_error=False)
UNAUTHORIZED_MESSAGE = "missing or invalid bearer token"
AUTH_DISABLED_WARNING = "AUTH_ENABLED is false: %s accepts requests without a bearer token"


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


def bearer_token_dependencies(
    auth_enabled: bool, expected_token: SecretStr | None, service_name: str
) -> list[DependsParam]:
    """Route dependencies for FastAPI: the token check, or nothing when auth is disabled."""
    if not auth_enabled:
        logger.warning(AUTH_DISABLED_WARNING, service_name)
        return []
    if expected_token is None:
        raise ValueError(f"{service_name}: a bearer token is required while auth is enabled")
    return [Depends(require_bearer_token(expected_token))]


def protect_with_bearer_token(
    app: ASGIApp,
    auth_enabled: bool,
    expected_token: SecretStr | None,
    service_name: str,
    exempt_paths: Collection[str] = (),
) -> ASGIApp:
    """Wraps an ASGI app in `BearerTokenMiddleware`, unless auth is disabled."""
    if not auth_enabled:
        logger.warning(AUTH_DISABLED_WARNING, service_name)
        return app
    if expected_token is None:
        raise ValueError(f"{service_name}: a bearer token is required while auth is enabled")
    return BearerTokenMiddleware(app, expected_token, exempt_paths)
