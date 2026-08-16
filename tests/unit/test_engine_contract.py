"""The facts contract: what a sender must put in an event for anything to happen.

Four services send events here and none of them waits for an answer, so a contract
violation is invisible at the sender. What makes it observable at all is that every
rejection writes an audit row to `nudges_log` — the rows tested here are the only trace
that a malformed event ever arrived.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from celine.nudging.db.models import NudgeLog
from celine.nudging.engine.engine_service import (
    EngineResultStatus,
    _infer_time_scope,
    _normalize_time_fields,
    _validate_facts_contract,
    run_engine_batch,
)
from celine.nudging.engine.rules.contract import validate_facts_contract
from celine.nudging.engine.rules.models import DigitalTwinEvent

# ---------------------------------------------------------------------------
# facts_version and scenario
# ---------------------------------------------------------------------------


# @verifies REQ-0012
def test_both_facts_version_and_scenario_are_required():
    ok, errors = _validate_facts_contract({"facts_version": "1", "scenario": "x"})
    assert ok is True and errors == []

    ok, errors = _validate_facts_contract({"scenario": "x"})
    assert ok is False and errors == ["missing facts_version"]

    ok, errors = _validate_facts_contract({"facts_version": "1"})
    assert ok is False and errors == ["missing scenario"]

    ok, errors = _validate_facts_contract({})
    assert ok is False and errors == ["missing facts_version", "missing scenario"]


# @verifies REQ-0012
def test_a_non_string_version_or_scenario_is_a_violation():
    """
    The engine requires strings; the endpoint's own validator (`rules/contract.py`) only
    requires truthiness, so `facts_version: 1` passes at the edge and is rejected one
    layer in. Both are pinned because the two are separate implementations of the same
    sentence and nothing keeps them aligned.
    """
    ok, errors = _validate_facts_contract({"facts_version": 1, "scenario": "x"})
    assert ok is False and errors == ["missing facts_version"]

    edge = validate_facts_contract({"facts_version": 1, "scenario": "x"})
    assert edge.ok is True
    assert edge.facts_version is None, "kept as None because it was not a string"


# @verifies REQ-0012
def test_the_edge_validator_reports_what_it_found():
    result = validate_facts_contract({"facts_version": "v2", "scenario": "price_up"})
    assert (result.ok, result.errors) == (True, [])
    assert (result.scenario, result.facts_version) == ("price_up", "v2")

    empty = validate_facts_contract({})
    assert empty.ok is False
    assert empty.errors == ["missing facts_version", "missing scenario"]
    assert empty.scenario is None and empty.facts_version is None


# ---------------------------------------------------------------------------
# The time scope, which is also the frequency
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "frequency"),
    [
        ("2026-08-15", "daily"),
        ("2026-W33", "weekly"),
        ("2026-08", "monthly"),
        ("2026", "yearly"),
    ],
)
# @verifies REQ-0020
def test_the_shape_of_the_time_value_decides_the_frequency(value, frequency):
    """
    There is no `frequency` field in an event. The *format* of the time string is the
    frequency, which means a sender changes what a rule is by changing how it formats a
    date — `2026-08` and `2026-08-15` select different rules for the same day.
    """
    scope = _infer_time_scope({"time": value})
    assert scope is not None
    assert (scope.frequency, scope.scope) == (frequency, value)


# @verifies REQ-0020
def test_time_wins_over_date_wins_over_week_wins_over_period():
    """
    Four keys are consulted in a fixed order and the first non-empty one wins, so an
    event carrying both `time` and `period` is decided by `time` alone.
    """
    facts = {"time": "2026-08-15", "date": "2026-01-01", "week": "2026-W01", "period": "2020"}
    assert _infer_time_scope(facts).scope == "2026-08-15"
    assert _infer_time_scope({"date": "2026-01-01", "period": "2020"}).scope == "2026-01-01"
    assert _infer_time_scope({"week": "2026-W01", "period": "2020"}).scope == "2026-W01"
    assert _infer_time_scope({"period": "2020"}).scope == "2020"


@pytest.mark.parametrize(
    "value",
    ["2026/08/15", "15-08-2026", "2026-8-1", "2026-W3", "August", "", "   ", "20260815"],
)
# @verifies REQ-0021
def test_a_time_value_the_engine_does_not_recognise_is_no_scope_at_all(value):
    """
    The patterns are anchored, so a nearly-right date is as good as no date. This is the
    single most likely way for a new sender to be silently ignored: the event is
    accepted, an audit row is written, and nothing is delivered.
    """
    assert _infer_time_scope({"time": value}) is None


# @verifies REQ-0021
def test_a_non_string_time_value_is_no_scope():
    assert _infer_time_scope({"time": 2026}) is None
    assert _infer_time_scope({"time": None, "date": None}) is None
    assert _infer_time_scope({}) is None


# @verifies REQ-0020
def test_surrounding_whitespace_is_forgiven():
    scope = _infer_time_scope({"time": "  2026-08-15  "})
    assert (scope.frequency, scope.scope) == ("daily", "2026-08-15")


@pytest.mark.parametrize(
    ("frequency", "value", "expected_key"),
    [
        ("daily", "2026-08-15", "date"),
        ("weekly", "2026-W33", "week"),
        ("monthly", "2026-08", "period"),
        ("yearly", "2026", "period"),
    ],
)
# @verifies REQ-0022
def test_the_inferred_scope_is_written_back_into_the_facts(frequency, value, expected_key):
    """
    Templates and evaluators read `date`, `week` and `period` directly. Normalising
    means a sender that sent only `time` still renders a message that mentions the
    period, and an evaluator comparing `facts["period"]` does not see a `KeyError`.
    """
    scope = _infer_time_scope({"time": value})
    out = _normalize_time_fields({"time": value, "other": 1}, scope)

    assert out["time"] == value
    assert out[expected_key] == value
    assert out["other"] == 1, "normalising copies the facts rather than replacing them"


# @verifies REQ-0022
def test_normalising_does_not_mutate_the_caller_s_facts():
    facts = {"time": "2026-08-15"}
    _normalize_time_fields(facts, _infer_time_scope(facts))
    assert facts == {"time": "2026-08-15"}


# ---------------------------------------------------------------------------
# What the engine does with a violation
# ---------------------------------------------------------------------------


async def _audit_rows(db) -> list[NudgeLog]:
    result = await db.execute(select(NudgeLog))
    return list(result.scalars().all())


# @verifies REQ-0012
async def test_an_event_missing_the_contract_is_refused_and_audited(db):
    """
    @verifies REQ-0017
    """
    event = DigitalTwinEvent(event_type="dt.metrics", user_id="user-alice", facts={"time": "2026-08-15"})

    results = await run_engine_batch(event, db)

    assert len(results) == 1
    assert results[0].status is EngineResultStatus.MISSING_FACTS
    assert results[0].reason == "invalid_facts_contract"
    assert results[0].details == {"errors": ["missing facts_version", "missing scenario"]}

    rows = await _audit_rows(db)
    assert len(rows) == 1
    assert rows[0].status == "missing_facts"
    assert rows[0].rule_id == "__no_rule__"
    assert rows[0].payload["details"] == {
        "errors": ["missing facts_version", "missing scenario"]
    }


# @verifies REQ-0021
async def test_an_event_with_no_usable_time_is_refused_and_audited(db):
    """
    @verifies REQ-0017
    """
    event = DigitalTwinEvent(
        event_type="dt.metrics",
        user_id="user-alice",
        facts={"facts_version": "1", "scenario": "price_up", "time": "yesterday"},
    )

    results = await run_engine_batch(event, db)

    assert results[0].status is EngineResultStatus.MISSING_FACTS
    assert results[0].reason == "missing_or_invalid_time_scope"

    rows = await _audit_rows(db)
    assert len(rows) == 1
    assert rows[0].payload["details"] == {"reason": "missing_or_invalid_time_scope"}
    assert rows[0].payload["scenario"] == "price_up"


# @verifies REQ-0017
async def test_an_audit_row_for_a_refusal_never_collides_with_a_real_dedup_key(db):
    """
    Every audit row shares one column with the delivered nudges — `dedup_key`, which is
    unique. A refusal therefore gets a synthetic key carrying a fresh UUID, because two
    malformed events from the same sender on the same day would otherwise be one row and
    the second would raise instead of being recorded.
    """
    facts = {"facts_version": "1", "scenario": "price_up", "time": "not-a-date"}
    event = DigitalTwinEvent(event_type="dt.metrics", user_id="user-alice", facts=facts)

    await run_engine_batch(event, db)
    await run_engine_batch(event, db)

    rows = await _audit_rows(db)
    assert len(rows) == 2
    assert rows[0].dedup_key != rows[1].dedup_key
    assert all(row.dedup_key.startswith("attempt:") for row in rows)


# @verifies REQ-0015
async def test_facts_are_preferred_over_payload_but_payload_still_works(db):
    """
    `payload` is the older shape. It is still accepted when `facts` is empty, which is
    what keeps an un-migrated sender working — and also what makes an event that sets
    *both* silently ignore `payload`.
    """
    both = DigitalTwinEvent(
        event_type="dt.metrics",
        user_id="user-alice",
        payload={"facts_version": "1", "scenario": "from_payload", "time": "2026-08-15"},
        facts={"facts_version": "1", "scenario": "from_facts", "time": "2026-08-15"},
    )
    await run_engine_batch(both, db)
    rows = await _audit_rows(db)
    assert rows[0].payload["scenario"] == "from_facts"

    payload_only = DigitalTwinEvent(
        event_type="dt.metrics",
        user_id="user-alice",
        payload={"facts_version": "1", "scenario": "from_payload", "time": "2026-08-15"},
    )
    await run_engine_batch(payload_only, db)
    rows = await _audit_rows(db)
    assert {row.payload["scenario"] for row in rows} == {"from_facts", "from_payload"}
