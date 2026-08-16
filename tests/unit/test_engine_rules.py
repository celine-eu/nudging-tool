"""Which rule runs, in which language, with which template.

Every step here can drop an event without an error: a scenario nothing claims, a rule
whose `dedup_window` does not match the shape of the sender's date, a community override
that disabled it, a missing required fact. The engine reports each as a status and an
audit row, and nothing upstream reads either.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from celine.nudging.db.models import Notification, NudgeLog, RuleOverride
from celine.nudging.engine.engine_service import (
    EngineResultStatus,
    _apply_rule_override,
    _deep_merge,
    _filter_rule_ids_by_definition,
    _load_rule_and_template,
    _normalize_lang,
    _resolve_lang,
    _resolve_rule_ids_from_db,
    _resolve_rule_ids_from_scenario,
    _validate_required_facts,
    run_engine_batch,
)
from celine.nudging.engine.rules.models import DigitalTwinEvent
from tests.fakes import make_preference, make_rule, make_template, seed_rule

DAILY = {"facts_version": "1", "scenario": "price_up", "time": "2026-08-15"}


def _event(facts: dict | None = None, *, user_id: str = "user-alice", community_id=None):
    return DigitalTwinEvent(
        event_type="dt.metrics",
        user_id=user_id,
        community_id=community_id,
        facts={**DAILY, **(facts or {})},
    )


async def _seed_triggering_rule(db, tmp_path, **kwargs):
    """A rule whose evaluator always triggers, with a daily window and one template."""
    evaluator = tmp_path / "evaluate.py"
    evaluator.write_text("def evaluate(rule, facts):\n    return True, dict(facts), None\n")
    definition = {
        "kind": "price_up",
        "dedup_window": "daily",
        "scenarios": ["price_up"],
        "evaluator_path": str(evaluator),
        **kwargs.pop("definition", {}),
    }
    return await seed_rule(db, "price_up", definition=definition, **kwargs)


# ---------------------------------------------------------------------------
# From a scenario to a set of rules
# ---------------------------------------------------------------------------


# @verifies REQ-0023
async def test_a_rule_claims_a_scenario_in_its_own_column_or_in_its_definition(db):
    """
    Two places carry the same list and both are read, because the seed loader writes it
    to both. A rule that claims the scenario in neither is unreachable however well its
    evaluator works.
    """
    db.add(make_rule("by_column", definition={"dedup_window": "daily"}))
    db.add(make_rule("by_definition", definition={"dedup_window": "daily", "scenarios": ["price_up"]}))
    db.add(make_rule("by_neither", definition={"dedup_window": "daily"}))
    (await db.get(type(make_rule()), "by_column")).scenarios = ["price_up"]
    await db.commit()

    found = await _resolve_rule_ids_from_db(db, "price_up")

    assert sorted(found) == ["by_column", "by_definition"]


# @verifies REQ-0025
async def test_a_disabled_rule_claims_nothing(db):
    db.add(make_rule("off", enabled=False, definition={"dedup_window": "daily"}, scenarios=["price_up"]))
    await db.commit()

    assert await _resolve_rule_ids_from_db(db, "price_up") == []


# @verifies REQ-0023
async def test_an_empty_scenario_resolves_to_nothing(db):
    assert await _resolve_rule_ids_from_db(db, "") == []


# @verifies REQ-0023
def test_the_settings_map_is_the_fallback_and_the_scenario_name_is_the_last_resort(
    monkeypatch,
):
    """
    Three sources in order: the rules in the database, then `SCENARIO_TO_RULE_IDS` from
    the environment, then the scenario read as a rule id. The last one is why a typo in
    a sender's scenario produces `unknown_scenario` rather than a crash — and why a rule
    whose id happens to equal a scenario is reachable without declaring it.
    """
    from celine.nudging.config import settings as settings_module

    monkeypatch.setattr(
        settings_module.settings, "SCENARIO_TO_RULE_IDS", {"price_up": ["a", "b"]}
    )
    assert _resolve_rule_ids_from_scenario("price_up") == ["a", "b"]
    assert _resolve_rule_ids_from_scenario("unmapped") == ["unmapped"]
    assert _resolve_rule_ids_from_scenario("") == []


# @verifies REQ-0024
async def test_only_a_rule_whose_window_matches_the_inferred_frequency_runs(db):
    """
    The `dedup_window` is doing two jobs: it decides how long a duplicate is suppressed
    *and* it is the only thing that decides whether the rule is eligible at all. A
    monthly rule is invisible to a daily event even when both name the same scenario.
    """
    db.add(make_rule("daily_rule", definition={"dedup_window": "daily"}))
    db.add(make_rule("monthly_rule", definition={"dedup_window": "MONTHLY"}))
    db.add(make_rule("windowless", definition={}))
    await db.commit()

    ids = ["daily_rule", "monthly_rule", "windowless"]
    assert await _filter_rule_ids_by_definition(db, ids, "daily") == ["daily_rule"]
    assert await _filter_rule_ids_by_definition(db, ids, "monthly") == ["monthly_rule"]
    assert await _filter_rule_ids_by_definition(db, ids, "yearly") == []


# @verifies REQ-0024
async def test_a_rule_whose_window_is_not_a_frequency_can_never_run(db):
    """
    `always`, `once`, `hourly` and `two_weeks` are all meaningful to `_dedup_scope`, and
    none of them can ever equal an inferred frequency — which is only ever `daily`,
    `weekly`, `monthly` or `yearly`. **Four rules in `seed/` are inert for this reason**,
    including `welcome` (`once`) and `botanswer` (`always`).

    Filed as https://github.com/celine-eu/nudging-tool/issues/34. The requirement states
    what happens today, not what was meant.
    """
    for window in ("always", "once", "hourly", "two_weeks"):
        db.add(make_rule(window, definition={"dedup_window": window}))
    await db.commit()

    for frequency in ("daily", "weekly", "monthly", "yearly"):
        assert await _filter_rule_ids_by_definition(
            db, ["always", "once", "hourly", "two_weeks"], frequency
        ) == []


# @verifies REQ-0016
async def test_a_scenario_no_rule_claims_is_reported_and_audited(db):
    """
    @verifies REQ-0017

    The status is `unknown_scenario`, and the reason is
    **`no_rule_for_inferred_frequency` rather than `scenario_not_mapped`**. The
    last-resort fallback reads the scenario as a rule id, so `rule_ids_all` is never
    empty for a non-empty scenario — and an empty one has already been refused by the
    contract check. `scenario_not_mapped` is therefore structurally dead, which matters
    because it is the more accurate of the two messages.
    """
    results = await run_engine_batch(_event({"scenario": "nobody_claims_this"}), db)

    assert results[0].status is EngineResultStatus.UNKNOWN_SCENARIO
    assert results[0].reason == "no_rule_for_inferred_frequency"

    rows = (await db.execute(select(NudgeLog))).scalars().all()
    assert rows[0].status == "unknown_scenario"
    assert rows[0].payload["details"] == {
        "reason": "no_rule_for_inferred_frequency",
        "frequency": "daily",
    }


# @verifies REQ-0024
async def test_a_rule_that_exists_but_not_at_this_frequency_is_reported_separately(db):
    """
    @verifies REQ-0017

    `scenario_not_mapped` and `no_rule_for_inferred_frequency` are different reasons on
    purpose: the first means nobody claims the scenario, the second means somebody does
    but the sender's date format did not select them. Only the audit row distinguishes
    them, and the caller sees the same `400`.
    """
    await seed_rule(db, "price_up", definition={"dedup_window": "monthly", "scenarios": ["price_up"]})

    results = await run_engine_batch(_event(), db)

    assert results[0].status is EngineResultStatus.UNKNOWN_SCENARIO
    assert results[0].reason == "no_rule_for_inferred_frequency"

    rows = (await db.execute(select(NudgeLog))).scalars().all()
    assert rows[0].payload["details"] == {
        "reason": "no_rule_for_inferred_frequency",
        "frequency": "daily",
    }


# @verifies REQ-0019
async def test_every_matching_rule_produces_its_own_result(db, tmp_path):
    """
    One event, several rules, one result each — and each is evaluated independently, so
    one rule failing its required facts does not stop the other from being delivered.
    """
    evaluator = tmp_path / "evaluate.py"
    evaluator.write_text("def evaluate(rule, facts):\n    return True, dict(facts), None\n")
    common = {"dedup_window": "daily", "scenarios": ["price_up"], "evaluator_path": str(evaluator)}
    await seed_rule(db, "rule_a", definition=dict(common))
    await seed_rule(
        db, "rule_b", definition={**common, "required_facts": ["never_sent"]}
    )

    results = await run_engine_batch(_event(), db)

    by_status = {r.status for r in results}
    assert len(results) == 2
    assert by_status == {EngineResultStatus.CREATED, EngineResultStatus.MISSING_FACTS}


# ---------------------------------------------------------------------------
# Required facts
# ---------------------------------------------------------------------------


# @verifies REQ-0026
def test_required_facts_are_checked_for_presence_not_for_value():
    """
    A required fact that arrives as `null` or `0` counts as present. Only a key that is
    absent is missing, so a sender that always sends the key and sometimes leaves it
    empty passes this check and reaches the evaluator.
    """
    rule = make_rule(definition={"required_facts": ["a", "b"]})

    assert _validate_required_facts(rule, {"a": 1, "b": 2}) == (True, [])
    assert _validate_required_facts(rule, {"a": None, "b": 0}) == (True, [])
    assert _validate_required_facts(rule, {"a": 1}) == (False, ["b"])
    assert _validate_required_facts(rule, {}) == (False, ["a", "b"])
    assert _validate_required_facts(make_rule(definition={}), {}) == (True, [])


# @verifies REQ-0026
async def test_a_missing_required_fact_stops_the_rule_before_the_evaluator(db, tmp_path):
    """
    @verifies REQ-0017
    """
    exploding = tmp_path / "evaluate.py"
    exploding.write_text("def evaluate(rule, facts):\n    raise AssertionError('never reached')\n")
    await seed_rule(
        db,
        "price_up",
        definition={
            "dedup_window": "daily",
            "scenarios": ["price_up"],
            "required_facts": ["delta_pct"],
            "evaluator_path": str(exploding),
        },
    )

    results = await run_engine_batch(_event(), db)

    assert results[0].status is EngineResultStatus.MISSING_FACTS
    assert results[0].details == {"missing": ["delta_pct"]}
    rows = (await db.execute(select(NudgeLog))).scalars().all()
    assert rows[0].payload["details"] == {"missing": ["delta_pct"]}


# ---------------------------------------------------------------------------
# Language
# ---------------------------------------------------------------------------


# @verifies REQ-0029
def test_a_language_tag_is_reduced_to_its_primary_subtag():
    assert _normalize_lang("IT-ch") == "it"
    assert _normalize_lang("  EN  ") == "en"
    assert _normalize_lang("es") == "es"
    assert _normalize_lang("") is None
    assert _normalize_lang(None) is None
    assert _normalize_lang(42) is None


# @verifies REQ-0029
async def test_the_language_comes_from_the_facts_then_the_preference_then_the_default(db):
    """
    The event wins, because the sender knows the context of this particular message.
    Failing that the stored preference, and failing that `DEFAULT_LANG`.
    """
    db.add(make_preference("user-alice", community_id=None, lang="en"))
    db.add(make_preference("user-bob", community_id="community-1", lang="es"))
    await db.commit()

    assert await _resolve_lang(db, user_id="user-alice", community_id=None, facts={"lang": "IT"}) == "it"
    assert await _resolve_lang(db, user_id="user-bob", community_id="community-1", facts={}) == "es"
    assert await _resolve_lang(db, user_id="user-alice", community_id=None, facts={}) == "en"
    assert await _resolve_lang(db, user_id="nobody", community_id=None, facts={}) == "en"


# @verifies REQ-0029
async def test_a_user_with_both_a_generic_and_a_community_preference_breaks_the_engine(db):
    """
    `_resolve_lang` matches the community-scoped row **or** the generic one and then
    calls `scalar_one_or_none()`, so a participant who has both — which the schema
    permits and `PUT /preferences/me` can produce — raises `MultipleResultsFound`
    before any rule is evaluated. The whole event is lost as a `500`, not one rule.

    The sibling function `get_user_pref` runs the same query with `.first()` and the
    same `ORDER BY`, which is what the ordering was for. Filed as
    https://github.com/celine-eu/nudging-tool/issues/33; this test pins what happens
    today so that fixing it is a visible change.
    """
    from sqlalchemy.exc import MultipleResultsFound

    db.add(make_preference("user-alice", community_id=None, lang="en"))
    db.add(make_preference("user-alice", community_id="community-1", lang="es"))
    await db.commit()

    with pytest.raises(MultipleResultsFound):
        await _resolve_lang(db, user_id="user-alice", community_id="community-1", facts={})

    # The event carries no `lang`, so the engine takes exactly that path.
    with pytest.raises(MultipleResultsFound):
        await run_engine_batch(_event(community_id="community-1"), db)


# @verifies REQ-0030
async def test_a_template_falls_back_to_the_default_language_and_then_to_english(db):
    """
    The chain is the requested language, then `DEFAULT_LANG`, then `en`. A participant
    who asked for Catalan and a rule that was only ever translated into English get the
    English message rather than silence.
    """
    await seed_rule(db, "price_up", langs=("en",), title="EN title")

    _, template = await _load_rule_and_template(db, "price_up", "ca")
    assert template.lang == "en"

    db.add(make_template("price_up", lang="ca", title="CA title"))
    await db.commit()
    _, template = await _load_rule_and_template(db, "price_up", "ca")
    assert template.lang == "ca"


# @verifies REQ-0030
async def test_a_rule_with_no_template_at_all_is_an_error_not_a_silent_send(db):
    """
    A nudge with no message is not a nudge. The `ValueError` is caught one frame up and
    becomes an `unknown_scenario` audit row, which is a poor name for it — but it is
    recorded, and no empty notification is written.
    """
    db.add(make_rule("price_up", definition={"dedup_window": "daily", "scenarios": ["price_up"]}))
    await db.commit()

    with pytest.raises(ValueError, match="No template found"):
        await _load_rule_and_template(db, "price_up", "en")

    results = await run_engine_batch(_event(), db)
    assert results[0].status is EngineResultStatus.UNKNOWN_SCENARIO
    assert "No template found" in results[0].reason
    assert (await db.execute(select(Notification))).scalars().all() == []


# @verifies REQ-0025
async def test_a_disabled_rule_is_not_loaded_even_when_named_directly(db):
    await seed_rule(db, "price_up", enabled=False)

    with pytest.raises(ValueError, match="Rule not found or disabled"):
        await _load_rule_and_template(db, "price_up", "en")


# ---------------------------------------------------------------------------
# Community overrides
# ---------------------------------------------------------------------------


# @verifies REQ-0031
def test_an_override_merges_deeply_rather_than_replacing():
    """
    A community that changes one threshold keeps everything else the rule declares. A
    shallow merge would silently drop `required_facts` and turn a guarded rule into an
    unguarded one.
    """
    base = {"a": 1, "nested": {"x": 1, "y": 2}, "list": [1, 2]}
    override = {"nested": {"y": 99, "z": 3}, "list": [9]}

    assert _deep_merge(base, override) == {
        "a": 1,
        "nested": {"x": 1, "y": 99, "z": 3},
        "list": [9],
    }
    assert base["nested"] == {"x": 1, "y": 2}, "the base is not mutated"


# @verifies REQ-0031
async def test_an_override_applies_only_to_its_own_community(db):
    rule = await seed_rule(
        db, "price_up", definition={"dedup_window": "daily", "threshold_pct": 10}
    )
    db.add(
        RuleOverride(
            rule_id="price_up",
            community_id="community-1",
            definition_override={"threshold_pct": 50},
        )
    )
    await db.commit()

    _, applied = await _apply_rule_override(db, rule, "community-1")
    assert applied == "override_applied"
    assert rule.definition["threshold_pct"] == 50
    assert rule.definition["dedup_window"] == "daily"

    db.expunge_all()
    fresh = await db.get(type(rule), "price_up")
    _, none_applied = await _apply_rule_override(db, fresh, "community-2")
    assert none_applied is None
    assert fresh.definition["threshold_pct"] == 10


# @verifies REQ-0031
async def test_an_event_with_no_community_is_never_overridden(db):
    rule = await seed_rule(db, "price_up", definition={"threshold_pct": 10})
    db.add(
        RuleOverride(
            rule_id="price_up", community_id="community-1", definition_override={"threshold_pct": 50}
        )
    )
    await db.commit()

    _, applied = await _apply_rule_override(db, rule, None)

    assert applied is None
    assert rule.definition["threshold_pct"] == 10


# @verifies REQ-0031
async def test_a_community_can_switch_a_rule_off_and_the_refusal_is_audited(db, tmp_path):
    """
    @verifies REQ-0017

    This is the one suppression that happens inside the engine rather than the
    orchestrator, and it is reported as `not_triggered` — indistinguishable in the
    status from a rule that simply did not match. The audit row's
    `details.reason` is what tells them apart.
    """
    await _seed_triggering_rule(db, tmp_path)
    db.add(RuleOverride(rule_id="price_up", community_id="community-1", enabled_override=False))
    await db.commit()

    results = await run_engine_batch(_event(community_id="community-1"), db)

    assert results[0].status is EngineResultStatus.NOT_TRIGGERED
    assert results[0].reason == "disabled_by_override"
    rows = (await db.execute(select(NudgeLog))).scalars().all()
    assert rows[0].payload["details"] == {"reason": "disabled_by_override"}
    assert (await db.execute(select(Notification))).scalars().all() == []


# @verifies REQ-0031
async def test_the_same_rule_still_fires_for_another_community(db, tmp_path):
    await _seed_triggering_rule(db, tmp_path)
    db.add(RuleOverride(rule_id="price_up", community_id="community-1", enabled_override=False))
    await db.commit()

    results = await run_engine_batch(_event(community_id="community-2"), db)

    assert results[0].status is EngineResultStatus.CREATED
