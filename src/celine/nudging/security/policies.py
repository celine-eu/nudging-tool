"""FastAPI authorization dependencies.

Provides:
  - get_current_user  : injects the JwtUser from request state (set by AuthMiddleware)
  - require_admin     : raises 403 unless the policy engine grants is_admin
  - policy_engine     : module-level singleton, initialized at lifespan startup
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import Depends, HTTPException, Request, status

from celine.sdk.auth import JwtUser
from celine.sdk.policies import (
    Action,
    CachedPolicyEngine,
    DecisionCache,
    PolicyEngine,
    PolicyInput,
    Resource,
    ResourceType,
    Subject,
    SubjectType,
)
from celine.nudging.config.settings import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Singleton policy engine – initialised once in lifespan
# ---------------------------------------------------------------------------
_engine: CachedPolicyEngine | None = None
_POLICY_PACKAGE = "celine.nudging.authz"


def init_policy_engine() -> None:
    """Load policies from disk. Call once at application startup."""
    global _engine
    policies_dir = settings.policies.policies_data_dir
    if policies_dir is None:
        raise ValueError("POLICIES_DIR not set")

    if not Path(policies_dir).exists():
        raise ValueError(f"POLICIES_DIR '{policies_dir}' does not exists")

    base = PolicyEngine(policies_dir=policies_dir)
    base.load()
    _engine = CachedPolicyEngine(
        engine=base,
        cache=DecisionCache(maxsize=10_000, ttl_seconds=300),
    )
    logger.info("Policy engine ready – packages: %s", _engine.get_packages())


def get_policy_engine() -> CachedPolicyEngine:
    if _engine is None:
        raise RuntimeError(
            "Policy engine not initialised (call init_policy_engine() at startup)"
        )
    return _engine


# ---------------------------------------------------------------------------
# Helpers – build PolicyInput from a JwtUser
# ---------------------------------------------------------------------------


def _scopes_from_user(user: JwtUser) -> list[str]:
    scope_str: Any = user.claims.get("scope", "")
    if isinstance(scope_str, str):
        return scope_str.split()
    if isinstance(scope_str, list):
        return scope_str
    return []


def _groups_from_user(user: JwtUser) -> list[str]:
    groups: Any = user.claims.get("groups", [])
    if isinstance(groups, list):
        return [str(g) for g in groups]
    return []


def _subject_from_user(user: JwtUser) -> Subject:
    stype = SubjectType.SERVICE if user.is_service_account else SubjectType.USER
    return Subject(
        id=user.sub,
        type=stype,
        groups=_groups_from_user(user),
        scopes=_scopes_from_user(user),
        claims=user.claims,
    )


def _make_policy_input(user: JwtUser, action: str = "access") -> PolicyInput:
    return PolicyInput(
        subject=_subject_from_user(user),
        resource=Resource(type=ResourceType.USERDATA, id="nudging"),
        action=Action(name=action),
    )


# ---------------------------------------------------------------------------
# FastAPI dependencies
# ---------------------------------------------------------------------------


def get_current_user(request: Request) -> JwtUser:
    """Inject the JwtUser attached by AuthMiddleware. Always present on protected routes."""
    user: JwtUser | None = getattr(request.state, "user", None)
    if user is None:
        # Should not happen if middleware is wired correctly, but guard anyway
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def require_admin(
    user: JwtUser = Depends(get_current_user),
    engine: CachedPolicyEngine = Depends(get_policy_engine),
) -> JwtUser:
    """Raise 403 unless the unified policy grants is_admin for this subject."""
    policy_input = _make_policy_input(user, action="admin")
    input_dict = engine._build_input_dict(policy_input)
    raw = engine.evaluate(f"data.{_POLICY_PACKAGE}.is_admin", input_dict)
    if not _extract_bool(raw):
        logger.warning("Admin access denied for subject=%s", user.sub)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient permissions: nudging.admin scope or admin group required",
        )
    return user


def _extract_bool(result: Any, default: bool = False) -> bool:
    try:
        if "result" in result and result["result"]:
            expr = result["result"][0].get("expressions", [])
            if expr:
                val = expr[0].get("value")
                if isinstance(val, bool):
                    return val
    except Exception:
        pass
    return default
