# engine/engine_service.py
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from celine.nudging.config.settings import settings

from celine.nudging.db.models import (
    Notification,
    NudgeLog,
    Rule,
    Template,
    UserPreference,
)

from celine.nudging.engine.rules.models import (
    DigitalTwinEvent,
    NudgeEvent,
    NudgeSeverity,
    NudgeType,
)
from celine.nudging.engine.templates.renderer import render

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


@dataclass(frozen=True)
class TimeScope:
    frequency: str
    scope: str


_DAILY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MONTHLY_RE = re.compile(r"^\d{4}-\d{2}$")
_WEEKLY_RE = re.compile(r"^\d{4}-W\d{2}$")
_YEARLY_RE = re.compile(r"^\d{4}$")


def _infer_time_scope(facts: dict) -> TimeScope | None:
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
    out = dict(facts)
    out["time"] = ts.scope
    if ts.frequency == "daily":
        out["date"] = ts.scope
    elif ts.frequency == "weekly":
        out["week"] = ts.scope
    elif ts.frequency in ("monthly", "yearly"):
        out["period"] = ts.scope
    return out


def _validate_facts_contract(facts: dict) -> tuple[bool, list[str]]:
    errors: list[str] = []
    if not isinstance(facts.get("facts_version"), str) or not facts.get(
        "facts_version"
    ):
        errors.append("missing facts_version")
    if not isinstance(facts.get("scenario"), str) or not facts.get("scenario"):
        errors.append("missing scenario")
    return (len(errors) == 0), errors


def _resolve_rule_ids_from_scenario(scenario: str) -> list[str]:
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
    if isinstance(scenario, str) and scenario:
        return [scenario]
    return []


async def _filter_rule_ids_by_definition(
    db: AsyncSession, rule_ids: list[str], frequency: str
) -> list[str]:
    res = await db.execute(
        select(Rule.id, Rule.definition).where(
            Rule.id.in_(rule_ids), Rule.enabled.is_(True)
        )
    )
    wanted = frequency.lower().strip()
    return [
        str(rid)
        for rid, definition in res.all()
        if str((definition or {}).get("dedup_window") or "").lower() == wanted
    ]


async def _resolve_rule_ids_from_db(
    db: AsyncSession, scenario: str
) -> list[str]:
    if not scenario:
        return []
    res = await db.execute(
        select(Rule.id, Rule.scenarios, Rule.definition).where(Rule.enabled.is_(True))
    )
    out: list[str] = []
    for rid, scenarios, definition in res.all():
        sc_list: list[str] = []
        if isinstance(scenarios, list):
            sc_list.extend([s for s in scenarios if isinstance(s, str)])
        defn_sc = (definition or {}).get("scenarios")
        if isinstance(defn_sc, list):
            sc_list.extend([s for s in defn_sc if isinstance(s, str)])
        if scenario in sc_list:
            out.append(str(rid))
    return out

async def _resolve_lang(
    db: AsyncSession,
    *,
    user_id: str,
    community_id: str | None,
    facts: dict,
) -> str:
    lang = facts.get("lang")
    if isinstance(lang, str) and lang:
        return lang
    res = await db.execute(
        select(UserPreference.lang)
        .where(
            UserPreference.user_id == user_id,
            or_(
                UserPreference.community_id == community_id,
                UserPreference.community_id.is_(None),
            ),
        )
        .order_by(UserPreference.community_id.is_(None).asc())
    )
    return res.scalar_one_or_none() or settings.DEFAULT_LANG


def compute_dedup_key(
    rule_id: str, user_id: str, community_id: str | None, scope: str
) -> str:
    cid = community_id or ""
    return f"{rule_id}:{user_id}:{cid}:{scope}"


def _attempt_dedup_key(
    rule_id: str, user_id: str, community_id: str | None, scope: str
) -> str:
    # unique key so it won't collide with uq_nudges_dedup_key
    cid = community_id or ""
    return f"attempt:{rule_id}:{user_id}:{cid}:{scope}:{uuid4().hex}"


