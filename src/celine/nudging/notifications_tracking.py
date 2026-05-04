from __future__ import annotations

import base64
import hashlib
import hmac
import json

from celine.nudging.config.settings import settings


def _urlsafe_b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _urlsafe_b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(f"{value}{padding}")


def _tracking_secret() -> str:
    secret = settings.CLICK_TRACKING_SECRET.strip() or settings.VAPID_PRIVATE_KEY.strip()
    if not secret:
        raise RuntimeError(
            "CLICK_TRACKING_SECRET or VAPID_PRIVATE_KEY must be configured for click tracking"
        )
    return secret


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
