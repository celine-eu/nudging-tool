"""`POST /admin/ingest-event` — the door every other service comes through.

The status codes are the contract, and **no sender reads them**: `../flexibility-api`
treats scheduling a nudge as best-effort and logs a failure without failing the action
that triggered it. So each code below is documentation for a human reading a log, and
the tests are the only thing keeping them true.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select

from celine.nudging.db.models import DeliveryLog, Notification, NudgeLog
from tests.fakes import make_preference, seed_rule

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ALWAYS_TRIGGERS = _REPO_ROOT / "seed" / "rules" / "flexibility_opportunity" / "evaluate.py"

DAILY = {"facts_version": "1", "scenario": "price_up", "time": "2026-08-15"}


def _event(facts: dict | None = None, **kwargs) -> dict:
    return {
        "event_type": "dt.metrics",
        "user_id": "user-alice",
        "facts": {**DAILY, **(facts or {})},
        **kwargs,
    }


@pytest.fixture
async def seeded(db):
    """A rule that always triggers, with an English template."""
    await seed_rule(
        db,
        "price_up",
        title="Price up",
        body="By {{ delta_pct }}%",
        definition={
            "dedup_window": "daily",
            "scenarios": ["price_up"],
            "evaluator_path": str(_ALWAYS_TRIGGERS),
        },
    )
    return db


# ---------------------------------------------------------------------------
# Who may ingest
# ---------------------------------------------------------------------------


# @verifies REQ-0018
async def test_a_participant_may_not_ingest(user_client, seeded):
    response = await user_client.post("/admin/ingest-event", json=_event())

    assert response.status_code == 403
    assert "nudging.ingest" in response.json()["detail"]


# @verifies REQ-0018
async def test_the_sender_service_account_may_ingest(ingest_client, seeded, webpush):
    response = await ingest_client.post("/admin/ingest-event", json=_event())

    assert response.status_code == 200


# @verifies REQ-0018
async def test_an_administrator_may_ingest_too(admin_client, seeded, webpush):
    response = await admin_client.post("/admin/ingest-event", json=_event())

    assert response.status_code == 200


# @verifies REQ-0002
async def test_ingesting_without_a_token_is_401(client, seeded):
    assert (await client.post("/admin/ingest-event", json=_event())).status_code == 401


# ---------------------------------------------------------------------------
# What a well-formed event produces
# ---------------------------------------------------------------------------


# @verifies REQ-0016
async def test_a_triggered_rule_answers_200_with_its_deliveries(ingest_client, seeded, webpush, db):
    """
    The response names the nudge, the rule and every delivery job built for it — the
    only place the platform ever reports what a nudge became.
    """
    from tests.fakes import make_notification  # noqa: F401  (documents the shape below)

    db.add(make_preference("user-alice", channel_email=True, email="alice@example.test"))
    await db.commit()

    response = await ingest_client.post(
        "/admin/ingest-event", json=_event({"delta_pct": 12})
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert len(body["created"]) == 1
    assert body["created"][0]["rule_id"] == "price_up"
    assert [d["channel"] for d in body["created"][0]["deliveries"]] == ["web", "email"]
    assert body["suppressed"] == []

    notification = (await db.execute(select(Notification))).scalar_one()
    assert (notification.title, notification.body) == ("Price up", "By 12%")


# @verifies REQ-0016
async def test_a_rule_that_declines_answers_204_and_writes_no_notification(
    ingest_client, db, tmp_path
):
    """
    No content, because there is nothing to report: the rule looked and decided this was
    not worth a person's attention. The audit row is still written.
    """
    declines = tmp_path / "evaluate.py"
    declines.write_text("def evaluate(rule, facts):\n    return False, dict(facts), 'quiet'\n")
    await seed_rule(
        db,
        "price_up",
        definition={
            "dedup_window": "daily",
            "scenarios": ["price_up"],
            "evaluator_path": str(declines),
        },
    )

    response = await ingest_client.post("/admin/ingest-event", json=_event())

    assert response.status_code == 204
    assert (await db.execute(select(Notification))).scalars().all() == []
    assert (await db.execute(select(NudgeLog))).scalar_one().status == "not_triggered"


# @verifies REQ-0016
async def test_a_second_identical_event_answers_409(ingest_client, seeded, webpush):
    """
    @verifies REQ-0035

    The sender is told, in the one status code that says "this is not new". Whether
    anything acts on it is another matter — nothing does.
    """
    assert (await ingest_client.post("/admin/ingest-event", json=_event())).status_code == 200

    response = await ingest_client.post("/admin/ingest-event", json=_event())

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["error"] == "suppressed"
    assert detail["reason"] == "all_rules_dedup"


# @verifies REQ-0016
async def test_everything_created_but_nothing_delivered_answers_202(
    ingest_client, seeded, db
):
    """
    @verifies REQ-0040

    The distinction between `200` and `202` is the whole point of this service: the
    notification exists and a participant will see it in their list, but no channel took
    it. A sender that treats `202` as failure would retry something that worked.
    """
    db.add(make_preference("user-alice", max_per_day=0))
    await db.commit()

    response = await ingest_client.post("/admin/ingest-event", json=_event())

    assert response.status_code == 202
    body = response.json()
    assert body == {
        "status": "accepted",
        "delivery": "suppressed",
        "created": body["created"],
        "suppressed": [],
    }
    assert (await db.execute(select(Notification))).scalar_one().status == "suppressed"
    assert (await db.execute(select(DeliveryLog))).scalar_one().error == "rate_limited"


# @verifies REQ-0016
async def test_an_unknown_scenario_answers_400(ingest_client, seeded):
    response = await ingest_client.post(
        "/admin/ingest-event", json=_event({"scenario": "nobody_claims_this"})
    )

    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "unknown_scenario"


# @verifies REQ-0011
async def test_an_event_with_no_facts_answers_422(ingest_client, seeded):
    response = await ingest_client.post(
        "/admin/ingest-event", json={"event_type": "dt.metrics", "user_id": "user-alice"}
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "Missing facts in DT event"


# @verifies REQ-0012
async def test_facts_missing_the_contract_answer_422_before_any_rule_runs(
    ingest_client, seeded, db
):
    """
    Refused at the edge, so no audit row is written either — the endpoint's own
    validator runs before the engine's. An event rejected here leaves **no trace at
    all**, which is the one contract violation this service cannot show you afterwards.
    """
    response = await ingest_client.post(
        "/admin/ingest-event", json=_event({"facts_version": None, "scenario": None})
    )

    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "invalid_facts_contract"
    assert (await db.execute(select(NudgeLog))).scalars().all() == []


# @verifies REQ-0026
async def test_a_missing_required_fact_answers_422_with_the_names(
    ingest_client, db, tmp_path
):
    """
    @verifies REQ-0016
    """
    await seed_rule(
        db,
        "price_up",
        definition={
            "dedup_window": "daily",
            "scenarios": ["price_up"],
            "required_facts": ["delta_pct", "threshold"],
            "evaluator_path": str(_ALWAYS_TRIGGERS),
        },
    )

    response = await ingest_client.post("/admin/ingest-event", json=_event())

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["error"] == "missing_required_facts"
    assert detail["results"][0]["details"] == {"missing": ["delta_pct", "threshold"]}


# @verifies REQ-0021
async def test_an_unusable_time_value_answers_422(ingest_client, seeded):
    response = await ingest_client.post(
        "/admin/ingest-event", json=_event({"time": "15/08/2026"})
    )

    assert response.status_code == 422
    assert response.json()["detail"]["results"][0]["reason"] == "missing_or_invalid_time_scope"


# ---------------------------------------------------------------------------
# Several rules at once
# ---------------------------------------------------------------------------


# @verifies REQ-0019
async def test_one_event_can_produce_several_notifications(ingest_client, db, webpush):
    for rule_id in ("price_up_alert", "price_up_digest"):
        await seed_rule(
            db,
            rule_id,
            definition={
                "dedup_window": "daily",
                "scenarios": ["price_up"],
                "evaluator_path": str(_ALWAYS_TRIGGERS),
            },
        )

    response = await ingest_client.post("/admin/ingest-event", json=_event())

    assert response.status_code == 200
    assert len(response.json()["created"]) == 2
    assert len((await db.execute(select(Notification))).scalars().all()) == 2


# @verifies REQ-0019
async def test_a_partly_suppressed_event_reports_both_halves(
    ingest_client, db, webpush, tmp_path
):
    """
    One rule fired and one declined: the response is a `200` naming what was created and
    what was not. A sender reading only the status code cannot tell that half of what it
    asked for did not happen.
    """
    declines = tmp_path / "evaluate.py"
    declines.write_text("def evaluate(rule, facts):\n    return False, dict(facts), 'quiet'\n")
    await seed_rule(
        db,
        "fires",
        definition={
            "dedup_window": "daily",
            "scenarios": ["price_up"],
            "evaluator_path": str(_ALWAYS_TRIGGERS),
        },
    )
    await seed_rule(
        db,
        "declines",
        definition={
            "dedup_window": "daily",
            "scenarios": ["price_up"],
            "evaluator_path": str(declines),
        },
    )

    response = await ingest_client.post("/admin/ingest-event", json=_event())

    body = response.json()
    assert response.status_code == 200
    assert [c["rule_id"] for c in body["created"]] == ["fires"]
    assert [s["reason"] for s in body["suppressed"]] == ["quiet"]


# ---------------------------------------------------------------------------
# Events addressed to email rather than to a participant
# ---------------------------------------------------------------------------


# @verifies REQ-0013
async def test_an_event_with_neither_a_participant_nor_a_recipient_is_422(
    ingest_client, seeded
):
    response = await ingest_client.post(
        "/admin/ingest-event", json={"event_type": "dt.metrics", "facts": dict(DAILY)}
    )

    assert response.status_code == 422
    assert response.json()["detail"] == {
        "error": "missing_target",
        "reason": "user_id_or_email_recipients_required",
    }


# @verifies REQ-0014
async def test_an_email_only_event_is_delivered_to_the_addresses_it_names(
    ingest_client, seeded, smtp, db
):
    """
    @verifies REQ-0042

    Used by `../celine-grid` to alert a DSO operator who has no account here. The
    synthetic user id is derived from the addresses, so the same recipients always
    resolve to the same participant — which is what makes deduplication work for a
    person the service has never met.
    """
    response = await ingest_client.post(
        "/admin/ingest-event",
        json={
            "event_type": "grid.alert",
            "facts": {**DAILY, "email_recipients": ["ops@example.test", "duty@example.test"]},
        },
    )

    assert response.status_code == 200
    assert sorted(smtp.recipients) == ["duty@example.test", "ops@example.test"]

    log = (await db.execute(select(NudgeLog))).scalar_one()
    assert log.user_id.startswith("email-ingest:")

    deliveries = (await db.execute(select(DeliveryLog))).scalars().all()
    assert {d.channel for d in deliveries} == {"email"}, "no web push for a synthetic user"


# @verifies REQ-0014
async def test_the_synthetic_participant_is_the_same_whatever_the_order_or_case(
    ingest_client, seeded, smtp, db
):
    """
    Sorted and lower-cased before hashing, so `[A, B]` and `[b, a]` are one recipient
    set. Without that, a sender reordering its list would defeat deduplication and send
    the same alert twice.
    """
    first = await ingest_client.post(
        "/admin/ingest-event",
        json={"event_type": "grid.alert", "facts": {**DAILY, "email_recipients": ["A@example.test", "b@example.test"]}},
    )
    second = await ingest_client.post(
        "/admin/ingest-event",
        json={"event_type": "grid.alert", "facts": {**DAILY, "email_recipients": ["B@example.test", "a@example.test"]}},
    )

    assert first.status_code == 200
    assert second.status_code == 409, "the second is the same recipients on the same day"

    user_ids = {row.user_id for row in (await db.execute(select(NudgeLog))).scalars()}
    assert len(user_ids) == 1


# @verifies REQ-0043
async def test_an_event_naming_only_unparseable_addresses_is_422(ingest_client, seeded):
    """
    The filter runs before the fallback, so a list of typos is indistinguishable from an
    empty one and the event is refused for having no target at all. That is the *loud*
    case; a list where only some addresses are typos is the silent one (REQ-0043).
    """
    response = await ingest_client.post(
        "/admin/ingest-event",
        json={"event_type": "grid.alert", "facts": {**DAILY, "email_recipients": ["not-an-email"]}},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "missing_target"
