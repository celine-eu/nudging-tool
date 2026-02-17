# engine/engine_service.py
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from celine.nudging.config.settings import settings
from celine.nudging.db.models import NudgeLog, Rule, Template, UserPreference
from celine.nudging.engine.rules.models import (
    DigitalTwinEvent,
    NudgeEvent,
    NudgeSeverity,
    NudgeType,
)
from celine.nudging.engine.templates.renderer import render

# Families that do NOT require energy/digital-twin metrics
NON_ENERGY_FAMILIES: set[str] = {
    "system",
    "onboarding",
    "engagement",
    "seasonal",
    "weather",
}


class EngineResultStatus(str, Enum):
    CREATED = "created"
    NOT_TRIGGERED = "not_triggered"
    MISSING_FACTS = "missing_facts"
    UNKNOWN_SCENARIO = "unknown_scenario"
    SUPPRESSED_DEDUP = "suppressed_dedup"


@dataclass(frozen=True)
class EngineResult:
    status: EngineResultStatus
    nudge: NudgeEvent | None = None
    reason: str | None = None
    details: dict | None = None


# -----------------------------
# Time inference (daily/weekly/monthly)
# -----------------------------


@dataclass(frozen=True)
class TimeScope:
    frequency: str  # "daily" | "weekly" | "monthly"
    scope: str  # stable value used for dedup (e.g. 2026-02-01, 2026-W05, 2026-02)


_DAILY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")  # 2026-02-01
_MONTHLY_RE = re.compile(r"^\d{4}-\d{2}$")  # 2026-02
_WEEKLY_RE = re.compile(r"^\d{4}-W\d{2}$")  # 2026-W05 (ISO week)
_YEARLY_RE = re.compile(r"^\d{4}$")  # 2026


def _infer_time_scope(facts: dict) -> TimeScope | None:
    """
    Infer frequency from DT facts.

    Preferred: facts["time"]
    Fallbacks: date (daily), period (monthly), week (weekly)

    Accepted formats:
      - daily:   YYYY-MM-DD
      - weekly:  YYYY-Www
      - monthly: YYYY-MM
    """
    raw = (
        facts.get("time")
        or facts.get("date")
        or facts.get("week")
        or facts.get("period")
    )
    if not isinstance(raw, str) or not raw.strip():
        return None

    raw = raw.strip()

    if _DAILY_RE.match(raw):
        return TimeScope(frequency="daily", scope=raw)
    if _WEEKLY_RE.match(raw):
        return TimeScope(frequency="weekly", scope=raw)
    if _MONTHLY_RE.match(raw):
        return TimeScope(frequency="monthly", scope=raw)
    if _YEARLY_RE.match(raw):
        return TimeScope(frequency="yearly", scope=raw)

    return None


def _normalize_time_fields(facts: dict, ts: TimeScope) -> dict:
    """
    Returns a new facts dict with consistent keys for dedup/required facts:
      - daily   => facts["date"] = YYYY-MM-DD
      - weekly  => facts["week"] = YYYY-Www
      - monthly => facts["period"] = YYYY-MM
      - yearly  => facts["period"] = YYYY
    Also sets facts["time"] = scope.
    """
    out = dict(facts)
    out["time"] = ts.scope
    if ts.frequency == "daily":
        out["date"] = ts.scope
    elif ts.frequency == "weekly":
        out["week"] = ts.scope
    elif ts.frequency == "monthly":
        out["period"] = ts.scope
    elif ts.frequency == "yearly":
        out["period"] = ts.scope
    return out


# -----------------------------
# Contract validation (Step 1)
# -----------------------------


def _validate_facts_contract(facts: dict) -> tuple[bool, list[str]]:
    """
    Minimal DT-enriched contract:
    facts must include:
      - facts_version (str)
      - scenario (str)
    """
    errors: list[str] = []
    if not isinstance(facts.get("facts_version"), str) or not facts.get(
        "facts_version"
    ):
        errors.append("missing facts_version")
    if not isinstance(facts.get("scenario"), str) or not facts.get("scenario"):
        errors.append("missing scenario")
    return (len(errors) == 0), errors


