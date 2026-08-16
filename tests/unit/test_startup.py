"""What has to happen before the service will answer, and what it does on the way down.

The lifespan does three things in a fixed order, two of which can stop the process. It is
worth pinning because the failure it prevents — a service that answers `200` while
delivering nothing — is the shape of every other problem in this repository.
"""

from __future__ import annotations

import asyncio

import pytest
from asgi_lifespan import LifespanManager

from celine.nudging.main import create_app


# @verifies REQ-0050
async def test_startup_loads_the_policy_bundle_seeds_and_starts_the_scheduler(monkeypatch):
    """
    In that order. The policy engine first, because everything else is behind it; the
    seed next, so a rule referenced by an event that arrives immediately is already
    there; the scheduler last, as a background task the shutdown waits for.
    """
    from celine.nudging import main as main_module

    order: list[str] = []
    stop_events: list[asyncio.Event] = []

    async def _seed() -> None:
        order.append("seed")

    async def _scheduler(stop_event: asyncio.Event) -> None:
        order.append("scheduler")
        stop_events.append(stop_event)
        await stop_event.wait()
        order.append("scheduler-stopped")

    monkeypatch.setattr(
        main_module, "init_policy_engine", lambda: order.append("policies")
    )
    monkeypatch.setattr(main_module, "auto_seed", _seed)
    monkeypatch.setattr(main_module, "run_scheduler", _scheduler)

    async with LifespanManager(create_app()):
        await asyncio.sleep(0)
        assert order == ["policies", "seed", "scheduler"]

    assert order[-1] == "scheduler-stopped", (
        "shutdown sets the stop event and awaits the task, so a poll in flight finishes"
    )
    assert stop_events[0].is_set()


# @verifies REQ-0003
async def test_a_bundle_that_will_not_load_stops_the_service_starting(monkeypatch):
    """
    @verifies REQ-0050

    The alternative — starting anyway — is what `../celine-grid` does, and there it
    means every authorisation check silently allows. Here it means the process does not
    come up, which is loud and therefore safe.
    """
    from celine.nudging import main as main_module

    def _explode() -> None:
        raise ValueError("POLICIES_DIR not set")

    monkeypatch.setattr(main_module, "init_policy_engine", _explode)

    with pytest.raises(ValueError, match="POLICIES_DIR"):
        async with LifespanManager(create_app()):
            pass


# @verifies REQ-0050
async def test_a_seed_that_will_not_load_stops_the_service_starting(monkeypatch):
    """
    @verifies REQ-0069

    `auto_seed` reads `active_kinds.yaml` through the same loader as everything else, and
    that loader raises on a missing or malformed catalogue. A service that started
    without one would suppress every notification as an unknown kind, so failing here is
    the better of the two.
    """
    from celine.nudging import main as main_module

    async def _explode() -> None:
        raise ValueError("Missing active kinds file")

    monkeypatch.setattr(main_module, "init_policy_engine", lambda: None)
    monkeypatch.setattr(main_module, "auto_seed", _explode)

    with pytest.raises(ValueError, match="Missing active kinds file"):
        async with LifespanManager(create_app()):
            pass


# @verifies REQ-0050
async def test_the_health_endpoint_reports_only_that_the_process_is_up(client):
    """
    It checks nothing: not the database, not the seed, not the scheduler. A service
    whose PostgreSQL has gone answers `{"status": "ok"}` and fails every request behind
    it, so this is a liveness probe and must not be read as a readiness one.
    """
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
