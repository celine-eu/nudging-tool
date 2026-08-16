"""The evaluators: arbitrary Python, loaded from the seed directory at evaluation time.

Every rule's decision to fire is a `.py` file in `seed/rules/<id>/`, imported by path and
called with the rule and the facts. Nothing type-checks it, nothing lints it as part of
the build, and a file that fails to load is reported as "did not trigger" — which is the
single most convincing reason this repository needed a suite.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from celine.nudging.engine.rules.evaluators import evaluate_rule
from celine.nudging.engine.rules.evaluators import registry
from tests.fakes import make_rule

_REPO_ROOT = Path(__file__).resolve().parents[2]

TRIGGERS = "def evaluate(rule, facts):\n    return True, dict(facts), None\n"
DECLINES = "def evaluate(rule, facts):\n    return False, dict(facts), 'below_threshold'\n"


@pytest.fixture(autouse=True)
def _clear_path_cache():
    """The loader caches by path for the life of the process.

    Two tests writing different code to the same `tmp_path` filename would otherwise see
    whichever ran first. That is also true in production — see
    `.agents/knowledge/an-evaluator-is-cached-for-the-life-of-the-process.md`.
    """
    registry._PATH_CACHE.clear()
    yield
    registry._PATH_CACHE.clear()


# ---------------------------------------------------------------------------
# What an evaluator returns
# ---------------------------------------------------------------------------


# @verifies REQ-0027
def test_an_evaluator_decides_whether_the_rule_fires(tmp_path):
    path = tmp_path / "evaluate.py"
    path.write_text(TRIGGERS)
    triggered, facts, reason = evaluate_rule(
        make_rule(definition={"evaluator_path": str(path)}), {"a": 1}
    )
    assert (triggered, facts, reason) == (True, {"a": 1}, None)

    path.write_text(DECLINES)
    registry._PATH_CACHE.clear()
    triggered, _, reason = evaluate_rule(
        make_rule(definition={"evaluator_path": str(path)}), {"a": 1}
    )
    assert (triggered, reason) == (False, "below_threshold")


# @verifies REQ-0027
def test_an_evaluator_may_rewrite_the_facts_the_message_is_rendered_from(tmp_path):
    """
    The second element of the return value replaces the facts for everything downstream:
    the template context, the notification and the audit row. An evaluator is therefore
    free to compute a display value — and equally free to drop a fact the template needs
    (REQ-0032).
    """
    path = tmp_path / "evaluate.py"
    path.write_text(
        "def evaluate(rule, facts):\n"
        "    out = dict(facts)\n"
        "    out['delta_pct'] = round(facts['raw'] * 100)\n"
        "    return True, out, None\n"
    )

    triggered, facts, _ = evaluate_rule(
        make_rule(definition={"evaluator_path": str(path)}), {"raw": 0.1234}
    )

    assert triggered is True
    assert facts["delta_pct"] == 12


# @verifies REQ-0027
def test_an_evaluator_that_raises_takes_the_whole_event_with_it(tmp_path):
    """
    Load failures are swallowed; *call* failures are not. A `KeyError` on a fact the
    rule forgot to require propagates out of the engine and the sender sees a `500` —
    which, for the one sender that does not ignore the response, is the only loud
    failure mode this service has.
    """
    path = tmp_path / "evaluate.py"
    path.write_text("def evaluate(rule, facts):\n    return True, facts['absent'], None\n")

    with pytest.raises(KeyError):
        evaluate_rule(make_rule(definition={"evaluator_path": str(path)}), {})


# ---------------------------------------------------------------------------
# Where the file is looked for
# ---------------------------------------------------------------------------


# @verifies REQ-0028
def test_a_bare_filename_is_looked_for_beside_its_own_rule(monkeypatch, tmp_path):
    """
    `evaluator_path: evaluate.py` — which is what every seeded rule says — resolves to
    `<SEED_DIR>/rules/<rule id>/evaluate.py`. The rule's **id** is the directory name,
    so renaming a rule id without moving its directory silently stops the rule firing.
    """
    monkeypatch.setattr(registry.settings, "SEED_DIR", str(tmp_path))
    rule_dir = tmp_path / "rules" / "price_up"
    rule_dir.mkdir(parents=True)
    (rule_dir / "evaluate.py").write_text(TRIGGERS)

    triggered, _, _ = evaluate_rule(
        make_rule("price_up", definition={"evaluator_path": "evaluate.py"}), {}
    )

    assert triggered is True


# @verifies REQ-0028
def test_a_path_with_a_directory_in_it_is_relative_to_the_seed_root(monkeypatch, tmp_path):
    monkeypatch.setattr(registry.settings, "SEED_DIR", str(tmp_path))
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "common.py").write_text(TRIGGERS)

    triggered, _, _ = evaluate_rule(
        make_rule("price_up", definition={"evaluator_path": "shared/common.py"}), {}
    )

    assert triggered is True


# @verifies REQ-0028
def test_an_absolute_path_is_taken_as_it_is(tmp_path):
    path = tmp_path / "evaluate.py"
    path.write_text(TRIGGERS)

    triggered, _, _ = evaluate_rule(
        make_rule(definition={"evaluator_path": str(path)}), {}
    )

    assert triggered is True


# @verifies REQ-0027
def test_a_missing_file_reads_as_a_rule_that_did_not_fire(tmp_path):
    """
    This is the failure that looks like success. A typo in `evaluator_path`, a rule
    directory that was not deployed, a file removed by a refactor: all of them produce
    `(False, facts, "evaluator_path_not_found")`, which the engine records as
    `not_triggered` — the same status as a rule that correctly decided to stay quiet.

    The reason string in the audit row is the only difference, and nothing reads it.
    """
    triggered, facts, reason = evaluate_rule(
        make_rule(definition={"evaluator_path": str(tmp_path / "absent.py")}), {"a": 1}
    )

    assert (triggered, reason) == (False, "evaluator_path_not_found")
    assert facts == {"a": 1}


# @verifies REQ-0027
def test_a_file_that_does_not_parse_reads_as_a_rule_that_did_not_fire(tmp_path):
    path = tmp_path / "evaluate.py"
    path.write_text("def evaluate(rule, facts)\n    this is not python\n")

    triggered, _, reason = evaluate_rule(
        make_rule(definition={"evaluator_path": str(path)}), {}
    )

    assert (triggered, reason) == (False, "evaluator_path_not_found")


# @verifies REQ-0027
def test_a_file_with_no_evaluate_function_reads_as_a_rule_that_did_not_fire(tmp_path):
    path = tmp_path / "evaluate.py"
    path.write_text("VALUE = 1\n")

    triggered, _, reason = evaluate_rule(
        make_rule(definition={"evaluator_path": str(path)}), {}
    )

    assert (triggered, reason) == (False, "evaluator_path_not_found")


# ---------------------------------------------------------------------------
# The module form, and no form at all
# ---------------------------------------------------------------------------


# @verifies REQ-0028
def test_an_evaluator_can_be_named_as_an_importable_module():
    """
    `evaluator_module` takes an import path instead of a file. No seeded rule uses it;
    it is pinned because it is the branch that would be reached if one did, and because
    a module that imports cleanly but exports no `evaluate` is the same silent
    non-trigger as the path form.
    """
    triggered, _, reason = evaluate_rule(
        make_rule(definition={"evaluator_module": "tests.evaluator_fixture"}), {}
    )
    assert (triggered, reason) == (True, None)

    triggered, _, reason = evaluate_rule(
        make_rule(definition={"evaluator_module": "json"}), {}
    )
    assert (triggered, reason) == (False, "evaluator_module_not_found")

    triggered, _, reason = evaluate_rule(
        make_rule(definition={"evaluator_module": "no.such.module"}), {}
    )
    assert (triggered, reason) == (False, "evaluator_module_not_found")


# @verifies REQ-0027
def test_a_rule_with_no_evaluator_never_fires():
    """
    There is no default evaluator and no declarative fallback: a rule whose definition
    forgets `evaluator_path` is inert, and reports it as `evaluator_not_configured`.
    """
    triggered, facts, reason = evaluate_rule(make_rule(definition={}), {"a": 1})

    assert (triggered, reason) == (False, "evaluator_not_configured")
    assert facts == {"a": 1}


# @verifies REQ-0028
def test_the_path_form_wins_over_the_module_form(tmp_path):
    path = tmp_path / "evaluate.py"
    path.write_text(TRIGGERS)

    triggered, _, _ = evaluate_rule(
        make_rule(
            definition={
                "evaluator_path": str(path),
                "evaluator_module": "no.such.module",
            }
        ),
        {},
    )

    assert triggered is True


# ---------------------------------------------------------------------------
# The evaluators this repository actually ships
# ---------------------------------------------------------------------------


def _seeded_evaluators() -> list[Path]:
    return sorted((_REPO_ROOT / "seed" / "rules").glob("*/evaluate.py"))


# @verifies REQ-0027
def test_every_seeded_rule_ships_an_evaluator_that_loads_and_answers():
    """
    Every `seed/rules/*/evaluate.py`, imported and called the way the engine calls it.
    A file that stopped parsing would otherwise be discovered as a rule that quietly
    stopped firing, which is exactly the failure nobody in the platform can see.

    The call passes an empty fact set: what is asserted is that the evaluator survives
    it and answers with the three-part contract, not what it decides.
    """
    evaluators = _seeded_evaluators()
    assert evaluators, "no seeded evaluators found — the seed directory moved"

    for path in evaluators:
        rule = make_rule(path.parent.name, definition={"evaluator_path": str(path)})
        result = evaluate_rule(rule, {})

        assert isinstance(result, tuple) and len(result) == 3, path
        triggered, facts, reason = result
        assert isinstance(triggered, bool), path
        assert isinstance(facts, dict), path
        assert reason is None or isinstance(reason, str), path


# @verifies REQ-0028
def test_a_seeded_evaluator_is_resolved_the_way_the_seed_declares_it(monkeypatch):
    """
    The seeded rules say `evaluator_path: evaluate.py`, and the resolution above turns
    that into `seed/rules/<id>/evaluate.py`. This runs the real pairing rather than a
    constructed one, so a change to either the seed layout or the resolution rule fails
    here.
    """
    monkeypatch.setattr(registry.settings, "SEED_DIR", str(_REPO_ROOT / "seed"))

    triggered, _, reason = evaluate_rule(
        make_rule(
            "flexibility_opportunity", definition={"evaluator_path": "evaluate.py"}
        ),
        {},
    )

    assert reason != "evaluator_path_not_found"
    assert triggered is True
