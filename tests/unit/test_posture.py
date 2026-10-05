"""Deployment posture: only `CELINE_ENV=dev` accepts the development defaults.

`celine.sdk.posture` treats unset, empty, `staging`, `prod` or a typo as hardened.
The suite runs in dev (conftest), so every test here names its environment.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest
from celine.sdk.posture import InsecureConfiguration
from celine.sdk.settings.models import OidcSettings

from celine.nudging.config.settings import Settings, posture_guard

HARDENED = ["", "staging"]

DEV_DATABASE_URL = (
    "postgresql+asyncpg://postgres:securepassword123@host.docker.internal:15432/nudging"
)

SHIPPED_DEFAULTS = {
    "DATABASE_URL",
    "CELINE_OIDC_CLIENT_SECRET",
    "CELINE_OIDC_BASE_URL",
    "CELINE_OIDC_JWKS_URI",
    "VAPID_PUBLIC_KEY",
    "VAPID_PRIVATE_KEY",
    "CLICK_TRACKING_SECRET",
}


@pytest.fixture
def no_oidc_env(monkeypatch):
    for name in ("BASE_URL", "JWKS_URI", "AUDIENCE", "CLIENT_ID", "CLIENT_SECRET"):
        monkeypatch.delenv(f"CELINE_OIDC_{name}", raising=False)


def _shipped_defaults() -> Settings:
    """What a checkout runs with when nothing is configured."""
    return Settings(
        _env_file=None,
        DATABASE_URL=DEV_DATABASE_URL,
        oidc=OidcSettings(
            client_id="svc-nudging", client_secret="svc-nudging", audience="svc-nudging"
        ),
        VAPID_PUBLIC_KEY="",
        VAPID_PRIVATE_KEY="",
        CLICK_TRACKING_SECRET="",
    )


def _deployed() -> Settings:
    return Settings(
        _env_file=None,
        DATABASE_URL="postgresql+asyncpg://nudging:Zq81-generated@db.example.org:5432/nudging",
        oidc=OidcSettings(
            base_url="https://auth.example.org/realms/celine",
            jwks_uri="https://auth.example.org/realms/celine/protocol/openid-connect/certs",
            client_id="svc-nudging",
            client_secret="a-real-generated-secret",
            audience="svc-nudging",
        ),
        VAPID_PUBLIC_KEY="BPublicKeyFromTheDeployment",
        VAPID_PRIVATE_KEY="private-key-from-the-deployment",
        CLICK_TRACKING_SECRET="a-dedicated-click-secret",
    )


# @verifies REQ-0081
@pytest.mark.usefixtures("no_oidc_env")
@pytest.mark.parametrize("env", HARDENED)
def test_hardened_refuses_every_shipped_default_at_once(env):
    """@verifies REQ-0081

    One failure lists everything, so a deployment learns every missing value in one
    cycle rather than one restart at a time.
    """
    guard = posture_guard(_shipped_defaults(), env=env)

    assert {v.setting for v in guard.violations} == SHIPPED_DEFAULTS
    with pytest.raises(InsecureConfiguration) as raised:
        guard.enforce()
    for setting in SHIPPED_DEFAULTS:
        assert setting in str(raised.value)


# @verifies REQ-0081
@pytest.mark.parametrize("env", HARDENED)
@pytest.mark.parametrize(
    ("setting", "update"),
    [
        ("DATABASE_URL", {"DATABASE_URL": DEV_DATABASE_URL}),
        ("VAPID_PUBLIC_KEY", {"VAPID_PUBLIC_KEY": ""}),
        ("VAPID_PRIVATE_KEY", {"VAPID_PRIVATE_KEY": "  "}),
        ("CLICK_TRACKING_SECRET", {"CLICK_TRACKING_SECRET": ""}),
        ("WEBPUSH_ENDPOINT_RELAXED", {"WEBPUSH_ENDPOINT_RELAXED": True}),
    ],
)
def test_hardened_refuses_each_default_on_its_own(env, setting, update):
    """@verifies REQ-0081"""
    guard = posture_guard(_deployed().model_copy(update=update), env=env)

    assert [v.setting for v in guard.violations] == [setting]
    with pytest.raises(InsecureConfiguration, match=setting):
        guard.enforce()


# @verifies REQ-0081
@pytest.mark.parametrize("env", HARDENED)
def test_hardened_refuses_a_client_secret_equal_to_the_client_id(env):
    """@verifies REQ-0081"""
    deployed = _deployed()
    oidc = deployed.oidc.model_copy(update={"client_secret": "svc-nudging"})
    guard = posture_guard(deployed.model_copy(update={"oidc": oidc}), env=env)

    assert [v.setting for v in guard.violations] == ["CELINE_OIDC_CLIENT_SECRET"]


# @verifies REQ-0081
@pytest.mark.usefixtures("no_oidc_env")
@pytest.mark.parametrize("env", HARDENED)
def test_hardened_refuses_the_sdk_default_issuer(env):
    """@verifies REQ-0081"""
    deployed = _deployed()
    oidc = OidcSettings(**deployed.oidc.model_dump(exclude={"base_url", "jwks_uri"}))
    guard = posture_guard(deployed.model_copy(update={"oidc": oidc}), env=env)

    assert [v.setting for v in guard.violations] == [
        "CELINE_OIDC_BASE_URL",
        "CELINE_OIDC_JWKS_URI",
    ]


# @verifies REQ-0081
@pytest.mark.parametrize("env", HARDENED)
def test_hardened_starts_with_a_real_configuration(env):
    """@verifies REQ-0081"""
    guard = posture_guard(_deployed(), env=env)

    assert guard.violations == []
    guard.enforce()


# @verifies REQ-0081
@pytest.mark.usefixtures("no_oidc_env")
def test_dev_starts_with_the_shipped_defaults(caplog):
    """@verifies REQ-0081"""
    guard = posture_guard(_shipped_defaults(), env="dev")

    guard.enforce()

    assert "development setting(s) in use" in caplog.text


# @verifies REQ-0081
@pytest.mark.parametrize("env", HARDENED)
def test_create_app_refuses_before_the_lifespan_runs(monkeypatch, env):
    """@verifies REQ-0081

    The suite's own settings carry a weak database password, so the factory itself —
    not the first request, not the lifespan — is what refuses.
    """
    from celine.nudging import main as main_module

    monkeypatch.setattr(main_module, "load_dotenv", lambda: None)
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    if env:
        monkeypatch.setenv("CELINE_ENV", env)
    else:
        monkeypatch.delenv("CELINE_ENV", raising=False)

    with pytest.raises(InsecureConfiguration, match="DATABASE_URL"):
        main_module.create_app()


# @verifies REQ-0081
def test_the_client_secret_can_be_set_from_the_environment():
    """@verifies REQ-0081

    A keyword argument beats the environment in pydantic-settings, so a literal secret
    in `Settings` would make `CELINE_OIDC_CLIENT_SECRET` impossible to set. Checked in a
    fresh interpreter because `settings` is built at import.
    """
    env = {**os.environ, "CELINE_OIDC_CLIENT_SECRET": "from-the-environment"}
    out = subprocess.run(
        [
            sys.executable,
            "-c",
            "from celine.nudging.config.settings import settings;"
            "print(settings.oidc.client_id, settings.oidc.client_secret)",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )

    assert out.stdout.split() == ["svc-nudging", "from-the-environment"]