async def _log_status(
    db: AsyncSession,
    *,
    status: EngineResultStatus,
    rule_id: str,
    user_id: str,
    community_id: str | None,
    scope: str,
    scenario: str,
    facts_version: str,
    facts: dict,
    details: dict | None = None,
) -> None:
    """Write an audit row to nudges_log. Never creates a Notification."""
    db.add(
        NudgeLog(
            id=uuid4().hex,
            rule_id=rule_id,
            user_id=user_id,
            community_id=community_id,
            dedup_key=_attempt_dedup_key(rule_id, user_id, community_id, scope),
            status=status.value,
            payload={
                "scenario": scenario,
                "facts_version": facts_version,
                "facts": facts,
                "details": details or {},
            },
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
    if rule is None:
        raise ValueError(f"Rule not found or disabled: {rule_id}")

    for try_lang in (lang, "en"):
        tmpl_res = await db.execute(
            select(Template).where(
                Template.rule_id == rule_id, Template.lang == try_lang
            )
        )
        tmpl = tmpl_res.scalar_one_or_none()
        if tmpl is not None:
            return rule, tmpl

    raise ValueError(f"No template found for rule={rule_id} lang={lang}")


def _dedup_scope(rule: Rule, facts: dict) -> str:
    window = str((rule.definition or {}).get("dedup_window") or "").lower().strip()
    if window == "always":
        return uuid4().hex
    if window in {"once", "one"}:
        return "once"
    if window == "hourly":
        return str(
            facts.get("hour") or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H")
        )
    if window in {"daily", "weekly", "monthly", "yearly"}:
        return str(
            facts.get("time")
            or facts.get("date")
            or facts.get("week")
            or facts.get("period")
            or datetime.now(timezone.utc).strftime("%Y-%m")
        )
    return str(
        facts.get("time")
        or facts.get("date")
        or facts.get("week")
        or facts.get("period")
        or datetime.now(timezone.utc).strftime("%Y-%m")
    )


def _validate_required_facts(rule: Rule, facts: dict) -> tuple[bool, list[str]]:
    required = (rule.definition or {}).get("required_facts") or []
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


def _coerce_num(value) -> float | None:
    try:
        return float(value)
    except Exception:
        return None


def _compare(op: str, left, right) -> bool | None:
    if op in {"<", "<=", ">", ">="}:
        l = _coerce_num(left)
        r = _coerce_num(right)
        if l is None or r is None:
            return None
        if op == "<":
            return l < r
        if op == "<=":
            return l <= r
        if op == ">":
            return l > r
        if op == ">=":
            return l >= r
    if op == "==":
        return left == right
    if op == "!=":
        return left != right
    return None


def _evaluate_kpi_conditions(rule: Rule, facts: dict) -> tuple[bool, dict, str | None]:
    conditions = (rule.definition or {}).get("conditions") or []
    if not isinstance(conditions, list) or not conditions:
        return False, dict(facts), "missing_conditions"

    for cond in conditions:
        if not isinstance(cond, dict):
            return False, dict(facts), "invalid_condition"
        fact_key = cond.get("fact_key")
        op = cond.get("op")
        if not isinstance(fact_key, str) or not fact_key:
            return False, dict(facts), "invalid_fact_key"
        if not isinstance(op, str) or not op:
            return False, dict(facts), "invalid_op"
        if fact_key not in facts:
            return False, dict(facts), f"missing_fact:{fact_key}"
        value = cond.get("value")
        cmp_res = _compare(op, facts.get(fact_key), value)
        if cmp_res is None:
            return False, dict(facts), f"invalid_compare:{fact_key}"
        if not cmp_res:
            return False, dict(facts), "condition_not_met"

    return True, dict(facts), None


_EVALUATORS: dict[str, callable] = {
    "static_message": _evaluate_static_message,
    "imported_up": _evaluate_imported_up,
    "imported_down": _evaluate_imported_down,
    "kpi_conditions": _evaluate_kpi_conditions,
    "sunny_pros": _evaluate_passthrough,
    "sunny_cons": _evaluate_passthrough,
    "extr_event": _evaluate_passthrough,
    "price_up": _evaluate_passthrough,
    "price_down": _evaluate_passthrough,
}


def _evaluate_rule(rule: Rule, facts: dict) -> tuple[bool, dict, str | None]:
    kind = str((rule.definition or {}).get("kind") or "").strip().lower()
    fn = _EVALUATORS.get(kind)
    if fn:
        return fn(rule, facts)
    return _evaluate_passthrough(rule, facts)


def _facts_from_event(evt: DigitalTwinEvent) -> dict:
    """
    Prefer evt.facts; fallback to payload for backward compatibility.
    """
    facts = getattr(evt, "facts", None)
    if isinstance(facts, dict) and facts:
        return facts
    return evt.payload or {}


async def run_engine_batch(
    evt: DigitalTwinEvent,
    db: AsyncSession,
    *,
    lang: str | None = None,
) -> list[EngineResult]:
    facts_in_raw = _facts_from_event(evt)
    lang = lang or await _resolve_lang(
        db,
        user_id=str(evt.user_id),
        community_id=str(evt.community_id) if evt.community_id is not None else None,
        facts=facts_in_raw,
    )

    ok, errors = _validate_facts_contract(facts_in_raw)
    if not ok:
        await _log_status(
            db,
            status=EngineResultStatus.MISSING_FACTS,
            rule_id="__no_rule__",
            user_id=str(evt.user_id),
            community_id=str(evt.community_id) if evt.community_id is not None else None,
            scope="__no_scope__",
            scenario=str(facts_in_raw.get("scenario") or "unknown"),
            facts_version=str(facts_in_raw.get("facts_version") or ""),
            facts=facts_in_raw,
            details={"errors": errors},
        )
        return [
            EngineResult(
                status=EngineResultStatus.MISSING_FACTS,
                reason="invalid_facts_contract",
                details={"errors": errors},
            )
        ]

    scenario = str(facts_in_raw["scenario"])
    ts = _infer_time_scope(facts_in_raw)
    if not ts:
        await _log_status(
            db,
            status=EngineResultStatus.MISSING_FACTS,
            rule_id="__no_rule__",
            user_id=str(evt.user_id),
            community_id=str(evt.community_id) if evt.community_id is not None else None,
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
            )
        ]

    facts_in = _normalize_time_fields(facts_in_raw, ts)
    rule_ids_all = await _resolve_rule_ids_from_db(db, scenario)
    if not rule_ids_all:
        rule_ids_all = _resolve_rule_ids_from_scenario(scenario)
    if not rule_ids_all:
        await _log_status(
            db,
            status=EngineResultStatus.UNKNOWN_SCENARIO,
            rule_id="__no_rule__",
            user_id=str(evt.user_id),
            community_id=str(evt.community_id) if evt.community_id is not None else None,
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

    rule_ids = await _filter_rule_ids_by_definition(db, rule_ids_all, ts.frequency)
    if not rule_ids:
        await _log_status(
            db,
            status=EngineResultStatus.UNKNOWN_SCENARIO,
            rule_id="__no_rule__",
            user_id=str(evt.user_id),
            community_id=str(evt.community_id) if evt.community_id is not None else None,
            scope=ts.scope,
            scenario=scenario,
            facts_version=str(facts_in_raw.get("facts_version") or ""),
            facts=facts_in_raw,
            details={
                "reason": "no_rule_for_inferred_frequency",
                "frequency": ts.frequency,
            },
        )
        return [
            EngineResult(
                status=EngineResultStatus.UNKNOWN_SCENARIO,
                reason="no_rule_for_inferred_frequency",
            )
        ]

    return [
        await _run_single_rule(
            evt, db, rule_id=rid, scenario=scenario, facts_in=facts_in, lang=lang
        )
        for rid in rule_ids
    ]


async def _run_single_rule(
    evt: DigitalTwinEvent,
    db: AsyncSession,
    *,
    rule_id: str,
    scenario: str,
    facts_in: dict,
    lang: str,
) -> EngineResult:
    facts_version_str = str(facts_in.get("facts_version") or "")

    try:
        rule, tmpl = await _load_rule_and_template(db, rule_id, lang)
        scope = _dedup_scope(rule, facts_in)
    except ValueError as e:
        await _log_status(
            db,
            status=EngineResultStatus.UNKNOWN_SCENARIO,
            rule_id=rule_id,
            user_id=str(evt.user_id),
            community_id=str(evt.community_id) if evt.community_id is not None else None,
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
        return EngineResult(status=EngineResultStatus.UNKNOWN_SCENARIO, reason=str(e))

    ok_req, missing = _validate_required_facts(rule, facts_in)
    if not ok_req:
        await _log_status(
            db,
            status=EngineResultStatus.MISSING_FACTS,
            rule_id=str(rule.id),
            user_id=str(evt.user_id),
            community_id=str(evt.community_id) if evt.community_id is not None else None,
            scope=scope,
            scenario=scenario,
            facts_version=facts_version_str,
            facts=facts_in,
            details={"missing": missing},
        )
        return EngineResult(
            status=EngineResultStatus.MISSING_FACTS,
            reason="missing_required_facts",
            details={"missing": missing},
        )

    triggered, facts, reason = _evaluate_rule(rule, facts_in)
    if not triggered:
        await _log_status(
            db,
            status=EngineResultStatus.NOT_TRIGGERED,
            rule_id=str(rule.id),
            user_id=str(evt.user_id),
            community_id=str(evt.community_id) if evt.community_id is not None else None,
            scope=scope,
            scenario=scenario,
            facts_version=facts_version_str,
            facts=facts,
            details={"reason": reason or "not_triggered"},
        )
        return EngineResult(
            status=EngineResultStatus.NOT_TRIGGERED, reason=reason or "not_triggered"
        )

    ctx = {
        "now": datetime.now(timezone.utc).isoformat(),
        "user_id": evt.user_id,
        "community_id": evt.community_id,
        **facts,
    }
    title, body = render(tmpl.title_jinja, tmpl.body_jinja, ctx)

    nudge = NudgeEvent(
        nudge_id=uuid4().hex,
        rule_id=str(rule.id),
        family=rule.family,
        type=NudgeType(rule.type),
        severity=NudgeSeverity(rule.severity),
        user_id=evt.user_id,
        facts=facts,
        render_context=ctx,
        title=title,
        body=body,
    )

    # write log with dedup
    dk = compute_dedup_key(str(rule.id), evt.user_id, evt.community_id, scope)

    try:
        nudge_log = NudgeLog(
            id=nudge.nudge_id,
            rule_id=str(rule.id),
            user_id=nudge.user_id,
            community_id=str(evt.community_id) if evt.community_id is not None else None,
            dedup_key=dk,
            status=EngineResultStatus.CREATED.value,
            payload={
                "scenario": scenario,
                "facts_version": facts_version_str,
                "facts": nudge.facts,
            },
        )
        notification = Notification(
            id=uuid4().hex,
            nudge_log_id=nudge.nudge_id,
            rule_id=str(rule.id),
            user_id=nudge.user_id,
            family=nudge.family,
            type=nudge.type.value,
            severity=nudge.severity.value,
            title=nudge.title,
            body=nudge.body,
            status="pending",
        )
        db.add(nudge_log)
        db.add(notification)
        await db.commit()
    except IntegrityError:
        await db.rollback()
        await _log_status(
            db,
            status=EngineResultStatus.SUPPRESSED_DEDUP,
            rule_id=str(rule.id),
            user_id=str(evt.user_id),
            community_id=str(evt.community_id) if evt.community_id is not None else None,
            scope=scope,
            scenario=scenario,
            facts_version=facts_version_str,
            facts=facts_in,
            details={"reason": "duplicate_in_dedup_window", "dedup_key": dk},
        )
        return EngineResult(
            status=EngineResultStatus.SUPPRESSED_DEDUP,
            reason="duplicate_in_dedup_window",
            details={"dedup_key": dk},
        )

    return EngineResult(
        status=EngineResultStatus.CREATED,
        nudge=nudge,
        details={"dedup_key": dk, "scenario": scenario, "rule_id": str(rule.id)},
    )
