"""REC-scoped, aggregate-only nudging analytics for the manager dashboard."""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from celine.nudging.api.schemas import (
    AnalyticsDeliveryFailure,
    AnalyticsFunnelStep,
    AnalyticsReachability,
    AnalyticsRuleMetric,
    CommunityNudgingAnalyticsOut,
)
from celine.nudging.db.models import (
    DeliveryLog,
    Notification,
    NudgeLog,
    Rule,
    RuleOverride,
    UserPreference,
    WebPushSubscription,
)
from celine.nudging.db.session import get_db
from celine.nudging.orchestrator.preferences import get_active_notification_kinds
from celine.nudging.security.policies import require_analytics
from celine.sdk.auth import JwtUser

router = APIRouter(prefix="/analytics", tags=["analytics"])
_STEPS = ("sent", "delivered", "read", "clicked", "committed")
_COMMIT_ACTIONS = {"accept", "accepted", "commit", "committed"}


def _window(start: date, end: date) -> tuple[datetime, datetime]:
    if end < start:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="end must be on or after start",
        )
    return (
        datetime.combine(start, time.min, tzinfo=timezone.utc),
        datetime.combine(end + timedelta(days=1), time.min, tzinfo=timezone.utc),
    )


def _steps(counts: dict[str, int]) -> list[AnalyticsFunnelStep]:
    return [AnalyticsFunnelStep(id=step, count=max(0, counts.get(step, 0))) for step in _STEPS]


def _channel(value: str | None) -> str:
    return "email" if (value or "").lower() == "email" else "webpush"


def _error_class(error: str | None) -> str:
    """Return a stable operational category without leaking raw provider errors."""
    value = (error or "").lower()
    if "no_subscription" in value or "no subscription" in value:
        return "no_subscription"
    if "404" in value or "410" in value or "expired" in value:
        return "expired_subscription"
    if "vapid" in value:
        return "push_configuration"
    if "smtp" in value or "mail" in value:
        return "email_transport"
    return "delivery_failed"


