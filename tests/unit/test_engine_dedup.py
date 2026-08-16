"""Deduplication: the half of this service whose job is *not* to send.

A suppressed notification and a broken one look identical from outside, which is the
argument for this file existing at all. The mechanism is a unique constraint plus an
error-text match, so it is tested from three sides: the key that is computed, the
constraint that rejects the duplicate, and the branch that recognises the rejection.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import select

from celine.nudging.db.models import Notification, NudgeLog
from celine.nudging.engine.engine_service import (
    EngineResultStatus,
    _dedup_scope,
    compute_dedup_key,
    run_engine_batch,
)
from celine.nudging.engine.rules.models import DigitalTwinEvent
from tests.fakes import make_rule, seed_rule

_REPO_ROOT = Path(__file__).resolve().parents[2]
DAILY = {"facts_version": "1", "scenario": "price_up", "time": "2026-08-15"}


def _event(*, user_id="user-alice", community_id=None, facts=None):
    return DigitalTwinEvent(
        event_type="dt.metrics",
        user_id=user_id,
        community_id=community_id,
        facts={**DAILY, **(facts or {})},
    )


async def _seed(db, **definition):
    evaluator = _REPO_ROOT / "seed" / "rules" / "flexibility_opportunity" / "evaluate.py"
    return await seed_rule(
        db,
        "price_up",
        definition={
            "dedup_window": "daily",
            "scenarios": ["price_up"],
            "evaluator_path": str(evaluator),
            **definition,
        },
    )


# ---------------------------------------------------------------------------
# The key
# ---------------------------------------------------------------------------


# @verifies REQ-0033
def test_the_key_names_the_rule_the_person_the_community_and_the_period():
    """
    Four parts, colon-separated, and every one of them widens the suppression when it is
    absent. A missing community becomes an empty segment rather than being dropped, so
    `rule:user::2026-08-15` is the *community-less* key and cannot collide with a real
    community named the empty string.
    """
    assert compute_dedup_key("r", "u", "c", "2026-08-15") == "r:u:c:2026-08-15"
    assert compute_dedup_key("r", "u", None, "2026-08-15") == "r:u::2026-08-15"


# @verifies REQ-0033
def test_two_communities_are_two_keys_and_so_are_two_people():
    """
    The same rule firing for two members of a community sends two notifications. That is
    the intent — dedup is per person — and it is worth pinning because a key that left
    the user out would silence everyone after the first.
    """
    keys = {
        compute_dedup_key("r", "alice", "c1", "s"),
        compute_dedup_key("r", "bob", "c1", "s"),
        compute_dedup_key("r", "alice", "c2", "s"),
        compute_dedup_key("r2", "alice", "c1", "s"),
        compute_dedup_key("r", "alice", "c1", "s2"),
    }
    assert len(keys) == 5


# ---------------------------------------------------------------------------
# The window, which becomes the scope segment
# ---------------------------------------------------------------------------


# @verifies REQ-0034
def test_always_never_suppresses():
    """
    `always` produces a fresh UUID per evaluation, so the key can never repeat. Note
    that a rule declaring it can never be *selected* either — see REQ-0024 — so this
    branch is only reachable by calling the engine directly.
    """
    rule = make_rule(definition={"dedup_window": "always"})
    first = _dedup_scope(rule, DAILY)
    assert first != _dedup_scope(rule, DAILY)


# @verifies REQ-0034
def test_once_suppresses_for_ever():
    rule = make_rule(definition={"dedup_window": "once"})
    assert _dedup_scope(rule, DAILY) == "once"
    assert _dedup_scope(make_rule(definition={"dedup_window": "one"}), DAILY) == "once"


# @verifies REQ-0034
def test_hourly_uses_the_hour_from_the_facts_or_the_clock():
    rule = make_rule(definition={"dedup_window": "hourly"})
    assert _dedup_scope(rule, {**DAILY, "hour": "2026-08-15T09"}) == "2026-08-15T09"

    now_hour = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H")
    assert _dedup_scope(rule, DAILY) == now_hour


@pytest.mark.parametrize("window", ["daily", "weekly", "monthly", "yearly"])
# @verifies REQ-0034
def test_a_frequency_window_uses_the_period_the_sender_sent(window):
    """
    The window name does not decide the granularity — the sender's own time value does.
    A rule declared `monthly` that receives `time: 2026-08-15` dedups per *day*, because
    the scope is the string it was given. The window's only other job is selecting the
    rule (REQ-0024), and that pairing is what normally keeps the two consistent.
    """
    rule = make_rule(definition={"dedup_window": window})
    assert _dedup_scope(rule, {"time": "2026-08-15"}) == "2026-08-15"
    assert _dedup_scope(rule, {"period": "2026-08"}) == "2026-08"


# @verifies REQ-0034
def test_an_unrecognised_window_falls_back_to_the_period_then_to_this_month():
    """
    `two_weeks` — which two seeded rules declare — lands here. It behaves exactly like
    `daily`/`monthly`: the scope is whatever period the sender sent. The current month
    is the last resort, so a rule with no window and no time value suppresses for the
    rest of the calendar month.
    """
    rule = make_rule(definition={"dedup_window": "two_weeks"})
    assert _dedup_scope(rule, {"time": "2026-08-15"}) == "2026-08-15"

    this_month = datetime.now(timezone.utc).strftime("%Y-%m")
    assert _dedup_scope(make_rule(definition={}), {}) == this_month


# ---------------------------------------------------------------------------
# The constraint, and the string the engine matches on
# ---------------------------------------------------------------------------


# @verifies REQ-0035
def test_the_constraint_the_engine_looks_for_is_the_one_the_schema_declares():
    """
    `_run_single_rule` recognises a duplicate by finding the literal
    `uq_nudges_dedup_key` in the driver's error text. Nothing connects that string to
    the schema, so renaming the constraint in a migration would turn every duplicate
    into an unhandled `500` with no test failing. This is that test.
    """
    engine_source = (
        _REPO_ROOT / "src" / "celine" / "nudging" / "engine" / "engine_service.py"
    ).read_text()
    assert '"uq_nudges_dedup_key" not in error_text' in engine_source

    names = {c.name for c in NudgeLog.__table__.constraints if c.name}
    assert "uq_nudges_dedup_key" in names

    migration = (
        _REPO_ROOT / "alembic" / "versions" / "8a2a854458f2_init.py"
    ).read_text()
    assert 'name="uq_nudges_dedup_key"' in migration


# @verifies REQ-0035
async def test_the_database_refuses_a_second_row_with_the_same_key(db):
    """
    The suppression is the database's, not the engine's: two concurrent workers racing
    on the same event both compute the same key and exactly one insert survives. An
    engine-side "select then insert" would let both through.
    """
    from sqlalchemy.exc import IntegrityError
    from tests.fakes import make_nudge_log

    db.add(make_nudge_log(nudge_id="first", dedup_key="rule:user::scope"))
    await db.commit()

    db.add(make_nudge_log(nudge_id="second", dedup_key="rule:user::scope"))
    with pytest.raises(IntegrityError):
        await db.commit()
    await db.rollback()


# ---------------------------------------------------------------------------
# End to end
# ---------------------------------------------------------------------------


# @verifies REQ-0035
async def test_the_same_event_twice_creates_one_notification_and_two_audit_rows(db):
    """
    @verifies REQ-0017

    The second evaluation is not silent — it writes a `suppressed_dedup` row naming the
    key it collided with. That row is the only evidence anywhere in the platform that a
    duplicate was stopped, and it is why the audit log is written even when nothing is
    delivered.
    """
    await _seed(db)

    first, second = await run_engine_batch(_event(), db), await run_engine_batch(_event(), db)

    assert first[0].status is EngineResultStatus.CREATED
    assert second[0].status is EngineResultStatus.SUPPRESSED_DEDUP
    assert second[0].reason == "duplicate_in_dedup_window"
    assert second[0].details["dedup_key"] == "price_up:user-alice::2026-08-15"

    notifications = (await db.execute(select(Notification))).scalars().all()
    assert len(notifications) == 1

    rows = (await db.execute(select(NudgeLog))).scalars().all()
    assert sorted(row.status for row in rows) == ["created", "suppressed_dedup"]


# @verifies REQ-0034
async def test_the_next_day_is_a_new_scope(db):
    await _seed(db)

    await run_engine_batch(_event(), db)
    tomorrow = await run_engine_batch(_event(facts={"time": "2026-08-16"}), db)

    assert tomorrow[0].status is EngineResultStatus.CREATED
    assert len((await db.execute(select(Notification))).scalars().all()) == 2


# @verifies REQ-0033
async def test_another_participant_is_not_suppressed_by_the_first(db):
    await _seed(db)

    await run_engine_batch(_event(user_id="user-alice"), db)
    bob = await run_engine_batch(_event(user_id="user-bob"), db)

    assert bob[0].status is EngineResultStatus.CREATED


# @verifies REQ-0017
async def test_the_notification_and_its_audit_row_are_written_together(db):
    """
    One transaction: the `nudges_log` row that claims the dedup key and the
    `notifications` row a participant will read are inserted in the same commit. A
    notification with no audit row would be untraceable, and an audit row with no
    notification would hold the key against a message nobody ever gets.
    """
    await _seed(db)

    [result] = await run_engine_batch(_event(), db)

    log = (await db.execute(select(NudgeLog))).scalar_one()
    notification = (await db.execute(select(Notification))).scalar_one()
    assert log.id == result.nudge.nudge_id
    assert notification.nudge_log_id == log.id
    assert notification.id != log.id, "the notification carries its own id"
    assert notification.status == "pending"
    assert log.status == "created"


# @verifies REQ-0032
async def test_a_notification_copies_the_rule_s_classification(db):
    """
    `family`, `type` and `severity` are denormalised onto the notification so the read
    path never joins. They are copied at creation, so changing a rule does not restate
    what was already sent — which is right, and also means the two can disagree.
    """
    await _seed(db)
    rule = await db.get(type(make_rule()), "price_up")
    rule.family, rule.type, rule.severity = "energy", "opportunity", "warning"
    await db.commit()

    await run_engine_batch(_event(), db)

    notification = (await db.execute(select(Notification))).scalar_one()
    assert (notification.family, notification.type, notification.severity) == (
        "energy",
        "opportunity",
        "warning",
    )
