from __future__ import annotations

import base64
import hashlib
import hmac
import json

from celine.sdk.posture import is_dev

from celine.nudging.config.settings import settings

# Used only under CELINE_ENV=dev when CLICK_TRACKING_SECRET is unset, so a local run
# signs clicks without configuration. It is public, so it signs nothing that matters;
# outside dev the posture guard refuses to start without a real secret (REQ-0081).
DEV_CLICK_TRACKING_SECRET = "dev-only-click-tracking-secret"


def _urlsafe_b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _urlsafe_b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(f"{value}{padding}")


def _tracking_secret() -> str:
    """`CLICK_TRACKING_SECRET`, and nothing else outside dev (REQ-0056)."""
    secret = settings.CLICK_TRACKING_SECRET.strip()
    if secret:
        return secret
    if is_dev():
        return DEV_CLICK_TRACKING_SECRET
    raise RuntimeError("CLICK_TRACKING_SECRET must be configured for click tracking")


def sign_click_tracking_token(notification_id: str) -> str:
    payload = {"notification_id": notification_id}
    payload_raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    payload_b64 = _urlsafe_b64encode(payload_raw)
    signature = hmac.new(
        _tracking_secret().encode("utf-8"),
        payload_b64.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return f"{payload_b64}.{_urlsafe_b64encode(signature)}"


def unsign_click_tracking_token(token: str) -> str:
    try:
        payload_b64, signature_b64 = token.split(".", 1)
    except ValueError as exc:
        raise ValueError("invalid tracking token format") from exc

    expected_signature = hmac.new(
        _tracking_secret().encode("utf-8"),
        payload_b64.encode("ascii"),
        hashlib.sha256,
    ).digest()
    actual_signature = _urlsafe_b64decode(signature_b64)
    if not hmac.compare_digest(expected_signature, actual_signature):
        raise ValueError("invalid tracking token signature")

    payload = json.loads(_urlsafe_b64decode(payload_b64).decode("utf-8"))
    notification_id = payload.get("notification_id")
    if not notification_id or not isinstance(notification_id, str):
        raise ValueError("invalid tracking token payload")
    return notification_id
