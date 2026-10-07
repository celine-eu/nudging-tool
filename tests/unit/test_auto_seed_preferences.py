"""Startup seeds the seeded preferences only under CELINE_ENV=dev (REQ-0085).

The repository's `seed/` ships with the image and `SEED_DIR` defaults to it, so a
deployment seeds whatever it holds on every start. Its preferences are development
fixtures; its rules and templates are the catalogue.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from celine.nudging.db import auto_seed as auto_seed_module

SEED = Path(__file__).resolve().parents[2] / "seed"


class _Session:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def commit(self):
        pass


@pytest.fixture
def written(monkeypatch):
    calls: dict[str, int] = {"rules": 0, "templates": 0, "preferences": 0}

    def _count(kind):
        async def _upsert(db, item):
            calls[kind] += 1

        return _upsert

    monkeypatch.setattr(auto_seed_module.settings, "SEED_DIR", str(SEED))
    monkeypatch.setattr(auto_seed_module, "AsyncSessionLocal", _Session)
    monkeypatch.setattr(auto_seed_module, "upsert_rule", _count("rules"))
    monkeypatch.setattr(auto_seed_module, "upsert_template", _count("templates"))
    monkeypatch.setattr(auto_seed_module, "upsert_preference", _count("preferences"))
    return calls


# @verifies REQ-0085
@pytest.mark.parametrize("env", ["", "staging", "prod"])
async def test_outside_dev_the_seeded_preferences_are_not_written(monkeypatch, written, env):
    monkeypatch.setenv("CELINE_ENV", env)
    monkeypatch.delenv("ENVIRONMENT", raising=False)

    await auto_seed_module.auto_seed()

    assert written["preferences"] == 0
    assert written["rules"] > 0 and written["templates"] > 0


# @verifies REQ-0085
async def test_under_dev_the_seeded_preferences_are_written(monkeypatch, written):
    monkeypatch.setenv("CELINE_ENV", "dev")

    await auto_seed_module.auto_seed()

    assert written["preferences"] > 0
    assert written["rules"] > 0 and written["templates"] > 0
