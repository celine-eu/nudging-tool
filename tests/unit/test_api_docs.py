"""The interactive API docs and the schema are mounted only in development.

`celine.sdk.posture.docs_urls`: in `CELINE_ENV=dev` `/docs`, `/redoc` and
`/openapi.json` are served; anywhere else — unset included — they are not mounted unless
`CELINE_PUBLIC_DOCS=true`. They stay on the open list, so an unmounted path is a plain
`404`, not a `401`. The posture guard is stubbed: it is `test_posture.py`'s subject, and
outside dev it would refuse the suite's development defaults.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from celine.nudging import main as main_module

PATHS = ("/docs", "/redoc", "/openapi.json")


class _NoGuard:
    def enforce(self) -> None:
        pass


def _client(monkeypatch, env: str | None, public: str | None = None) -> TestClient:
    # `create_app` loads `.env`; a checkout's own file must not decide the outcome.
    monkeypatch.setattr(main_module, "load_dotenv", lambda *a, **k: False)
    monkeypatch.setattr(main_module, "posture_guard", lambda _s: _NoGuard())
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    if env is None:
        monkeypatch.delenv("CELINE_ENV", raising=False)
    else:
        monkeypatch.setenv("CELINE_ENV", env)
    if public is None:
        monkeypatch.delenv("CELINE_PUBLIC_DOCS", raising=False)
    else:
        monkeypatch.setenv("CELINE_PUBLIC_DOCS", public)
    # No `with`: the lifespan (policy bundle, seed, scheduler) is not under test here.
    return TestClient(main_module.create_app())


@pytest.mark.parametrize("env", [None, "staging", "prod"])
# @verifies REQ-0001
def test_outside_dev_the_docs_are_not_mounted(monkeypatch, env):
    client = _client(monkeypatch, env)

    assert [client.get(p).status_code for p in PATHS] == [404, 404, 404]


@pytest.mark.parametrize("public", ["", "false", "0"])
# @verifies REQ-0001
def test_only_a_true_opt_in_serves_them_outside_dev(monkeypatch, public):
    client = _client(monkeypatch, "staging", public)

    assert [client.get(p).status_code for p in PATHS] == [404, 404, 404]


# @verifies REQ-0001
def test_the_public_docs_opt_in_serves_them_outside_dev(monkeypatch):
    client = _client(monkeypatch, "staging", "true")

    assert [client.get(p).status_code for p in PATHS] == [200, 200, 200]


# @verifies REQ-0001
def test_dev_serves_the_docs(monkeypatch):
    client = _client(monkeypatch, "dev")

    assert [client.get(p).status_code for p in PATHS] == [200, 200, 200]
    schema = client.get("/openapi.json").json()
    assert schema["info"]["title"] == "nudging-tool-api"