@router.get(
    "/communities/{community_id}/conversion",
    response_model=CommunityNudgingAnalyticsOut,
    operation_id="get_community_nudging_analytics",
    summary="Get aggregate nudging analytics for one REC",
    description=(
        "Returns funnel, rule, failure and reachability aggregates only. "
        "No participant identity, destination or message content is returned."
    ),
)
async def get_community_nudging_analytics(
    community_id: str,
    start: date = Query(..., description="First UTC calendar day, inclusive"),
    end: date = Query(..., description="Last UTC calendar day, inclusive"),
    _analytics: JwtUser = Depends(require_analytics),
    db: AsyncSession = Depends(get_db),
) -> CommunityNudgingAnalyticsOut:
    start_at, end_at = _window(start, end)
    catalogued_rule_ids = {
        rule_id
        for active_kind in get_active_notification_kinds()
        for rule_id in active_kind.get("rule_ids", [])
        if isinstance(rule_id, str) and rule_id
    }
    period_filter = (
        NudgeLog.community_id == community_id,
        NudgeLog.created_at >= start_at,
        NudgeLog.created_at < end_at,
    )

    rules = list((await db.execute(select(Rule).order_by(Rule.name, Rule.id))).scalars())
    overrides = {
        item.rule_id: item
        for item in (
            await db.execute(
                select(RuleOverride).where(RuleOverride.community_id == community_id)
            )
        ).scalars()
    }

    nudge_rows = (
        await db.execute(
            select(
                NudgeLog.rule_id,
                func.count(NudgeLog.id),
                func.max(NudgeLog.created_at),
            )
            .where(*period_filter, NudgeLog.status == "created")
            .group_by(NudgeLog.rule_id)
        )
    ).all()
    nudge_metrics = {
        rule_id: {"sent": int(count or 0), "last_fired_at": last_fired_at}
        for rule_id, count, last_fired_at in nudge_rows
    }

    committed_case = case(
        (
            and_(
                Notification.clicked_at.is_not(None),
                func.lower(func.coalesce(Notification.click_action, "")).in_(_COMMIT_ACTIONS),
            ),
            1,
        ),
        else_=0,
    )
    notification_rows = (
        await db.execute(
            select(
                NudgeLog.rule_id,
                func.count(Notification.id),
                func.sum(case((Notification.read_at.is_not(None), 1), else_=0)),
                func.sum(case((Notification.clicked_at.is_not(None), 1), else_=0)),
                func.sum(committed_case),
            )
            .join(Notification, Notification.nudge_log_id == NudgeLog.id)
            .where(*period_filter, NudgeLog.status == "created")
            .group_by(NudgeLog.rule_id)
        )
    ).all()
    notification_metrics = {
        rule_id: {
            "delivered": int(delivered or 0),
            "read": int(read or 0),
            "clicked": int(clicked or 0),
            "committed": int(committed or 0),
        }
        for rule_id, delivered, read, clicked, committed in notification_rows
    }

    channel_rows = (
        await db.execute(
            select(NudgeLog.rule_id, DeliveryLog.channel, func.count(DeliveryLog.id))
            .join(DeliveryLog, DeliveryLog.nudge_id == NudgeLog.id)
            .where(*period_filter, DeliveryLog.status == "sent")
            .group_by(NudgeLog.rule_id, DeliveryLog.channel)
        )
    ).all()
    channels: dict[str, tuple[str, int]] = {}
    for rule_id, raw_channel, count in channel_rows:
        candidate = (_channel(raw_channel), int(count or 0))
        if rule_id not in channels or candidate[1] > channels[rule_id][1]:
            channels[rule_id] = candidate

    rule_metrics: list[AnalyticsRuleMetric] = []
    totals = {
        "sent": sum(int(item.get("sent", 0)) for item in nudge_metrics.values()),
        **{
            step: sum(int(item.get(step, 0)) for item in notification_metrics.values())
            for step in ("delivered", "read", "clicked", "committed")
        },
    }
    for rule in rules:
        if rule.id not in catalogued_rule_ids:
            continue
        counts = {step: 0 for step in _STEPS}
        counts.update(notification_metrics.get(rule.id, {}))
        counts["sent"] = int(nudge_metrics.get(rule.id, {}).get("sent", 0))
        override = overrides.get(rule.id)
        active = (
            override.enabled_override
            if override is not None and override.enabled_override is not None
            else rule.enabled
        )
        rule_metrics.append(
            AnalyticsRuleMetric(
                id=rule.id,
                name=rule.name,
                family=rule.family,
                channel=channels.get(rule.id, ("webpush", 0))[0],
                severity=rule.severity,
                active=active,
                last_fired_at=nudge_metrics.get(rule.id, {}).get("last_fired_at"),
                volume=counts["sent"],
                steps=_steps(counts),
            )
        )

    failure_rows = (
        await db.execute(
            select(DeliveryLog.channel, DeliveryLog.error, func.count(DeliveryLog.id))
            .join(NudgeLog, NudgeLog.id == DeliveryLog.nudge_id)
            .where(*period_filter, DeliveryLog.status == "failed")
            .group_by(DeliveryLog.channel, DeliveryLog.error)
        )
    ).all()
    failures = Counter()
    for raw_channel, error, count in failure_rows:
        failures[(_channel(raw_channel), _error_class(error))] += int(count or 0)

    audience = set(
        (
            await db.execute(select(NudgeLog.user_id).where(*period_filter).distinct())
        ).scalars()
    )
    preference_rows = list(
        (
            await db.execute(
                select(UserPreference).where(
                    or_(
                        UserPreference.community_id == community_id,
                        UserPreference.community_id.is_(None),
                    )
                )
            )
        ).scalars()
    )
    preferences: dict[str, UserPreference] = {}
    for preference in preference_rows:
        if preference.user_id not in audience:
            continue
        current = preferences.get(preference.user_id)
        if current is None or preference.community_id == community_id:
            preferences[preference.user_id] = preference

    subscription_rows = list(
        (
            await db.execute(
                select(WebPushSubscription.user_id, WebPushSubscription.enabled).where(
                    or_(
                        WebPushSubscription.community_id == community_id,
                        WebPushSubscription.community_id.is_(None),
                    )
                )
            )
        ).all()
    )
    web_reachable = {
        user_id for user_id, enabled in subscription_rows if enabled and user_id in audience
    }
    web_disabled = {
        user_id for user_id, enabled in subscription_rows if not enabled and user_id in audience
    }
    web_preference_opt_out = {
        user_id for user_id, preference in preferences.items() if not preference.channel_web
    }
    web_reachable -= web_preference_opt_out
    web_opted_out = web_preference_opt_out | (web_disabled - web_reachable)
    email_reachable = {
        user_id
        for user_id, preference in preferences.items()
        if preference.channel_email and bool((preference.email or "").strip())
    }
    email_opted_out = {
        user_id for user_id, preference in preferences.items() if not preference.channel_email
    }

    return CommunityNudgingAnalyticsOut(
        community_id=community_id,
        start=start,
        end=end,
        steps=_steps(totals),
        rules=rule_metrics,
        failures=[
            AnalyticsDeliveryFailure(channel=channel, error_class=error_class, count=count)
            for (channel, error_class), count in sorted(failures.items())
        ],
        reachability=[
            AnalyticsReachability(
                channel="webpush",
                reachable=len(web_reachable),
                total=len(audience),
                opted_out=len(web_opted_out),
            ),
            AnalyticsReachability(
                channel="email",
                reachable=len(email_reachable),
                total=len(audience),
                opted_out=len(email_opted_out),
            ),
        ],
    )
