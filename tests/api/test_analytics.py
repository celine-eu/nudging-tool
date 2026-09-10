"""Manager analytics API contract."""

from datetime import datetime, timezone

from sqlalchemy import select

from celine.nudging.db.models import (
    DeliveryLog,
    NudgeLog,
    RuleOverride,
    UserPreference,
    WebPushSubscription,
)
from tests.conftest import COMMUNITY
from tests.fakes import make_notification, make_nudge_log, make_rule


async def _seed_analytics(db) -> None:
    observed_at = datetime(2026, 8, 20, 10, 30, tzinfo=timezone.utc)
    rule = make_rule(
        "flexibility_opportunity",
        name="Flexibility opportunity",
        family="energy",
        severity="warning",
    )
    inactive = make_rule("seasonal_tip", name="Seasonal tip", family="seasonal")
    enabled_by_override = make_rule(
        "community_campaign",
        name="Community campaign",
        family="community",
        enabled=False,
    )
    db.add_all([rule, inactive, enabled_by_override])
    await db.flush()
    db.add(
        RuleOverride(
            rule_id=inactive.id,
            community_id=COMMUNITY,
            enabled_override=False,
        )
    )
    db.add(
        RuleOverride(
            rule_id=enabled_by_override.id,
            community_id=COMMUNITY,
            enabled_override=True,
        )
    )

    first = make_nudge_log(
        nudge_id="nudge-one",
        rule_id=rule.id,
        user_id="user-alice",
        community_id=COMMUNITY,
    )
    first.created_at = observed_at
    second = make_nudge_log(
        nudge_id="nudge-two",
        rule_id=rule.id,
        user_id="user-bob",
        community_id=COMMUNITY,
    )
    second.created_at = observed_at
    suppressed = make_nudge_log(
        nudge_id="nudge-suppressed",
        rule_id=rule.id,
        user_id="user-alice",
        community_id=COMMUNITY,
        status="suppressed_dedup",
    )
    suppressed.created_at = observed_at
    uncatalogued = make_nudge_log(
        nudge_id="nudge-uncatalogued",
        rule_id=inactive.id,
        user_id="user-bob",
        community_id=COMMUNITY,
    )
    uncatalogued.created_at = observed_at
    other_community = make_nudge_log(
        nudge_id="nudge-other-rec",
        rule_id=rule.id,
        user_id="user-charlie",
        community_id="community-2",
    )
    other_community.created_at = observed_at
    db.add_all([first, second, suppressed, uncatalogued, other_community])
    await db.flush()

    db.add_all(
        [
            make_notification(
                notification_id="notification-one",
                nudge_log_id=first.id,
                rule_id=rule.id,
                user_id=first.user_id,
                title="Private title",
                body="Private message",
                created_at=observed_at,
                read_at=observed_at,
                clicked_at=observed_at,
                click_action="commit",
            ),
            make_notification(
                notification_id="notification-two",
                nudge_log_id=second.id,
                rule_id=rule.id,
                user_id=second.user_id,
                created_at=observed_at,
                click_action="commit",
            ),
            make_notification(
                notification_id="notification-uncatalogued",
                nudge_log_id=uncatalogued.id,
                rule_id=inactive.id,
                user_id=uncatalogued.user_id,
                created_at=observed_at,
            ),
            make_notification(
                notification_id="notification-other-rec",
                nudge_log_id=other_community.id,
                rule_id=rule.id,
                user_id=other_community.user_id,
                created_at=observed_at,
                read_at=observed_at,
            ),
            DeliveryLog(
                id="delivery-one",
                nudge_id=first.id,
                channel="webpush",
                destination="private-push-destination",
                status="sent",
                created_at=observed_at,
                sent_at=observed_at,
            ),
            DeliveryLog(
                id="delivery-two",
                nudge_id=second.id,
                channel="email",
                destination="bob@example.test",
                status="failed",
                error="SMTP connection refused: private-host",
                created_at=observed_at,
            ),
            WebPushSubscription(
                id="subscription-one",
                user_id=first.user_id,
                community_id=COMMUNITY,
                endpoint="https://private.example/push/alice",
                p256dh="key",
                auth="secret",
                enabled=True,
            ),
            WebPushSubscription(
                id="subscription-two",
                user_id=second.user_id,
                community_id=COMMUNITY,
                endpoint="https://private.example/push/bob",
                p256dh="key",
                auth="secret",
                enabled=False,
            ),
            UserPreference(
                id="preference-one",
                user_id=first.user_id,
                community_id=COMMUNITY,
                channel_email=True,
                email="alice@example.test",
            ),
            UserPreference(
                id="preference-two",
                user_id=second.user_id,
                community_id=COMMUNITY,
                channel_email=False,
            ),
        ]
    )
    await db.commit()


async def test_analytics_are_scoped_aggregate_and_complete(analytics_client, db) -> None:
    """@verifies REQ-0077
    @verifies REQ-0078
    """
    await _seed_analytics(db)

    response = await analytics_client.get(
        f"/admin/analytics/communities/{COMMUNITY}/conversion",
        params={"start": "2026-08-01", "end": "2026-08-31"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["community_id"] == COMMUNITY
    assert {item["id"]: item["count"] for item in payload["steps"]} == {
        "sent": 3,
        "delivered": 3,
        "read": 1,
        "clicked": 1,
        "committed": 1,
    }
    by_rule = {item["id"]: item for item in payload["rules"]}
    assert set(by_rule) == {"flexibility_opportunity"}
    assert by_rule["flexibility_opportunity"]["volume"] == 2
    assert by_rule["flexibility_opportunity"]["channel"] == "webpush"
    assert "seasonal_tip" not in by_rule
    assert "community_campaign" not in by_rule
    assert payload["failures"] == [
        {"channel": "email", "error_class": "email_transport", "count": 1}
    ]
    assert payload["reachability"] == [
        {"channel": "webpush", "reachable": 1, "total": 2, "opted_out": 1},
        {"channel": "email", "reachable": 1, "total": 2, "opted_out": 1},
    ]
    serialized = response.text
    for private_value in (
        "user-alice",
        "user-bob",
        "user-charlie",
        "alice@example.test",
        "bob@example.test",
        "Private title",
        "Private message",
        "private-push-destination",
        "private-host",
    ):
        assert private_value not in serialized

    assert len((await db.execute(select(NudgeLog))).scalars().all()) == 5


async def test_analytics_require_dedicated_scope(user_client, ingest_client) -> None:
    """@verifies REQ-0076"""
    path = f"/admin/analytics/communities/{COMMUNITY}/conversion"
    params = {"start": "2026-08-01", "end": "2026-08-31"}

    assert (await user_client.get(path, params=params)).status_code == 403
    assert (await ingest_client.get(path, params=params)).status_code == 403


async def test_admin_may_read_analytics(admin_client) -> None:
    """@verifies REQ-0076"""
    response = await admin_client.get(
        f"/admin/analytics/communities/{COMMUNITY}/conversion",
        params={"start": "2026-08-01", "end": "2026-08-31"},
    )
    assert response.status_code == 200


async def test_analytics_reject_an_inverted_date_range(analytics_client) -> None:
    """@verifies REQ-0077"""
    response = await analytics_client.get(
        f"/admin/analytics/communities/{COMMUNITY}/conversion",
        params={"start": "2026-09-01", "end": "2026-08-31"},
    )
    assert response.status_code == 422
