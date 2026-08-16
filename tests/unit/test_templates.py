"""Rendering, which is the last step before a real person reads the result.

The renderer is three lines of Jinja2 with no environment, no autoescape and no undefined
policy. Everything below is a property of that default, and every one of them reaches an
inbox or a lock screen.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from jinja2 import Template as JinjaTemplate
from sqlalchemy import select

from celine.nudging.db.models import Notification
from celine.nudging.engine.engine_service import run_engine_batch
from celine.nudging.engine.rules.models import DigitalTwinEvent
from celine.nudging.engine.templates.renderer import render
from tests.fakes import seed_rule

_REPO_ROOT = Path(__file__).resolve().parents[2]
DAILY = {"facts_version": "1", "scenario": "price_up", "time": "2026-08-15"}


async def _seed(db, *, title="Title", body="Body"):
    evaluator = _REPO_ROOT / "seed" / "rules" / "flexibility_opportunity" / "evaluate.py"
    return await seed_rule(
        db,
        "price_up",
        title=title,
        body=body,
        definition={
            "dedup_window": "daily",
            "scenarios": ["price_up"],
            "evaluator_path": str(evaluator),
        },
    )


def _event(facts=None, *, community_id=None):
    return DigitalTwinEvent(
        event_type="dt.metrics",
        user_id="user-alice",
        community_id=community_id,
        facts={**DAILY, **(facts or {})},
    )


# ---------------------------------------------------------------------------
# The renderer
# ---------------------------------------------------------------------------


# @verifies REQ-0032
def test_the_title_and_the_body_are_rendered_from_the_same_context():
    title, body = render("Price {{ direction }}", "Up by {{ delta_pct }}%", {"direction": "up", "delta_pct": 12})

    assert title == "Price up"
    assert body == "Up by 12%"


# @verifies REQ-0032
def test_an_undefined_variable_renders_as_nothing_at_all():
    """
    Jinja2's default `Undefined` prints as the empty string, so a template naming a fact
    the evaluator did not produce sends **"You saved  kWh this month"** to a participant.
    Nothing raises, nothing is logged, and the notification is stored and delivered.

    A `StrictUndefined` environment would turn this into a loud failure. That is a
    change to the service, not to the test — filed as
    https://github.com/celine-eu/nudging-tool/issues/35. This test states what happens
    today.
    """
    title, body = render("Hello {{ name }}", "You saved {{ saved_kwh }} kWh", {})

    assert title == "Hello "
    assert body == "You saved  kWh"


# @verifies REQ-0032
def test_an_attribute_of_an_undefined_variable_does_raise():
    """
    The undefined value is forgiving until something is *done* to it. `{{ a.b }}` and
    `{{ a | int }}` raise, so the same missing fact is silent in one template and a 500
    in another, depending on how the sentence happens to be written.
    """
    with pytest.raises(Exception):
        render("{{ missing.attribute }}", "", {})

    assert render("{{ missing }}", "", {})[0] == ""


# @verifies REQ-0032
def test_nothing_is_escaped():
    """
    Autoescape is off, which is right for a plain-text push body and a risk for the
    email one — `EmailMessage.set_content` sends it as `text/plain`, so this is markup a
    reader sees rather than markup a reader runs. It is pinned because switching the
    email publisher to HTML would silently make it the other thing.
    """
    title, body = render("{{ v }}", "{{ v }}", {"v": "<b>bold</b> & <script>"})

    assert title == "<b>bold</b> & <script>"
    assert body == "<b>bold</b> & <script>"


# @verifies REQ-0032
def test_a_template_is_compiled_on_every_render():
    """
    `JinjaTemplate(...)` is constructed per call rather than cached, so a template
    changed by a re-seed takes effect on the next notification with no restart. The cost
    is a compile per delivery.
    """
    first = render("{{ v }}", "", {"v": 1})
    second = render("{{ v }}", "", {"v": 2})

    assert (first[0], second[0]) == ("1", "2")


# ---------------------------------------------------------------------------
# The context the engine builds
# ---------------------------------------------------------------------------


# @verifies REQ-0032
async def test_the_context_carries_the_facts_the_identity_and_the_time(db):
    """
    A template may name any fact, plus `now`, `user_id` and `community_id`. `now` is the
    render time in UTC ISO-8601 — not the event's time, which is `time`/`date`/`period`.
    """
    await _seed(
        db,
        title="{{ user_id }} in {{ community_id }}",
        body="{{ delta_pct }}% on {{ date }}, rendered {{ now[:4] }}",
    )

    [result] = await run_engine_batch(_event({"delta_pct": 12}, community_id="c1"), db)

    assert result.nudge.title == "user-alice in c1"
    assert result.nudge.body.startswith("12% on 2026-08-15, rendered ")
    assert result.nudge.render_context["user_id"] == "user-alice"
    assert result.nudge.render_context["community_id"] == "c1"
    assert result.nudge.render_context["now"].endswith("+00:00")


# @verifies REQ-0032
async def test_a_fact_may_shadow_the_identity_the_engine_put_in_the_context(db):
    """
    The facts are merged **after** `user_id` and `community_id`, so a sender that puts a
    `user_id` in its facts renders that one into the message while the notification is
    stored against the real recipient. The message can therefore address the wrong
    person while being delivered to the right one.
    """
    await _seed(db, title="Hello {{ user_id }}", body="-")

    [result] = await run_engine_batch(_event({"user_id": "somebody-else"}), db)

    assert result.nudge.title == "Hello somebody-else"
    assert result.nudge.user_id == "user-alice"

    stored = (await db.execute(select(Notification))).scalar_one()
    assert stored.user_id == "user-alice"
    assert stored.title == "Hello somebody-else"


# @verifies REQ-0032
async def test_the_rendered_message_is_what_is_stored(db):
    """
    Rendering happens once, at creation. The notification row holds text, not a template
    and a context, so a later change to the template does not rewrite history — and a
    message rendered from the wrong facts cannot be repaired by re-seeding.
    """
    await _seed(db, title="Saved {{ saved_kwh }} kWh", body="On {{ date }}")

    [result] = await run_engine_batch(_event({"saved_kwh": 4.5}), db)

    stored = (await db.execute(select(Notification))).scalar_one()
    assert (stored.title, stored.body) == ("Saved 4.5 kWh", "On 2026-08-15")
    assert (stored.title, stored.body) == (result.nudge.title, result.nudge.body)


# @verifies REQ-0027
async def test_the_message_is_rendered_from_the_evaluator_s_facts_not_the_sender_s(
    db, tmp_path
):
    """
    @verifies REQ-0032
    """
    evaluator = tmp_path / "evaluate.py"
    evaluator.write_text(
        "def evaluate(rule, facts):\n"
        "    out = dict(facts)\n"
        "    out['delta_pct'] = 99\n"
        "    return True, out, None\n"
    )
    await seed_rule(
        db,
        "price_up",
        title="{{ delta_pct }}%",
        body="-",
        definition={
            "dedup_window": "daily",
            "scenarios": ["price_up"],
            "evaluator_path": str(evaluator),
        },
    )

    [result] = await run_engine_batch(_event({"delta_pct": 1}), db)

    assert result.nudge.title == "99%"


# ---------------------------------------------------------------------------
# The templates this repository ships
# ---------------------------------------------------------------------------


def _yaml_items(path: Path) -> list[dict]:
    import yaml

    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if isinstance(payload, dict) and isinstance(payload.get("rules"), list):
        return payload["rules"]
    if isinstance(payload, list):
        return payload
    return [payload] if isinstance(payload, dict) else []


def _seeded_templates() -> list[tuple[Path, str, str]]:
    out: list[tuple[Path, str, str]] = []
    for path in sorted((_REPO_ROOT / "seed" / "rules").glob("*/templates/*.y*ml")):
        for item in _yaml_items(path):
            if isinstance(item, dict) and "title_jinja" in item:
                out.append((path, item["title_jinja"], item["body_jinja"]))
    return out


def _required_facts(rule_dir: Path) -> list[str]:
    required: list[str] = []
    for item in _yaml_items(rule_dir / "rule.yaml"):
        required.extend((item.get("definition") or {}).get("required_facts") or [])
    return required


# @verifies REQ-0032
def test_every_seeded_template_compiles():
    """
    A template that does not compile raises at delivery time, inside the request that
    ingested the event — long after whoever wrote it has moved on. Compiling all of them
    here costs milliseconds.
    """
    templates = _seeded_templates()
    assert templates, "no seeded templates found — the seed layout moved"

    for path, title, body in templates:
        JinjaTemplate(title)
        JinjaTemplate(body)


# @verifies REQ-0026
def test_every_seeded_template_renders_from_its_own_rule_s_required_facts():
    """
    @verifies REQ-0032

    This is what `required_facts` is *for*, and the pairing is not enforced anywhere:
    the list is declared in `rule.yaml` and the variables are named in the templates
    beside it, with nothing checking that the first covers the second.

    It happens to hold today for all of them — which is why the check is worth keeping.
    Thirty of these templates raise on an empty context (they round, divide or compare
    an undefined value), so for those rules `required_facts` is the only thing standing
    between a sender's omission and a `500` that loses the whole event.
    """
    templates = _seeded_templates()
    assert templates, "no seeded templates found — the seed layout moved"

    engine_context = {
        "now": "2026-08-15T00:00:00+00:00",
        "user_id": "user-alice",
        "community_id": "community-1",
        "time": "2026-08",
        "date": "2026-08-15",
        "week": "2026-W33",
        "period": "2026-08",
    }

    for path, title, body in templates:
        context = {key: 1 for key in _required_facts(path.parent.parent)}
        try:
            render(title, body, {**engine_context, **context})
        except Exception as exc:  # pragma: no cover - the failure message is the point
            pytest.fail(f"{path} needs a fact its rule does not require: {exc!r}")


# @verifies REQ-0026
def test_a_template_that_computes_with_a_missing_fact_takes_the_event_down():
    """
    The other side of the test above, stated as behaviour rather than as a survey: an
    undefined value is silent when printed and fatal when used. A rule that names a
    fact in its template and forgets it in `required_facts` is a `500` waiting for the
    first sender that omits it.
    """
    with pytest.raises(Exception):
        render("", "{{ (a / b) | round(1) }}", {})
