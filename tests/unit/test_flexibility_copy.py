"""Flexibility templates promise points as "if you keep the commitment", never "up to".

Under the fair gamification (demo3 spec 2026-10-07, design 2026-10-09) a kept commitment
is worth a fixed number of points; the senders put that number in `reward_points*`. The
same wording also reads correctly while the points variant still sends its estimate.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

_RULES = Path(__file__).resolve().parents[2] / "seed" / "rules"

KEEP_WORDING = {"it": "se mantieni", "en": "if you keep", "es": "si cumples"}
BANNED = {"it": ("fino a",), "en": ("up to",), "es": ("hasta",)}


def _templates(rule: str) -> dict[str, dict]:
    out = {}
    for path in sorted((_RULES / rule / "templates").glob("*.yaml")):
        out[path.stem] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return out


@pytest.mark.parametrize("rule,var", [("flexibility_reminder", "reward_points_estimated"), ("flexibility_opportunity", "reward_points")])
def test_offers_promise_points_only_for_a_kept_commitment(rule, var):
    for lang, tpl in _templates(rule).items():
        text = (tpl["title_jinja"] + " " + tpl["body_jinja"]).lower()
        assert f"{{{{ {var} }}}}" in text, (rule, lang)
        assert KEEP_WORDING[lang] in text, (rule, lang)
        for word in BANNED[lang]:
            assert word not in text, (rule, lang, word)


def test_committed_confirmation_says_points_depend_on_keeping_it():
    for lang, tpl in _templates("flexibility_committed").items():
        text = tpl["body_jinja"].lower()
        assert "{{ reward_points_estimated }}" in text
        assert KEEP_WORDING[lang] in text, lang


@pytest.mark.parametrize("kept,reason,needle", [("true", "kept", {"it": "mantenuto", "en": "kept", "es": "cumplido"}),
                                               ("false", "not_kept", {"it": "non mantenuto", "en": "not kept", "es": "no cumplido"}),
                                               ("false", "no_window", {"it": "non valutabile", "en": "not evaluable", "es": "no evaluable"}),
                                               ("false", "duplicate_window", {"it": "già conteggiato", "en": "already counted", "es": "ya contabilizado"})])
def test_settled_outcome_renders_kept_not_kept_or_not_evaluable(kept, reason, needle):
    from jinja2 import Environment

    env = Environment(autoescape=False)
    facts = {"kept": kept, "reason": reason, "reward_points_actual": "7", "actual_kwh": "", "window_start": "13:00", "window_end": "14:00", "period": "2026-10-01"}
    for lang, tpl in _templates("commitment_settled").items():
        title = env.from_string(tpl["title_jinja"]).render(**facts).lower()
        body = env.from_string(tpl["body_jinja"]).render(**facts).lower()
        assert needle[lang] in title, (lang, title)
        assert "{{" not in body and body.strip()
        if kept == "true":
            assert "7" in title and "7" in body
