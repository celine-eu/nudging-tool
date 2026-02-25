from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Tuple

import yaml

from celine.nudging.seed.schema import (
    PreferenceSeed,
    RuleOverrideSeed,
    RuleSeed,
    TemplateSeed,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SeedData:
    rules: List[dict]
    templates: List[dict]
    preferences: List[dict]
    overrides: List[dict]


# ---------------------------------------------------------------------------
# YAML loading helpers
# ---------------------------------------------------------------------------

def _load_yaml(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as e:
        raise ValueError(f"Failed to read YAML {path}: {e}") from e


def _infer_template_coords(
    root: Path, source: Path, item: dict, rule_id_hint: str | None = None
) -> dict:
    if "rule_id" in item and "lang" in item:
        return item
    try:
        rel = source.relative_to(root)
        parts = rel.parts
    except Exception:
        return item
    if len(parts) >= 1:
        rule_id = rule_id_hint or (parts[-2] if len(parts) >= 2 else None)
        lang = source.stem
        if "rule_id" not in item and rule_id:
            item["rule_id"] = rule_id
        if "lang" not in item and lang:
            item["lang"] = lang
    return item


def _infer_override_coords(root: Path, source: Path, item: dict) -> dict:
    if "rule_id" in item and "community_id" in item:
        return item
    try:
        rel = source.relative_to(root)
        parts = rel.parts
    except Exception:
        return item
    # Expected: overrides/<community_id>/<rule_id>.yaml
    if len(parts) >= 2:
        community_id = parts[-2]
        rule_id = source.stem
        if "community_id" not in item and community_id:
            item["community_id"] = community_id
        if "rule_id" not in item and rule_id:
            item["rule_id"] = rule_id
    return item


def _normalize_items(payload: Any, key: str, source: Path, root: Path | None = None) -> List[dict]:
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
        if key == "templates":
            item = payload
            if root is not None:
                item = _infer_template_coords(root, source, dict(payload))
            if all(k in item for k in ("rule_id", "title_jinja", "body_jinja")):
                return [item]
        if "user_id" in payload and key == "preferences":
            return [payload]
        if key == "overrides":
            item = payload
            if root is not None:
                item = _infer_override_coords(root, source, dict(payload))
            if all(k in item for k in ("rule_id", "community_id")):
                return [item]
    logger.warning("Skipping %s: unrecognized structure", source)
    return []


def _collect_from_dir(root: Path, key: str) -> List[dict]:
    if not root.exists() or not root.is_dir():
        return []
    items: List[dict] = []
    for path in sorted(root.rglob("*.yml")) + sorted(root.rglob("*.yaml")):
        payload = _load_yaml(path)
        items.extend(_normalize_items(payload, key, path, root))
    return items


def _collect_rule_dirs(rules_dir: Path) -> tuple[list[dict], list[dict]]:
    if not rules_dir.exists() or not rules_dir.is_dir():
        return [], []
    rules: list[dict] = []
    templates: list[dict] = []
    for rule_dir in sorted(p for p in rules_dir.iterdir() if p.is_dir()):
        rule_id = rule_dir.name
        rule_file = None
        for name in ("rule.yaml", "rule.yml"):
            candidate = rule_dir / name
            if candidate.exists():
                rule_file = candidate
                break
        if rule_file is not None:
            payload = _load_yaml(rule_file)
            items = _normalize_items(payload, "rules", rule_file, rules_dir)
            for it in items:
                if "id" not in it:
                    it["id"] = rule_id
                rules.append(it)

        tmpl_dir = rule_dir / "templates"
        if tmpl_dir.exists() and tmpl_dir.is_dir():
            for path in sorted(tmpl_dir.rglob("*.yml")) + sorted(
                tmpl_dir.rglob("*.yaml")
            ):
                payload = _load_yaml(path)
                items = _normalize_items(payload, "templates", path, tmpl_dir)
                for it in items:
                    it = _infer_template_coords(
                        tmpl_dir, path, it, rule_id_hint=rule_id
                    )
                    templates.append(it)
    return rules, templates


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
    overrides_dir = seed_dir / "overrides"

    rules, templates = _collect_rule_dirs(rules_dir)

    # Legacy folders
    if not rules:
        rules = _collect_from_dir(rules_dir, "rules")
    if not templates:
        templates = _collect_from_dir(templates_dir, "templates")

    preferences = _collect_from_dir(preferences_dir, "preferences")
    overrides = _collect_from_dir(overrides_dir, "overrides")

    # Legacy fallback if the new dirs are missing/empty
    if not rules and not rules_dir.exists():
        rules = _collect_legacy(seed_dir, "rules.yaml", "rules")
    if not templates and not templates_dir.exists():
        templates = _collect_legacy(seed_dir, "templates.yaml", "templates")
    if not preferences and not preferences_dir.exists():
        preferences = _collect_legacy(seed_dir, "preferences.yaml", "preferences")
    if not overrides and not overrides_dir.exists():
        overrides = _collect_legacy(seed_dir, "overrides.yaml", "overrides")

    return SeedData(
        rules=rules,
        templates=templates,
        preferences=preferences,
        overrides=overrides,
    )


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

    overrides_out: List[dict] = []
    for idx, o in enumerate(seed.overrides):
        try:
            obj = RuleOverrideSeed.model_validate(o)
        except Exception as e:
            errors.append(f"overrides[{idx}]: {e}")
            continue
        overrides_out.append(obj.model_dump(exclude_none=True))

    return (
        SeedData(
            rules=rules_out,
            templates=templates_out,
            preferences=prefs_out,
            overrides=overrides_out,
        ),
        errors,
    )
