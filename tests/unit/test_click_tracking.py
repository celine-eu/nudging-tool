"""The signed token that lets a service worker report a click without a session.

`/notifications/track-click` is the one route outside the middleware's protection, so
this signature is the whole of its access control: anything the verifier accepts can
mark any notification as clicked.
"""

from __future__ import annotations

import base64
import json

import pytest

from celine.nudging import notifications_tracking as tracking
from celine.nudging.notifications_tracking import (
    sign_click_tracking_token,
    unsign_click_tracking_token,
)


# @verifies REQ-0056
def test_a_token_names_the_notification_it_was_minted_for():
    token = sign_click_tracking_token("notification-1")

    assert unsign_click_tracking_token(token) == "notification-1"


# @verifies REQ-0056
def test_the_token_is_two_url_safe_parts_and_carries_no_padding():
    """
    It travels inside a JSON push payload and is handed back in a request body, so the
    shape matters: `<payload>.<signature>`, both base64url with `=` stripped. The payload
    is **not encrypted** — a notification id is readable by anyone holding the token,
    which is fine because holding it already implies having received that notification.
    """
    payload_b64, signature_b64 = sign_click_tracking_token("notification-1").split(".")

    assert "=" not in payload_b64 and "=" not in signature_b64
    assert set("+/").isdisjoint(payload_b64 + signature_b64)

    decoded = base64.urlsafe_b64decode(payload_b64 + "=" * (-len(payload_b64) % 4))
    assert json.loads(decoded) == {"notification_id": "notification-1"}


# @verifies REQ-0056
def test_signing_is_deterministic_so_a_retry_is_the_same_token():
    assert sign_click_tracking_token("n1") == sign_click_tracking_token("n1")
    assert sign_click_tracking_token("n1") != sign_click_tracking_token("n2")


# @verifies REQ-0056
def test_a_token_signed_with_another_secret_is_rejected(monkeypatch):
    """
    This is the property the endpoint depends on. Without it, a caller could mint a
    token for any notification id and mark another participant's notifications as
    clicked — the endpoint checks nothing else.
    """
    token = sign_click_tracking_token("notification-1")

    monkeypatch.setattr(tracking.settings, "CLICK_TRACKING_SECRET", "a-different-secret")

    with pytest.raises(ValueError, match="invalid tracking token signature"):
        unsign_click_tracking_token(token)


# @verifies REQ-0056
def test_a_tampered_payload_is_rejected():
    _, signature = sign_click_tracking_token("notification-1").split(".")
    forged_payload = (
        base64.urlsafe_b64encode(b'{"notification_id":"notification-2"}')
        .decode()
        .rstrip("=")
    )

    with pytest.raises(ValueError, match="signature"):
        unsign_click_tracking_token(f"{forged_payload}.{signature}")


@pytest.mark.parametrize("token", ["", "no-dot", "...", "a.b"])
# @verifies REQ-0056
def test_a_malformed_token_is_a_value_error_not_a_crash(token):
    """
    The endpoint turns `ValueError` into a `400`. Anything else — an `IndexError`, a
    `binascii` error — would be a `500` on a public route, which is a difference an
    unauthenticated caller can see.
    """
    with pytest.raises(ValueError):
        unsign_click_tracking_token(token)


# @verifies REQ-0056
def test_a_token_whose_payload_names_no_notification_is_rejected():
    payload = base64.urlsafe_b64encode(b'{"notification_id": null}').decode().rstrip("=")
    import hashlib
    import hmac

    signature = hmac.new(
        tracking._tracking_secret().encode(), payload.encode(), hashlib.sha256
    ).digest()
    signed = base64.urlsafe_b64encode(signature).decode().rstrip("=")

    with pytest.raises(ValueError, match="payload"):
        unsign_click_tracking_token(f"{payload}.{signed}")


# @verifies REQ-0056
@pytest.mark.parametrize("env", ["", "staging", "prod"])
def test_outside_dev_only_the_click_tracking_secret_signs(monkeypatch, env):
    """
    The VAPID private key is never the signing secret. Outside dev an unset
    `CLICK_TRACKING_SECRET` refuses to sign — the posture guard already refuses to start
    without it (REQ-0081), so this is the second line, for a code path that skipped it.
    """
    monkeypatch.setenv("CELINE_ENV", env)
    monkeypatch.setattr(tracking.settings, "CLICK_TRACKING_SECRET", "  ")
    monkeypatch.setattr(tracking.settings, "VAPID_PRIVATE_KEY", "a-configured-vapid-key")

    with pytest.raises(RuntimeError, match="CLICK_TRACKING_SECRET must be configured"):
        sign_click_tracking_token("notification-1")


# @verifies REQ-0056
def test_in_dev_an_unset_secret_signs_with_the_development_secret(monkeypatch):
    """
    A local run works without configuration, with a fixed development secret — not the
    VAPID key, so rotating that key leaves tokens in flight valid.
    """
    monkeypatch.setenv("CELINE_ENV", "dev")
    monkeypatch.setattr(tracking.settings, "CLICK_TRACKING_SECRET", "")

    token = sign_click_tracking_token("notification-1")
    assert tracking._tracking_secret() == tracking.DEV_CLICK_TRACKING_SECRET

    monkeypatch.setattr(tracking.settings, "VAPID_PRIVATE_KEY", "a-rotated-key")
    assert unsign_click_tracking_token(token) == "notification-1"


# @verifies REQ-0056
def test_rotating_the_vapid_key_leaves_tokens_valid(monkeypatch):
    token = sign_click_tracking_token("notification-1")

    monkeypatch.setattr(tracking.settings, "VAPID_PRIVATE_KEY", "a-rotated-key")

    assert unsign_click_tracking_token(token) == "notification-1"