def _resolve_rule_ids_from_scenario(scenario: str) -> list[str]:
    """
    Map scenario -> list[rule_id].
    Supports:
      - SCENARIO_TO_RULE_IDS (multi)
      - SCENARIO_TO_RULE_ID (legacy single)
      - fallback: if scenario looks like a rule_id, return [scenario]
    """
    mapping_multi = getattr(settings, "SCENARIO_TO_RULE_IDS", None)
    if isinstance(mapping_multi, dict):
        val = mapping_multi.get(scenario)
        if isinstance(val, (list, tuple)):
            return [x for x in val if isinstance(x, str) and x]

    mapping_single = getattr(settings, "SCENARIO_TO_RULE_ID", None)
    if isinstance(mapping_single, dict):
        rid = mapping_single.get(scenario)
        if isinstance(rid, str) and rid:
            return [rid]

    # optional fallback (handy in dev): scenario itself is a rule id
    if isinstance(scenario, str) and scenario:
        return [scenario]

    return []


async def _filter_rule_ids_by_definition(
    db: AsyncSession,
    rule_ids: list[str],
    frequency: str,
) -> list[str]:
    """
    Select only the rule(s) matching inferred frequency.

    Quick heuristic based on rule_id naming.
    If you prefer, make it stricter by checking rule.definition["dedup_window"] later.
    """
    res = await db.execute(
        select(Rule.id, Rule.definition).where(
            Rule.id.in_(rule_ids), Rule.enabled.is_(True)
        )
    )
    rows = res.all()

    wanted = frequency.lower().strip()  # daily|weekly|monthly
    out: list[str] = []
    for rid, definition in rows:
        window = str((definition or {}).get("dedup_window") or "").lower()
        if window == wanted:
            out.append(str(rid))

    return out


# -----------------------------
# Helpers
# -----------------------------


async def _resolve_lang(
    db: AsyncSession,
    *,
    user_id: str,
    facts: dict,
) -> str:
    # 1) override esplicito dal Digital Twin
    lang = facts.get("lang")
    if isinstance(lang, str) and lang:
        return lang

    # 2) preferenza utente
    res = await db.execute(
        select(UserPreference.lang).where(UserPreference.user_id == user_id)
    )
    pref_lang = res.scalar_one_or_none()
    if pref_lang:
        return pref_lang

    # 3) fallback
    return settings.DEFAULT_LANG


def compute_dedup_key(rule_id: str, user_id: str, scope: str) -> str:
    return f"{rule_id}:{user_id}:{scope}"


def _attempt_dedup_key(rule_id: str, user_id: str, scope: str) -> str:
    # unique key so it won't collide with uq_nudges_dedup_key
    return f"attempt:{rule_id}:{user_id}:{scope}:{uuid4().hex}"


async def _log_status(
    db: AsyncSession,
    *,
    status: EngineResultStatus,
    rule_id: str,
    user_id: str,
    scope: str,
    scenario: str,
    facts_version: str,
    facts: dict,
    details: dict | None = None,
    created_dedup_key: str | None = None,
    nudge_id: str | None = None,
) -> None:
    """
    Audit log for both CREATED and non-created outcomes.

    - For CREATED: use created_dedup_key (stable) + nudge_id
    - For others: use attempt dedup key (unique) so UNIQUE constraint isn't hit
    """
    if status == EngineResultStatus.CREATED:
        dedup_key = created_dedup_key or _attempt_dedup_key(rule_id, user_id, scope)
        log_id = nudge_id or uuid4().hex
    else:
        dedup_key = _attempt_dedup_key(rule_id, user_id, scope)
        log_id = uuid4().hex

    payload = {
        "scenario": scenario,
        "facts_version": facts_version,
        "facts": facts,
        "details": details or {},
    }

    db.add(
        NudgeLog(
            id=log_id,
            rule_id=rule_id,
            user_id=user_id,
            dedup_key=dedup_key,
            status=status.value,  # <-- IMPORTANT: EngineResultStatus
            payload=payload,
        )
    )
    await db.commit()


async def _load_rule_and_template(
    db: AsyncSession, rule_id: str, lang: str
) -> tuple[Rule, Template]:
    rule_res = await db.execute(
        select(Rule).where(Rule.id == rule_id, Rule.enabled.is_(True))
    )
    rule = rule_res.scalar_one_or_none()
    if not rule:
        raise ValueError(f"Rule not found or disabled: {rule_id}")

    tmpl_res = await db.execute(
        select(Template).where(Template.rule_id == rule.id, Template.lang == lang)
    )
    tmpl = tmpl_res.scalar_one_or_none()
    if not tmpl:
        raise ValueError(f"Template not found for rule={rule_id} lang={lang}")

    return rule, tmpl


