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


async def _resolve_lang(db: AsyncSession, *, user_id: str, facts: dict) -> str:
    lang = facts.get("lang")
    if isinstance(lang, str) and lang:
        return lang
    res = await db.execute(
        select(UserPreference.lang).where(UserPreference.user_id == user_id)
    )
    return res.scalar_one_or_none() or settings.DEFAULT_LANG


def compute_dedup_key(rule_id: str, user_id: str, scope: str) -> str:
    return f"{rule_id}:{user_id}:{scope}"


def _attempt_dedup_key(rule_id: str, user_id: str, scope: str) -> str:
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
) -> None:
    """Write an audit row to nudges_log. Never creates a Notification."""
    db.add(
        NudgeLog(
            id=uuid4().hex,
            rule_id=rule_id,
            user_id=user_id,
            dedup_key=_attempt_dedup_key(rule_id, user_id, scope),
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
    window = str((rule.definition or {}).get("dedup_window") or "").lower()
    if window == "always":
        return uuid4().hex
    if window == "once":
        return "once"
    return str(
        facts.get("time")
        or facts.get("date")
        or facts.get("week")
        or facts.get("period")
        or datetime.utcnow().strftime("%Y-%m")
    )


def _validate_required_facts(rule: Rule, facts: dict) -> tuple[bool, list[str]]:
    required = (rule.definition or {}).get("required_facts") or []
    missing = [f for f in required if f not in facts or facts[f] is None]
    return (len(missing) == 0), missing


def _evaluate_rule(rule: Rule, facts: dict) -> tuple[bool, dict, str | None]:
    return True, facts, None


def _facts_from_event(evt: DigitalTwinEvent) -> dict:
    return {**evt.facts, **evt.payload}


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
        await _log_status(
            db,
            status=EngineResultStatus.MISSING_FACTS,
            rule_id="__no_rule__",
            user_id=str(evt.user_id),
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
            scope="__no_scope__",
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
        "now": datetime.utcnow().isoformat(),
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
        community_id=evt.community_id,
        facts=facts,
        render_context=ctx,
        title=title,
        body=body,
    )

    dk = compute_dedup_key(str(rule.id), evt.user_id, scope)

    try:
        nudge_log = NudgeLog(
            id=nudge.nudge_id,
            rule_id=str(rule.id),
            user_id=nudge.user_id,
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
            community_id=nudge.community_id,
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
