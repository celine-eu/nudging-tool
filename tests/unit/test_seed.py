"""Seeding: how rules, templates and the notification catalogue get into the database.

The seed directory is the product configuration of this service — what a participant can
switch off, what each rule requires, what every message says. It is loaded at startup, by
the CLI, and on every preferences request, so a malformed directory is not a deployment
problem but a request-time one.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from sqlalchemy import select

from celine.nudging.db.models import Rule, RuleOverride, Template, UserPreference
from celine.nudging.db.seed_db import (
    upsert_preference,
    upsert_rule,
    upsert_rule_override,
    upsert_template,
)
from celine.nudging.seed import load_seed_dir, localize_active_kinds, validate_seed
from celine.nudging.seed.loader import validate_rule_definition

_REPO_ROOT = Path(__file__).resolve().parents[2]

ACTIVE_KINDS = {
    "active_kinds": [
        {
            "kind": "flexibility_opportunity",
            "label": {"it": "IT", "en": "EN", "es": "ES"},
            "description": {"it": "IT", "en": "EN", "es": "ES"},
            "cadence": {"it": "IT", "en": "EN", "es": "ES"},
            "rule_ids": ["flexibility_opportunity"],
        }
    ]
}


def _seed_dir(tmp_path: Path, *, active_kinds=ACTIVE_KINDS) -> Path:
    (tmp_path / "active_kinds.yaml").write_text(yaml.safe_dump(active_kinds))
    return tmp_path


def _write_rule(seed_dir: Path, rule_id: str, payload: dict, *, templates: dict | None = None):
    rule_dir = seed_dir / "rules" / rule_id
    rule_dir.mkdir(parents=True, exist_ok=True)
    (rule_dir / "rule.yaml").write_text(yaml.safe_dump(payload))
    for lang, template in (templates or {}).items():
        template_dir = rule_dir / "templates"
        template_dir.mkdir(exist_ok=True)
        (template_dir / f"{lang}.yaml").write_text(yaml.safe_dump(template))


def _rule_payload(rule_id="flexibility_opportunity", **definition) -> dict:
    return {
        "rules": [
            {
                "id": rule_id,
                "name": "A rule",
                "family": "energy",
                "type": "alert",
                "severity": "info",
                "definition": {"kind": "flexibility_opportunity", **definition},
            }
        ]
    }


# ---------------------------------------------------------------------------
# The directory layout
# ---------------------------------------------------------------------------


# @verifies REQ-0068
def test_a_rule_is_a_directory_and_its_directory_name_is_its_id(tmp_path):
    """
    `seed/rules/<id>/rule.yaml`, with the id filled in from the directory when the file
    omits it. The directory name is also where the evaluator is looked for (REQ-0028),
    so the two cannot be separated.
    """
    seed_dir = _seed_dir(tmp_path)
    payload = _rule_payload()
    del payload["rules"][0]["id"]
    _write_rule(seed_dir, "flexibility_opportunity", payload)

    seed = load_seed_dir(seed_dir)

    assert [r["id"] for r in seed.rules] == ["flexibility_opportunity"]


# @verifies REQ-0068
def test_a_template_takes_its_rule_and_language_from_where_it_sits(tmp_path):
    """
    `templates/<lang>.yaml` beside the rule: the filename is the language and the parent
    directory is the rule. Neither has to be repeated inside the file, which is why a
    template moved to the wrong directory is silently a template for another rule.
    """
    seed_dir = _seed_dir(tmp_path)
    _write_rule(
        seed_dir,
        "flexibility_opportunity",
        _rule_payload(),
        templates={"it": {"title_jinja": "Titolo", "body_jinja": "Corpo"}},
    )

    seed = load_seed_dir(seed_dir)

    assert seed.templates == [
        {
            "rule_id": "flexibility_opportunity",
            "lang": "it",
            "title_jinja": "Titolo",
            "body_jinja": "Corpo",
        }
    ]


# @verifies REQ-0071
def test_scenarios_may_be_written_at_the_top_level_and_end_up_in_the_definition(tmp_path):
    """
    An authoring convenience with a consequence: the engine reads `definition.scenarios`
    and `Rule.scenarios`, and `upsert_rule` fills the column from the definition. A rule
    that declares scenarios in *both* places keeps the definition's, because the merge
    does not overwrite what is already there.
    """
    from celine.nudging.seed.schema import RuleSeed

    merged = RuleSeed.model_validate(
        {
            "id": "r",
            "name": "n",
            "family": "energy",
            "type": "alert",
            "severity": "info",
            "scenarios": ["from_top_level"],
            "definition": {"kind": "flexibility_opportunity"},
        }
    )
    assert merged.definition["scenarios"] == ["from_top_level"]

    conflicting = RuleSeed.model_validate(
        {
            "id": "r",
            "name": "n",
            "family": "energy",
            "type": "alert",
            "severity": "info",
            "scenarios": ["from_top_level"],
            "definition": {"kind": "x", "scenarios": ["from_definition"]},
        }
    )
    assert conflicting.definition["scenarios"] == ["from_definition"]


# @verifies REQ-0070
def test_an_unknown_field_in_a_seed_file_is_refused(tmp_path):
    """
    Every seed model forbids extras, so a misspelled key is an error rather than a
    setting that silently does nothing. This is the one place in the service where a
    typo in configuration is loud.
    """
    from celine.nudging.seed.schema import RuleSeed

    with pytest.raises(Exception):
        RuleSeed.model_validate(
            {
                "id": "r",
                "name": "n",
                "family": "energy",
                "type": "alert",
                "severity": "info",
                "dedup_window": "daily",  # belongs inside `definition`
            }
        )


# ---------------------------------------------------------------------------
# active_kinds.yaml
# ---------------------------------------------------------------------------


# @verifies REQ-0069
def test_the_catalogue_file_is_mandatory(tmp_path):
    """
    Without it there is no list of kinds, so `get_enabled_notification_kinds` would
    return nothing and **every** notification would be suppressed as an unknown kind.
    Raising is the safer failure: it takes down the preferences endpoint instead.
    """
    with pytest.raises(ValueError, match="Missing active kinds file"):
        load_seed_dir(tmp_path)


# @verifies REQ-0069
def test_a_catalogue_that_is_not_a_list_is_refused(tmp_path):
    (tmp_path / "active_kinds.yaml").write_text(yaml.safe_dump({"kinds": []}))
    with pytest.raises(ValueError, match="Invalid active kinds structure"):
        load_seed_dir(tmp_path)

    (tmp_path / "active_kinds.yaml").write_text("")
    with pytest.raises(ValueError, match="payload is empty"):
        load_seed_dir(tmp_path)


# @verifies REQ-0069
def test_a_catalogue_entry_missing_a_translation_is_refused(tmp_path):
    """
    `label`, `description` and `cadence` are required in Italian, English and Spanish.
    Catalan is *not* required, though `/preferences` accepts `ca` — a Catalan reader
    falls back to English (REQ-0075).
    """
    seed_dir = _seed_dir(
        tmp_path,
        active_kinds={
            "active_kinds": [
                {
                    "kind": "k",
                    "label": {"en": "EN"},
                    "description": {"it": "IT", "en": "EN", "es": "ES"},
                    "cadence": {"it": "IT", "en": "EN", "es": "ES"},
                }
            ]
        },
    )

    _, errors = validate_seed(load_seed_dir(seed_dir))

    assert "active_kinds[0].label.it is required" in errors
    assert "active_kinds[0].label.es is required" in errors


# @verifies REQ-0070
def test_a_repeated_kind_is_dropped_on_load_and_reported_on_validation(tmp_path):
    """
    Loading keeps the first and discards the rest, so the service starts; validation —
    which the CLI runs and the startup path does not — names it. A duplicate is
    therefore invisible in production and visible in `nudging-cli seed apply`.
    """
    entry = dict(ACTIVE_KINDS["active_kinds"][0])
    seed_dir = _seed_dir(tmp_path, active_kinds={"active_kinds": [entry, dict(entry)]})

    seed = load_seed_dir(seed_dir)
    assert len(seed.active_kinds) == 1

    _, errors = validate_seed(
        type(seed)(
            rules=[],
            templates=[],
            preferences=[],
            overrides=[],
            active_kinds=[entry, dict(entry)],
        )
    )
    assert any("duplicates" in error for error in errors)


# @verifies REQ-0075
def test_the_catalogue_is_localised_with_english_as_the_fallback():
    """
    Three i18n maps collapse to three strings for one reader. An unknown language falls
    back to English, and a kind with no English falls back to whichever translation
    happens to be first — which is a guess, but a message in the wrong language beats an
    empty label in a preferences screen.
    """
    kinds = [
        {
            "kind": "k",
            "label": {"it": "Etichetta", "en": "Label", "es": "Etiqueta"},
            "description": {"it": "D-IT", "en": "D-EN", "es": "D-ES"},
            "cadence": {"it": "C-IT", "en": "C-EN", "es": "C-ES"},
        }
    ]

    assert localize_active_kinds(kinds, "it")[0]["label"] == "Etichetta"
    assert localize_active_kinds(kinds, "ca")[0]["label"] == "Label"

    only_italian = [{**kinds[0], "label": {"it": "Solo"}}]
    assert localize_active_kinds(only_italian, "en")[0]["label"] == "Solo"


# @verifies REQ-0069
def test_the_shipped_catalogue_loads_and_validates():
    """
    The real `seed/` directory, loaded the way the service loads it. Every preferences
    request parses these files, so a broken one is a `500` on a screen a participant
    opens — not a deployment failure somebody notices first.
    """
    seed = load_seed_dir(_REPO_ROOT / "seed")
    validated, errors = validate_seed(seed)

    assert errors == []
    assert validated.active_kinds, "the shipped catalogue is not empty"
    assert validated.rules, "the shipped rules are not empty"


# @verifies REQ-0069
def test_every_shipped_rule_id_named_by_the_catalogue_exists():
    """
    `active_kinds.yaml` lists the rules each kind covers. Nothing enforces the link —
    suppression matches on `definition.kind`, not on this list — so an id here that
    names no rule is a documentation error the code cannot see, and the reverse (a rule
    whose kind is not in the catalogue) means that rule can never be switched off.
    """
    seed = load_seed_dir(_REPO_ROOT / "seed")
    rule_ids = {rule["id"] for rule in seed.rules}

    named = {rid for kind in seed.active_kinds for rid in kind.get("rule_ids", [])}
    missing = sorted(named - rule_ids)

    assert missing == ["sensor_not_working"], (
        "the catalogue names a rule that is not seeded; if that is intended, this list "
        "is where it is recorded"
    )


# @verifies REQ-0036
def test_most_shipped_rules_are_of_a_kind_no_participant_can_switch_off():
    """
    @verifies REQ-0069

    The catalogue lists **three** kinds. Every other kind a rule declares is matched
    against nobody's enabled list, and `orchestrate` suppresses only when the rule's kind
    is *known and not enabled* — so a rule of an uncatalogued kind is always delivered,
    whatever the participant chose.

    That is a product decision nobody wrote down, and it is the reason this list is
    pinned: adding a rule of a new kind is a decision about whether it can be refused,
    and this test is where that decision has to be made explicitly.
    """
    seed = load_seed_dir(_REPO_ROOT / "seed")
    catalogued = {kind["kind"] for kind in seed.active_kinds}

    assert sorted(catalogued) == ["extr_event", "flexibility_opportunity", "meter_anomaly"]

    always_delivered = sorted(
        {
            rule["definition"].get("kind")
            for rule in seed.rules
            if rule["definition"].get("kind") not in catalogued
        }
    )
    assert always_delivered == [
        "commitment_settled",
        "flexibility_committed",
        "imported_down",
        "imported_up",
        "kpi_conditions",
        "price_down",
        "price_up",
        "static_message",
        "sunny_cons",
        "sunny_pros",
    ]


# ---------------------------------------------------------------------------
# Definition validation
# ---------------------------------------------------------------------------


# @verifies REQ-0074
def test_a_definition_must_name_a_kind():
    assert validate_rule_definition({}) == ["definition.kind is required"]
    assert validate_rule_definition({"kind": ""}) == ["definition.kind is required"]
    assert validate_rule_definition("not a dict") == ["definition must be an object"]


# @verifies REQ-0074
def test_an_unknown_kind_is_allowed_and_skips_the_rest_of_the_checks(caplog):
    """
    Strict validation applies only to kinds the catalogue lists. An unknown kind is
    warned about and then *returned early*, so its `required_facts` and `scenarios` are
    never checked — a rule can be seeded with a malformed definition simply by naming a
    kind nobody declared.
    """
    from celine.nudging.seed import loader

    loader.KNOWN_KINDS.clear()
    loader.KNOWN_KINDS.add("flexibility_opportunity")

    assert validate_rule_definition({"kind": "invented", "required_facts": "not-a-list"}) == []
    assert validate_rule_definition(
        {"kind": "flexibility_opportunity", "required_facts": "not-a-list"}
    ) == ["definition.required_facts must be a list of strings"]


# @verifies REQ-0074
def test_kpi_conditions_are_checked_field_by_field():
    """
    Seventeen of the shipped rules are `kpi_conditions`, so this is the most-used shape
    in the seed. The operators are a closed set; a condition with an unknown operator or
    no value would be a rule that never fires.
    """
    from celine.nudging.seed import loader

    loader.KNOWN_KINDS.add("kpi_conditions")

    assert validate_rule_definition(
        {"kind": "kpi_conditions", "conditions": [{"fact_key": "a", "op": ">=", "value": 1}]}
    ) == []

    errors = validate_rule_definition(
        {"kind": "kpi_conditions", "conditions": [{"op": "≥", "value": 1}]}
    )
    assert "definition.conditions[0].fact_key is required" in errors
    assert any("must be one of" in error for error in errors)

    assert validate_rule_definition({"kind": "kpi_conditions", "conditions": []}) == [
        "definition.conditions must be a non-empty list"
    ]


# ---------------------------------------------------------------------------
# Writing it to the database
# ---------------------------------------------------------------------------


# @verifies REQ-0072
async def test_seeding_the_same_rule_twice_updates_it_in_place(db):
    """
    Every upsert is keyed on the logical identity, not on a generated id, so startup
    seeding is safe to repeat — which it must be, because it runs on every boot of every
    replica.
    """
    payload = {
        "id": "price_up",
        "name": "First name",
        "family": "energy",
        "type": "alert",
        "severity": "info",
        "definition": {"kind": "price_up", "scenarios": ["price_up"]},
    }
    await upsert_rule(db, payload)
    await db.commit()

    await upsert_rule(db, {**payload, "name": "Second name", "enabled": False})
    await db.commit()

    rules = (await db.execute(select(Rule))).scalars().all()
    assert len(rules) == 1
    assert rules[0].name == "Second name"
    assert rules[0].enabled is False
    assert rules[0].scenarios == ["price_up"], "the column is filled from the definition"


# @verifies REQ-0072
async def test_a_template_is_identified_by_its_rule_and_language(db):
    """
    @verifies REQ-0073

    The id is derived — `tpl_<rule>_<lang>` — so the same seed produces the same ids in
    every environment, and re-seeding a translation replaces it rather than adding a
    second row that the engine would then pick between arbitrarily.
    """
    await upsert_template(
        db, {"rule_id": "price_up", "lang": "en", "title_jinja": "T1", "body_jinja": "B1"}
    )
    await upsert_template(
        db, {"rule_id": "price_up", "lang": "it", "title_jinja": "T-IT", "body_jinja": "B-IT"}
    )
    await db.commit()

    await upsert_template(
        db, {"rule_id": "price_up", "lang": "en", "title_jinja": "T2", "body_jinja": "B2"}
    )
    await db.commit()

    templates = {t.lang: t for t in (await db.execute(select(Template))).scalars().all()}
    assert len(templates) == 2
    assert templates["en"].id == "tpl_price_up_en"
    assert templates["en"].title_jinja == "T2"


# @verifies REQ-0072
async def test_a_preference_is_identified_by_the_participant_and_the_community(db):
    await upsert_preference(db, {"user_id": "user-alice", "max_per_day": 5})
    await upsert_preference(
        db, {"user_id": "user-alice", "community_id": "c1", "max_per_day": 9}
    )
    await db.commit()

    await upsert_preference(db, {"user_id": "user-alice", "max_per_day": 7})
    await db.commit()

    rows = {p.community_id: p for p in (await db.execute(select(UserPreference))).scalars()}
    assert len(rows) == 2
    assert rows[None].max_per_day == 7
    assert rows["c1"].max_per_day == 9


# @verifies REQ-0072
async def test_a_seeded_preference_keeps_its_language_when_the_seed_omits_one(db):
    await upsert_preference(db, {"user_id": "user-alice", "lang": "it"})
    await db.commit()

    await upsert_preference(db, {"user_id": "user-alice", "max_per_day": 4})
    await db.commit()

    assert (await db.execute(select(UserPreference))).scalar_one().lang == "it"


# @verifies REQ-0072
async def test_an_override_is_identified_by_the_rule_and_the_community(db):
    """
    Absent keys are left alone rather than reset, so an override that only carries
    `enabled_override` does not wipe a `definition_override` seeded earlier.
    """
    await upsert_rule_override(
        db,
        {"rule_id": "price_up", "community_id": "c1", "definition_override": {"threshold_pct": 50}},
    )
    await db.commit()

    await upsert_rule_override(db, {"rule_id": "price_up", "community_id": "c1", "enabled_override": False})
    await db.commit()

    override = (await db.execute(select(RuleOverride))).scalar_one()
    assert override.enabled_override is False
    assert override.definition_override == {"threshold_pct": 50}