def _facts_from_event(evt: DigitalTwinEvent) -> dict:
    """
    Prefer evt.facts; fallback to payload for backward compatibility.
    """
    facts = getattr(evt, "facts", None)
    if isinstance(facts, dict) and facts:
        return facts
    return evt.payload or {}


def _validate_required_facts(rule: Rule, facts: dict) -> tuple[bool, list[str]]:
    required = rule.definition.get("required_facts") or []
    if not required:
        return True, []
    missing = [k for k in required if k not in facts]
    return len(missing) == 0, missing


def _evaluate_imported_up(rule: Rule, facts: dict) -> tuple[bool, dict, str | None]:
    """
    Rule-specific for now.
    Requires: delta_pct already computed by DT.
    Returns: triggered, enriched_facts, reason_if_not_triggered
    """
    threshold_pct = float(rule.definition.get("threshold_pct", 20.0))

    delta_pct = facts.get("delta_pct")
    if delta_pct is None:
        return False, facts, "missing_fact:delta_pct"

    try:
        triggered = float(delta_pct) > threshold_pct
    except Exception:
        return False, {**facts, "delta_pct": delta_pct}, "invalid_fact:delta_pct"

    enriched = {**facts, "threshold_pct": threshold_pct}
    if not triggered:
        return False, enriched, "condition_not_met"

    return True, enriched, None


def _evaluate_imported_down(rule: Rule, facts: dict) -> tuple[bool, dict, str | None]:
    threshold_pct = float(rule.definition.get("threshold_pct", -5.0))
    delta_pct = facts.get("delta_pct")
    if delta_pct is None:
        return False, facts, "missing_fact:delta_pct"
    try:
        triggered = float(delta_pct) < threshold_pct
    except Exception:
        return False, {**facts, "delta_pct": delta_pct}, "invalid_fact:delta_pct"
    enriched = {**facts, "threshold_pct": threshold_pct}
    return (
        (True, enriched, None) if triggered else (False, enriched, "condition_not_met")
    )


def _evaluate_static_message(rule: Rule, facts: dict) -> tuple[bool, dict, str | None]:
    return True, dict(facts), None


def _evaluate_passthrough(rule: Rule, facts: dict) -> tuple[bool, dict, str | None]:
    return True, dict(facts), None


def _evaluate_rule(rule: Rule, facts: dict) -> tuple[bool, dict, str | None]:
    kind = str((rule.definition or {}).get("kind") or "").strip().lower()

    if kind == "static_message":
        return _evaluate_static_message(rule, facts)

    if kind == "imported_up":
        return _evaluate_imported_up(rule, facts)

    if kind == "imported_down":
        return _evaluate_imported_down(rule, facts)

    # Weather / external events
    if kind in {"sunny_pros", "sunny_cons", "extr_event"}:
        return _evaluate_passthrough(rule, facts)

    # Price nudges (se poi vuoi, puoi mettere gating)
    if kind in {"price_up", "price_down"}:
        return _evaluate_passthrough(rule, facts)

    # Default safe
    return _evaluate_passthrough(rule, facts)

    # TODO - Aggiungere evaluate per tutte le altre regole


def _should_skip_dedup(rule: Rule) -> bool:
    return str(rule.definition.get("dedup_window", "")).lower() == "always"


# TODO valutare bene per la duplicazione
def _dedup_scope(rule: Rule, facts: dict) -> str:
    window = str(rule.definition.get("dedup_window") or "monthly").lower().strip()

    if window in {"always", "once", "one"}:
        return "once"

    if window == "yearly":
        return str(facts.get("year") or datetime.utcnow().strftime("%Y"))

    if window == "daily":
        return str(facts.get("date") or datetime.utcnow().strftime("%Y-%m-%d"))

    if window == "weekly":
        # prefer DT-provided ISO week (YYYY-Www)
        w = facts.get("week")
        if isinstance(w, str) and w:
            return w
        # fallback (not perfect ISO, but dev-friendly)
        return datetime.utcnow().strftime("%Y-W%W")

    if window == "hourly":
        return str(facts.get("hour") or datetime.utcnow().strftime("%Y-%m-%dT%H"))

    # default monthly
    return str(facts.get("period") or datetime.utcnow().strftime("%Y-%m"))


# -----------------------------
# Engine main
# -----------------------------


