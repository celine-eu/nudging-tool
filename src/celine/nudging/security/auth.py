"""JWT authentication middleware.

Validates Bearer tokens on every request except OpenAPI/docs routes.
Attaches the resolved JwtUser to request.state.user.
"""

from __future__ import annotations

import logging
from typing import Awaitable, Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from celine.sdk.auth import JwtUser
from celine.nudging.config.settings import settings

logger = logging.getLogger(__name__)

# Routes that don't require a token (FastAPI / OpenAPI meta)
_OPEN_PATHS: frozenset[str] = frozenset(
    {
        "/docs",
        "/redoc",
        "/openapi.json",
        "/favicon.ico",
    }
)


def _is_open(path: str) -> bool:
    """Return True if the path should bypass auth."""
    if path in _OPEN_PATHS:
        return True
    # static assets
    if path.startswith("/static/"):
        return True
    return False


class AuthMiddleware(BaseHTTPMiddleware):
    """Starlette middleware that validates JWT on every protected route."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if _is_open(request.url.path):
            return await call_next(request)

        auth_header = request.headers.get("Authorization", "")
        if not auth_header:
            return JSONResponse(
                status_code=401,
                content={"detail": "Missing Authorization header"},
                headers={"WWW-Authenticate": "Bearer"},
            )

        try:
            user = JwtUser.from_token(auth_header, settings.oidc)
        except Exception as exc:
            logger.warning("JWT validation failed: %s", exc)
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid or expired token"},
                headers={"WWW-Authenticate": "Bearer"},
            )

        request.state.user = user
        return await call_next(request)
