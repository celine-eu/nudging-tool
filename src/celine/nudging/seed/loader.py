from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

import yaml

from celine.nudging.seed.schema import PreferenceSeed, RuleSeed, TemplateSeed

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SeedData:
    rules: List[dict]
    templates: List[dict]
    preferences: List[dict]


# ---------------------------------------------------------------------------
# YAML loading helpers
# ---------------------------------------------------------------------------

def _load_yaml(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as e:
        raise ValueError(f"Failed to read YAML {path}: {e}") from e


def _normalize_items(payload: Any, key: str, source: Path) -> List[dict]:
    if payload is None:
        return []
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        if key in payload and isinstance(payload[key], list):
            return payload[key]
        # Accept single-object YAML for convenience
        if all(k in payload for k in ("id", "name")) and key == "rules":
            return [payload]
        if all(k in payload for k in ("rule_id", "title_jinja", "body_jinja")) and key == "templates":
            return [payload]
        if "user_id" in payload and key == "preferences":
            return [payload]
    logger.warning("Skipping %s: unrecognized structure", source)
    return []


def _collect_from_dir(root: Path, key: str) -> List[dict]:
    if not root.exists() or not root.is_dir():
        return []
    items: List[dict] = []
    for path in sorted(root.rglob("*.yml")) + sorted(root.rglob("*.yaml")):
        payload = _load_yaml(path)
        items.extend(_normalize_items(payload, key, path))
    return items


def _collect_legacy(seed_dir: Path, name: str, key: str) -> List[dict]:
    path = seed_dir / name
    if not path.exists():
        return []
    payload = _load_yaml(path)
    return _normalize_items(payload, key, path)


def load_seed_dir(seed_dir: Path) -> SeedData:
    rules_dir = seed_dir / "rules"
    templates_dir = seed_dir / "templates"
    preferences_dir = seed_dir / "preferences"

    rules = _collect_from_dir(rules_dir, "rules")
    templates = _collect_from_dir(templates_dir, "templates")
    preferences = _collect_from_dir(preferences_dir, "preferences")

    # Legacy fallback if the new dirs are missing/empty
    if not rules and not rules_dir.exists():
        rules = _collect_legacy(seed_dir, "rules.yaml", "rules")
    if not templates and not templates_dir.exists():
        templates = _collect_legacy(seed_dir, "templates.yaml", "templates")
    if not preferences and not preferences_dir.exists():
        preferences = _collect_legacy(seed_dir, "preferences.yaml", "preferences")

    return SeedData(rules=rules, templates=templates, preferences=preferences)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

KNOWN_KINDS = {
    "static_message",
    "imported_up",
    "imported_down",
    "price_up",
    "price_down",
    "sunny_pros",
    "sunny_cons",
    "extr_event",
    "kpi_conditions",
}


def _validate_required_facts(defn: Dict[str, Any], errors: List[str]) -> None:
    rf = defn.get("required_facts")
    if rf is None:
        return
    if not isinstance(rf, list) or any(not isinstance(x, str) for x in rf):
        errors.append("definition.required_facts must be a list of strings")


def _validate_scenarios(defn: Dict[str, Any], errors: List[str]) -> None:
    sc = defn.get("scenarios")
    if sc is None:
        return
    if not isinstance(sc, list) or any(not isinstance(x, str) for x in sc):
        errors.append("definition.scenarios must be a list of strings")


def _validate_kpi_conditions(defn: Dict[str, Any], errors: List[str]) -> None:
    conditions = defn.get("conditions")
    if not isinstance(conditions, list) or not conditions:
        errors.append("definition.conditions must be a non-empty list")
        return
    allowed_ops = {"<", "<=", ">", ">=", "==", "!="}
    for idx, c in enumerate(conditions):
        if not isinstance(c, dict):
            errors.append(f"definition.conditions[{idx}] must be an object")
            continue
        if not isinstance(c.get("fact_key"), str) or not c.get("fact_key"):
            errors.append(f"definition.conditions[{idx}].fact_key is required")
        if not isinstance(c.get("op"), str) or not c.get("op"):
            errors.append(f"definition.conditions[{idx}].op is required")
        elif c.get("op") not in allowed_ops:
            errors.append(
                f"definition.conditions[{idx}].op must be one of {sorted(allowed_ops)}"
            )
        if "value" not in c:
            errors.append(f"definition.conditions[{idx}].value is required")


def validate_rule_definition(defn: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    if not isinstance(defn, dict):
        return ["definition must be an object"]

    kind = defn.get("kind")
    if not isinstance(kind, str) or not kind:
        errors.append("definition.kind is required")
    elif kind not in KNOWN_KINDS:
        logger.warning("Unknown rule kind '%s' - allowing but skipping strict validation", kind)

    _validate_required_facts(defn, errors)
    _validate_scenarios(defn, errors)

    if kind in {"imported_up", "imported_down"}:
        thr = defn.get("threshold_pct")
        if thr is not None and not isinstance(thr, (int, float)):
            errors.append("definition.threshold_pct must be a number")

    if kind == "kpi_conditions":
        _validate_kpi_conditions(defn, errors)

    return errors


def validate_seed(seed: SeedData) -> Tuple[SeedData, List[str]]:
    errors: List[str] = []

    rules_out: List[dict] = []
    for idx, r in enumerate(seed.rules):
        try:
            obj = RuleSeed.model_validate(r)
        except Exception as e:
            errors.append(f"rules[{idx}]: {e}")
            continue
        defn_errors = validate_rule_definition(obj.definition)
        errors.extend([f"rules[{idx}]: {msg}" for msg in defn_errors])
        rules_out.append(obj.model_dump(exclude_none=True))

    templates_out: List[dict] = []
    for idx, t in enumerate(seed.templates):
        try:
            obj = TemplateSeed.model_validate(t)
        except Exception as e:
            errors.append(f"templates[{idx}]: {e}")
            continue
        templates_out.append(obj.model_dump(exclude_none=True))

    prefs_out: List[dict] = []
    for idx, p in enumerate(seed.preferences):
        try:
            obj = PreferenceSeed.model_validate(p)
        except Exception as e:
            errors.append(f"preferences[{idx}]: {e}")
            continue
        prefs_out.append(obj.model_dump(exclude_none=True))

    return SeedData(rules=rules_out, templates=templates_out, preferences=prefs_out), errors
