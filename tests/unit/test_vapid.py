"""The VAPID key, in the shape `pywebpush` can actually sign with.

Staging never delivered a single Web Push (12 of 12 attempts, May → July 2026:
`Could not deserialize key data`). The key was a PEM, `pywebpush.webpush()` was handed
it as a string, and a string to `pywebpush` means *base64url of a raw or DER key* —
`py_vapid.Vapid.from_string` strips newlines, base64-decodes, and never looks for
`-----BEGIN`. A PEM can only be passed as a `py_vapid.Vapid` instance.

The Helm value additionally carries the PEM's line breaks as the two characters `\\n`,
which `get_vapid` already normalises; that is pinned here too so the two fixes cannot
drift apart.
"""

from __future__ import annotations

import py_vapid
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from celine.nudging.config import settings as settings_module
from celine.nudging.utils import get_vapid


@pytest.fixture
def pem_key() -> tuple[str, ec.EllipticCurvePrivateKey]:
    private = ec.generate_private_key(ec.SECP256R1())
    pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ).decode()
    return pem, private


def _public_point(key: ec.EllipticCurvePrivateKey) -> bytes:
    return key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )


# @verifies REQ-0047
def test_a_pem_key_is_handed_to_pywebpush_as_a_vapid_instance(monkeypatch, pem_key):
    pem, private = pem_key
    monkeypatch.setattr(settings_module.settings, "VAPID_PRIVATE_KEY", pem)

    signing_key = get_vapid().signing_key

    assert isinstance(signing_key, py_vapid.Vapid)
    assert _public_point(signing_key.private_key) == _public_point(private)


# @verifies REQ-0047
def test_a_pem_with_literal_backslash_n_line_breaks_still_loads(monkeypatch, pem_key):
    """
    What the Helm release actually passes: one line, `\\n` spelled out. `od -c` on the
    live Deployment showed `-----BEGIN EC PRIVATE KEY-----\\nMHcCAQEE…`.
    """
    pem, private = pem_key
    monkeypatch.setattr(
        settings_module.settings, "VAPID_PRIVATE_KEY", pem.replace("\n", "\\n")
    )

    signing_key = get_vapid().signing_key

    assert isinstance(signing_key, py_vapid.Vapid)
    assert _public_point(signing_key.private_key) == _public_point(private)


# @verifies REQ-0047
def test_a_raw_base64url_key_is_passed_through_as_a_string(monkeypatch):
    """
    The other format `pywebpush` accepts — the 43-character base64url private scalar
    that `vapid --gen` and the web-push tutorials produce — is left to `pywebpush`, which
    already knows how to read it.
    """
    raw = "x" * 43
    monkeypatch.setattr(settings_module.settings, "VAPID_PRIVATE_KEY", raw)

    assert get_vapid().signing_key == raw