async def run_engine_batch(
    evt: DigitalTwinEvent,
    db: AsyncSession,
    *,
    lang: str | None = None,
) -> list[EngineResult]:

    facts_in_raw = _facts_from_event(evt)
    lang = lang or await _resolve_lang(db, user_id=str(evt.user_id), facts=facts_in_raw)

    ok, errors = _validate_facts_contract(facts_in_raw)
    if not ok:
        # log generic attempt without rule_id
        scenario = str(facts_in_raw.get("scenario") or "unknown")
        await _log_status(
            db,
            status=EngineResultStatus.MISSING_FACTS,
            rule_id="__no_rule__",
            user_id=str(evt.user_id),
            scope="__no_scope__",
            scenario=scenario,
            facts_version=str(facts_in_raw.get("facts_version") or ""),
            facts=facts_in_raw,
            details={"errors": errors, "reason": "invalid_facts_contract"},
        )
        return [
            EngineResult(
                status=EngineResultStatus.MISSING_FACTS,
                reason="invalid_facts_contract",
                details={"errors": errors},
            )
        ]

    scenario = str(facts_in_raw["scenario"])

    # Infer time scope -> choose daily/weekly/monthly (only ONE)
    ts = _infer_time_scope(facts_in_raw)
    if not ts:
        await _log_status(
            db,
            status=EngineResultStatus.MISSING_FACTS,
            rule_id="__no_rule__",
            user_id=str(evt.user_id),
            scope="__no_scope__",
            scenario=scenario,
            facts_version=str(facts_in_raw.get("facts_version") or ""),
            facts=facts_in_raw,
            details={"reason": "missing_or_invalid_time_scope"},
        )
        return [
            EngineResult(
                status=EngineResultStatus.MISSING_FACTS,
                reason="missing_or_invalid_time_scope",
                details={
                    "expected": ["YYYY-MM-DD", "YYYY-Www", "YYYY-MM"],
                    "hint": "Send facts.time (preferred) or date/week/period",
                    "got": facts_in_raw.get("time")
                    or facts_in_raw.get("date")
                    or facts_in_raw.get("week")
                    or facts_in_raw.get("period"),
                },
            )
        ]

    facts_in = _normalize_time_fields(facts_in_raw, ts)

    rule_ids_all = _resolve_rule_ids_from_scenario(scenario)
    if not rule_ids_all:
        await _log_status(
            db,
            status=EngineResultStatus.UNKNOWN_SCENARIO,
            rule_id="__no_rule__",
            user_id=str(evt.user_id),
            scope=ts.scope,
            scenario=scenario,
            facts_version=str(facts_in_raw.get("facts_version") or ""),
            facts=facts_in_raw,
            details={"reason": "scenario_not_mapped"},
        )
        return [
            EngineResult(
                status=EngineResultStatus.UNKNOWN_SCENARIO,
                reason="scenario_not_mapped",
                details={"scenario": scenario},
            )
        ]

    # Filter to ONE frequency => prevents double notifications
    rule_ids = await _filter_rule_ids_by_definition(db, rule_ids_all, ts.frequency)

    if not rule_ids:
        await _log_status(
            db,
            status=EngineResultStatus.UNKNOWN_SCENARIO,
            rule_id="__no_rule__",
            user_id=str(evt.user_id),
            scope=ts.scope,
            scenario=scenario,
            facts_version=str(facts_in_raw.get("facts_version") or ""),
            facts=facts_in_raw,
            details={
                "reason": "no_rule_for_inferred_frequency",
                "frequency": ts.frequency,
                "mapped_rules": rule_ids_all,
            },
        )
        return [
            EngineResult(
                status=EngineResultStatus.UNKNOWN_SCENARIO,
                reason="no_rule_for_inferred_frequency",
                details={
                    "scenario": scenario,
                    "frequency": ts.frequency,
                    "mapped_rules": rule_ids_all,
                },
            )
        ]

    # Evaluate only filtered rules (typically exactly 1)
    results: list[EngineResult] = []
    for rid in rule_ids:
        res = await _run_single_rule(
            evt,
            db,
            rule_id=rid,
            scenario=scenario,
            facts_in=facts_in,
            lang=lang,
        )
        results.append(res)

    return results


