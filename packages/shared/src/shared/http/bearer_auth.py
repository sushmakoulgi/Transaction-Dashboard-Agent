"""Static bearer-token authentication for service-to-service calls."""

import hmac
from collections.abc import Callable

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import SecretStr

bearer_scheme = HTTPBearer(auto_error=False)


def require_bearer_token(expected_token: SecretStr) -> Callable[..., None]:
    """A FastAPI dependency rejecting requests without exactly this token (401)."""
    expected = expected_token.get_secret_value().encode()

    def verify_bearer_token(
        credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),  # noqa: B008
    ) -> None:
        presented = credentials.credentials.encode() if credentials is not None else b""
        if not hmac.compare_digest(presented, expected):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="missing or invalid bearer token",
                headers={"WWW-Authenticate": "Bearer"},
            )

    return verify_bearer_token
