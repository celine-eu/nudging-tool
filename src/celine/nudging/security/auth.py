"""JWT authentication middleware.

Validates Bearer tokens on every request except OpenAPI/docs routes.
Attaches the resolved JwtUser to request.state.user.
"""

from __future__ import annotations

import logging
from typing import Awaitable, Callable

import jwt as pyjwt
from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from celine.sdk.auth import JwtUser
from celine.nudging.config.settings import settings

logger = logging.getLogger(__name__)

# Routes that don't require a token (FastAPI / OpenAPI meta)
_OPEN_PATHS: frozenset[str] = frozenset(
    {
        "/health",
        "/docs",
        "/redoc",
        "/openapi.json",
        "/favicon.ico",
        "/notifications/track-click",
    }
)


_DESCRIBED_CLAIMS = ("aud", "azp", "typ", "sub", "exp")


def describe_token(auth_header: str) -> str:
    """The claims that explain a rejection, and nothing that could replay it.

    Decoded *without* verification — the point is to say which audience or token type
    was presented, not to trust it. The credential itself never reaches the log: a
    bearer token in Loki is readable by everyone with Grafana access for the whole
    retention window, which is what happened in staging in September 2026.
    """
    token = auth_header.split(" ", 1)[1] if " " in auth_header else auth_header
    try:
        claims = pyjwt.decode(token, options={"verify_signature": False})
    except Exception:
        return "unparseable token"
    return " ".join(f"{name}={claims.get(name)}" for name in _DESCRIBED_CLAIMS)


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
            logger.warning(
                "JWT validation failed: %s (%s)", exc, describe_token(auth_header)
            )
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid or expired token"},
                headers={"WWW-Authenticate": "Bearer"},
            )

        request.state.user = user
        return await call_next(request)
