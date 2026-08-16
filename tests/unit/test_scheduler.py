"""Scheduled delivery: the path nobody is watching when it fires.

An event is stored now and dispatched later by a polling loop inside the API process.
There is no caller to answer, no response code, and the only record of what happened is
the row's own `status` and `last_error` — so those columns are what this file is about.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import select

from celine.nudging.db.models import Notification, ScheduledEvent, utc_now
from celine.nudging.scheduler import process_due_scheduled_events, run_scheduler
from tests.fakes import seed_rule

DAILY = {"facts_version": "1", "scenario": "reminder", "time": "2026-08-15"}


@pytest.fixture(autouse=True)
def scheduler_uses_the_test_database(monkeypatch, db_sessionmaker):
    """The scheduler opens its own session from `AsyncSessionLocal`.

    It is the one component that is not handed a session, because nothing calls it — so
    pointing it at the test database is the only way to run it at all. This is also why
    the `app` fixture disables the loop: two sessions on one SQLite connection racing
    inside an API test would deadlock rather than fail.
    """
    from celine.nudging import scheduler as scheduler_module

    monkeypatch.setattr(scheduler_module, "AsyncSessionLocal", db_sessionmaker)


async def _rule(db, tmp_path, *, triggers: bool = True):
    evaluator = tmp_path / "evaluate.py"
    evaluator.write_text(
        f"def evaluate(rule, facts):\n    return {triggers}, dict(facts), None\n"
    )
    return await seed_rule(
        db,
        "reminder",
        definition={
            "dedup_window": "daily",
            "scenarios": ["reminder"],
            "evaluator_path": str(evaluator),
        },
    )


def _due(**kwargs) -> ScheduledEvent:
    defaults = dict(
        event_type="flexibility.reminder",
        user_id="user-alice",
        community_id=None,
        trigger_at=utc_now() - timedelta(minutes=1),
        facts=dict(DAILY),
        status="pending",
    )
    return ScheduledEvent(**{**defaults, **kwargs})


# ---------------------------------------------------------------------------
# What is picked up
# ---------------------------------------------------------------------------


# @verifies REQ-0065
async def test_an_event_whose_time_has_come_is_dispatched(db, tmp_path):
    """
    @verifies REQ-0066

    Dispatch is the same engine and the same orchestrator as an ingested event — the
    only difference is that the facts were stored earlier. A scheduled reminder is
    therefore deduplicated, rate-limited and preference-filtered exactly like a live one.
    """
    await _rule(db, tmp_path)
    db.add(_due())
    await db.commit()

    await process_due_scheduled_events()

    event = (await db.execute(select(ScheduledEvent))).scalar_one()
    assert event.status == "dispatched"
    assert event.dispatched_at is not None
    assert event.last_error is None
    assert len((await db.execute(select(Notification))).scalars().all()) == 1


# @verifies REQ-0065
async def test_an_event_in_the_future_is_left_alone(db, tmp_path):
    await _rule(db, tmp_path)
    db.add(_due(trigger_at=utc_now() + timedelta(hours=1)))
    await db.commit()

    await process_due_scheduled_events()

    assert (await db.execute(select(ScheduledEvent))).scalar_one().status == "pending"
    assert (await db.execute(select(Notification))).scalars().all() == []


# @verifies REQ-0065
async def test_an_event_that_has_already_run_is_not_run_again(db, tmp_path):
    """
    Only `pending` rows are selected, so `dispatched` and `failed` are terminal. A failed
    dispatch is therefore **never retried** — somebody has to set it back by hand — which
    is worth knowing before relying on a reminder.
    """
    await _rule(db, tmp_path)
    db.add(_due(status="dispatched", external_key="already-sent"))
    db.add(_due(status="failed", external_key="gave-up"))
    await db.commit()

    await process_due_scheduled_events()

    assert (await db.execute(select(Notification))).scalars().all() == []


# @verifies REQ-0065
async def test_the_oldest_due_events_are_taken_first_and_the_batch_is_bounded(db, tmp_path):
    """
    Ordered by `trigger_at`, limited to twenty per poll. A backlog therefore drains
    oldest-first at twenty per `SCHEDULER_POLL_SECONDS` rather than all at once.
    """
    await _rule(db, tmp_path)
    for index in range(25):
        db.add(
            _due(
                external_key=f"key-{index:02d}",
                trigger_at=utc_now() - timedelta(minutes=30 - index),
                facts={**DAILY, "time": f"2026-08-{index + 1:02d}"},
            )
        )
    await db.commit()

    await process_due_scheduled_events()

    dispatched = (
        await db.execute(
            select(ScheduledEvent).where(ScheduledEvent.status == "dispatched")
        )
    ).scalars().all()
    assert len(dispatched) == 20
    assert sorted(e.external_key for e in dispatched) == [f"key-{i:02d}" for i in range(20)]


# @verifies REQ-0065
async def test_a_smaller_batch_can_be_asked_for(db, tmp_path):
    await _rule(db, tmp_path)
    db.add(_due(external_key="a", facts={**DAILY, "time": "2026-08-01"}))
    db.add(_due(external_key="b", facts={**DAILY, "time": "2026-08-02"}))
    await db.commit()

    await process_due_scheduled_events(batch_size=1)

    statuses = sorted(e.status for e in (await db.execute(select(ScheduledEvent))).scalars())
    assert statuses == ["dispatched", "pending"]


# ---------------------------------------------------------------------------
# What happens when a dispatch goes wrong
# ---------------------------------------------------------------------------


# @verifies REQ-0066
async def test_an_event_that_triggers_nothing_is_still_marked_dispatched(db, tmp_path):
    """
    "Dispatched" means the engine ran, not that a message was sent. A reminder whose
    rule declined, or whose notification was suppressed by a preference, ends in exactly
    the same state as one that reached a browser.
    """
    await _rule(db, tmp_path, triggers=False)
    db.add(_due())
    await db.commit()

    await process_due_scheduled_events()

    assert (await db.execute(select(ScheduledEvent))).scalar_one().status == "dispatched"
    assert (await db.execute(select(Notification))).scalars().all() == []


# @verifies REQ-0067
async def test_a_failing_event_is_recorded_and_the_rest_of_the_batch_continues(db, tmp_path):
    """
    One event's exception must not take the poll down with it: the loop has no
    supervisor, and an exception escaping `process_due_scheduled_events` would leave the
    remaining events unprocessed until the next poll — or for ever, if the same row is
    picked up first every time.

    The error is stored on the row, which is the only place anyone can read it.
    """
    await _rule(db, tmp_path)
    db.add(_due(external_key="broken", facts={"facts_version": "1"}))
    db.add(_due(external_key="fine", facts=dict(DAILY)))
    await db.commit()

    from celine.nudging import scheduler as scheduler_module

    original = scheduler_module.run_engine_batch

    async def _explode_for_the_broken_one(evt, session, **kwargs):
        if evt.facts.get("scenario") is None:
            raise RuntimeError("engine blew up")
        return await original(evt, session, **kwargs)

    scheduler_module.run_engine_batch = _explode_for_the_broken_one
    try:
        await process_due_scheduled_events()
    finally:
        scheduler_module.run_engine_batch = original

    rows = {
        e.external_key: e
        for e in (await db.execute(select(ScheduledEvent))).scalars().all()
    }
    assert rows["broken"].status == "failed"
    assert "engine blew up" in rows["broken"].last_error
    assert rows["broken"].dispatched_at is None
    assert rows["fine"].status == "dispatched"


# @verifies REQ-0067
async def test_a_previous_error_is_cleared_when_a_dispatch_succeeds(db, tmp_path):
    await _rule(db, tmp_path)
    db.add(_due(last_error="something went wrong last time"))
    await db.commit()

    await process_due_scheduled_events()

    assert (await db.execute(select(ScheduledEvent))).scalar_one().last_error is None


# @verifies REQ-0065
async def test_an_empty_poll_touches_nothing(db):
    await process_due_scheduled_events()

    assert (await db.execute(select(ScheduledEvent))).scalars().all() == []


# ---------------------------------------------------------------------------
# The loop itself
# ---------------------------------------------------------------------------


# @verifies REQ-0065
async def test_the_loop_polls_until_it_is_told_to_stop(monkeypatch):
    """
    The loop waits on the stop event with a timeout rather than sleeping, so shutdown is
    immediate rather than delayed by up to `SCHEDULER_POLL_SECONDS`. `main.py` awaits the
    task after setting the event, which is what makes that matter.
    """
    from celine.nudging import scheduler as scheduler_module

    polls = 0

    async def _count() -> None:
        nonlocal polls
        polls += 1

    monkeypatch.setattr(scheduler_module, "process_due_scheduled_events", _count)
    monkeypatch.setattr(scheduler_module.settings, "SCHEDULER_POLL_SECONDS", 0.01)

    stop = asyncio.Event()
    task = asyncio.create_task(run_scheduler(stop))
    await asyncio.sleep(0.05)
    stop.set()
    await asyncio.wait_for(task, timeout=1)

    assert polls >= 2


# @verifies REQ-0067
async def test_a_poll_that_raises_does_not_end_the_loop(monkeypatch):
    """
    The outer `try` is what keeps a database outage from silently ending scheduled
    delivery for the life of the process. Nothing restarts this task: if it exits, the
    only symptom is that reminders stop arriving.
    """
    from celine.nudging import scheduler as scheduler_module

    calls = 0

    async def _fail_then_work() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("database is down")

    monkeypatch.setattr(scheduler_module, "process_due_scheduled_events", _fail_then_work)
    monkeypatch.setattr(scheduler_module.settings, "SCHEDULER_POLL_SECONDS", 0.01)

    stop = asyncio.Event()
    task = asyncio.create_task(run_scheduler(stop))
    await asyncio.sleep(0.05)
    stop.set()
    await asyncio.wait_for(task, timeout=1)

    assert calls >= 2, "the loop survived the failing poll"