async def _run_single_rule(
    evt: DigitalTwinEvent,
    db: AsyncSession,
    *,
    rule_id: str,
    scenario: str,
    facts_in: dict,
    lang: str,
) -> EngineResult:
    # load rule + template
    try:
        rule, tmpl = await _load_rule_and_template(db, rule_id, lang)
        facts_version_str = str(facts_in.get("facts_version") or "")
        scope = _dedup_scope(
            rule, facts_in
        )  # compute early for audit #TODO: fare la casistica "always"

    except ValueError as e:
        facts_version_str = str(facts_in.get("facts_version") or "")
        await _log_status(
            db,
            status=EngineResultStatus.UNKNOWN_SCENARIO,
            rule_id=str(rule_id),
            user_id=str(evt.user_id),
            scope=str(
                facts_in.get("time")
                or facts_in.get("date")
                or facts_in.get("week")
                or facts_in.get("period")
                or "__no_scope__"
            ),
            scenario=scenario,
            facts_version=facts_version_str,
            facts=facts_in,
            details={"reason": str(e)},
        )
        return EngineResult(
            status=EngineResultStatus.UNKNOWN_SCENARIO,
            reason=str(e),
            details={"scenario": scenario, "rule_id": rule_id},
        )

    # validate required facts
    ok_req, missing = _validate_required_facts(rule, facts_in)
    if not ok_req:
        await _log_status(
            db,
            status=EngineResultStatus.MISSING_FACTS,
            rule_id=str(rule.id),
            user_id=str(evt.user_id),
            scope=scope,
            scenario=scenario,
            facts_version=facts_version_str,
            facts=facts_in,
            details={"reason": "missing_required_facts", "missing": missing},
        )
        return EngineResult(
            status=EngineResultStatus.MISSING_FACTS,
            reason="missing_required_facts",
            details={"missing": missing, "scenario": scenario, "rule_id": str(rule.id)},
        )

    # evaluate
    triggered, facts, reason = _evaluate_rule(rule, facts_in)

    if not triggered:
        await _log_status(
            db,
            status=EngineResultStatus.NOT_TRIGGERED,
            rule_id=str(rule.id),
            user_id=str(evt.user_id),
            scope=scope,
            scenario=scenario,
            facts_version=facts_version_str,
            facts=facts,
            details={"reason": reason or "not_triggered"},
        )
        return EngineResult(
            status=EngineResultStatus.NOT_TRIGGERED,
            reason=reason or "not_triggered",
            details={"scenario": scenario, "rule_id": str(rule.id)},
        )

    # render
    ctx = {
        "now": datetime.utcnow().isoformat(),
        "user_id": evt.user_id,
        "community_id": evt.community_id,
        **facts,
    }
    title, body = render(tmpl.title_jinja, tmpl.body_jinja, ctx)

    # build NudgeEvent
    nudge_id = uuid4().hex
    nudge = NudgeEvent(
        nudge_id=nudge_id,
        rule_id=str(rule.id),
        family=rule.family,
        type=NudgeType(rule.type),
        severity=NudgeSeverity(rule.severity),
        user_id=evt.user_id,
        community_id=evt.community_id,
        facts=facts,
        render_context=ctx,
        title=title,
        body=body,
    )

    # write log with dedup
    dk = compute_dedup_key(str(rule.id), evt.user_id, scope)

    rule_id_str = str(rule.id)
    scenario_str = scenario
    dk_str = dk
    facts_version_str = str(facts_in.get("facts_version") or "")

    try:
        db.add(
            NudgeLog(
                id=nudge.nudge_id,
                rule_id=rule_id_str,
                user_id=nudge.user_id,
                dedup_key=dk_str,
                status=EngineResultStatus.CREATED.value,
                payload={
                    "title": nudge.title,
                    "body": nudge.body,
                    "facts": nudge.facts,
                    "scenario": scenario_str,
                    "facts_version": facts_version_str,
                },
            )
        )
        await db.commit()
    except IntegrityError:
        await db.rollback()

        await _log_status(
            db,
            status=EngineResultStatus.SUPPRESSED_DEDUP,
            rule_id=rule_id_str,
            user_id=str(evt.user_id),
            scope=scope,
            scenario=scenario_str,
            facts_version=facts_version_str,
            facts=facts_in,
            details={
                "reason": "duplicate_in_dedup_window",
                "created_dedup_key": dk_str,
            },
        )

        return EngineResult(
            status=EngineResultStatus.SUPPRESSED_DEDUP,
            reason="duplicate_in_dedup_window",
            details={
                "dedup_key": dk_str,
                "scenario": scenario_str,
                "rule_id": rule_id_str,
            },
        )

    return EngineResult(
        status=EngineResultStatus.CREATED,
        nudge=nudge,
        details={"dedup_key": dk_str, "scenario": scenario_str, "rule_id": rule_id_str},
    )
