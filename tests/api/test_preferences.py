"""`/preferences` — the screen where a participant decides what reaches them.

Everything a participant can refuse is here, and everything they cannot is here by
omission: the catalogue is `seed/active_kinds.yaml`, and a rule of a kind that file does
not list is delivered whatever this screen says (REQ-0036).
"""

from __future__ import annotations

from sqlalchemy import select

from celine.nudging.db.models import UserPreference
from tests.conftest import USER_SUB
from tests.fakes import make_preference, make_user

CATALOGUE_KINDS = {"flexibility_opportunity", "meter_anomaly", "extr_event"}


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


# @verifies REQ-0058
async def test_a_participant_with_no_row_gets_the_defaults(user_client):
    """
    No row is written on read: a participant who never opened the screen has no
    preference row, and `orchestrate` treats that as three a day and every kind enabled.
    What is returned here says the same thing without storing it.
    """
    response = await user_client.get("/preferences/me")
    body = response.json()

    assert response.status_code == 200
    assert body["lang"] == "en"
    assert body["max_per_day"] == 3
    assert body["channel_email"] is False
    assert body["email"] is None
    assert set(body["enabled_notification_kinds"]) == CATALOGUE_KINDS


# @verifies REQ-0058
async def test_a_stored_preference_is_what_is_returned(user_client, db):
    db.add(
        make_preference(
            USER_SUB,
            lang="it",
            max_per_day=7,
            channel_email=True,
            email="alice@example.test",
            consents={"enabled_notification_kinds": ["flexibility_opportunity"]},
        )
    )
    await db.commit()

    body = (await user_client.get("/preferences/me")).json()

    assert body["lang"] == "it"
    assert body["max_per_day"] == 7
    assert body["channel_email"] is True
    assert body["email"] == "alice@example.test"
    assert set(body["enabled_notification_kinds"]) == {
        "flexibility_opportunity",
        "extr_event",
    }, "the non-editable kind is always in the list"


# @verifies REQ-0058
async def test_an_unsupported_language_falls_back_to_the_default(user_client, db):
    """
    The endpoint knows four languages — `en`, `it`, `es`, `ca` — and a stored value
    outside that set is reported as the default rather than echoed back. The catalogue
    itself is only translated into three (REQ-0069), so `ca` is accepted here and reads
    in English.
    """
    db.add(make_preference(USER_SUB, lang="de"))
    await db.commit()

    assert (await user_client.get("/preferences/me")).json()["lang"] == "en"


# ---------------------------------------------------------------------------
# The catalogue
# ---------------------------------------------------------------------------


# @verifies REQ-0060
async def test_the_catalogue_is_localised_for_the_reader(user_client):
    """
    One entry per kind, with the label, description and cadence already resolved to a
    single language — the client never sees the i18n maps.
    """
    english = (await user_client.get("/preferences/catalog?lang=en")).json()
    italian = (await user_client.get("/preferences/catalog?lang=it")).json()

    assert {item["kind"] for item in english} == CATALOGUE_KINDS
    assert all(isinstance(item["label"], str) for item in english)

    by_kind = {item["kind"]: item for item in english}
    italian_by_kind = {item["kind"]: item for item in italian}
    assert by_kind["extr_event"]["label"] != italian_by_kind["extr_event"]["label"]


# @verifies REQ-0060
async def test_the_catalogue_says_which_kinds_may_not_be_refused(user_client):
    """
    `editable: false` is how the screen knows to render a switch that cannot be turned
    off. Weather alerts are the one such kind today, and they are also reported as
    enabled whatever the participant stored.
    """
    catalogue = {item["kind"]: item for item in (await user_client.get("/preferences/catalog")).json()}

    assert catalogue["extr_event"]["editable"] is False
    assert catalogue["extr_event"]["enabled"] is True
    assert catalogue["flexibility_opportunity"]["editable"] is True


# @verifies REQ-0075
async def test_an_unknown_language_falls_back_to_english(user_client):
    catalogue = (await user_client.get("/preferences/catalog?lang=de")).json()
    english = (await user_client.get("/preferences/catalog?lang=en")).json()

    assert catalogue == english


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


# @verifies REQ-0059
async def test_a_first_save_creates_the_row_under_the_canonical_identifier(user_client, db):
    """
    The row is written under the token's `sub`, not under whichever alias the caller was
    recognised by. Orchestration looks preferences up by the user id on the *nudge*,
    which is whatever the sender used — so an alias mismatch here is how a participant's
    settings quietly stop applying.
    """
    response = await user_client.put(
        "/preferences/me", json={"max_per_day": 5, "lang": "it"}
    )

    assert response.status_code == 200
    stored = (await db.execute(select(UserPreference))).scalar_one()
    assert stored.user_id == USER_SUB
    assert stored.max_per_day == 5
    assert stored.lang == "it"


# @verifies REQ-0059
async def test_a_row_stored_under_an_alias_is_moved_to_the_canonical_identifier(
    app, fake_jwt, db
):
    """
    A participant whose preferences were seeded under their username keeps them: the row
    is renamed rather than duplicated. If a canonical row already exists, the canonical
    one wins and the alias row is left behind untouched — two rows, one of them
    orphaned, which is also how a participant ends up with the two rows that break
    language resolution (REQ-0029).
    """
    from httpx import ASGITransport, AsyncClient

    user = fake_jwt.register(
        make_user(sub="uuid-4bd1", preferred_username="alice", email="alice@example.test")
    )
    db.add(make_preference("alice", max_per_day=9))
    await db.commit()

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {user.token}"},
    ) as client:
        response = await client.put("/preferences/me", json={"max_per_day": 4})

    assert response.status_code == 200
    rows = (await db.execute(select(UserPreference))).scalars().all()
    assert [(row.user_id, row.max_per_day) for row in rows] == [("uuid-4bd1", 4)]


# @verifies REQ-0060
async def test_the_kinds_a_participant_chooses_are_stored_and_filtered(user_client, db):
    """
    Only kinds the catalogue lists are stored, so a client sending an invented one does
    not poison the row — and a required kind is appended whether or not it was sent.
    """
    response = await user_client.put(
        "/preferences/me",
        json={
            "max_per_day": 3,
            "enabled_notification_kinds": ["flexibility_opportunity", "invented_kind"],
        },
    )

    assert response.status_code == 200
    stored = (await db.execute(select(UserPreference))).scalar_one()
    assert stored.consents["enabled_notification_kinds"] == [
        "flexibility_opportunity",
        "extr_event",
    ]


# @verifies REQ-0037
async def test_switching_everything_off_leaves_the_kinds_that_may_not_be_refused(
    user_client, db
):
    await user_client.put(
        "/preferences/me", json={"max_per_day": 3, "enabled_notification_kinds": []}
    )

    stored = (await db.execute(select(UserPreference))).scalar_one()
    assert stored.consents["enabled_notification_kinds"] == ["extr_event"]


# @verifies REQ-0059
async def test_email_may_not_be_enabled_without_a_valid_address(user_client, db):
    """
    Validated at the edge rather than at delivery, because an email channel with no
    address is a preference that silently does nothing: `_build_delivery_jobs` skips the
    email job and nothing is recorded.
    """
    assert (
        await user_client.put(
            "/preferences/me", json={"max_per_day": 3, "channel_email": True}
        )
    ).status_code == 422

    assert (
        await user_client.put(
            "/preferences/me",
            json={"max_per_day": 3, "channel_email": True, "email": "not-an-email"},
        )
    ).status_code == 422

    ok = await user_client.put(
        "/preferences/me",
        json={"max_per_day": 3, "channel_email": True, "email": " alice@example.test "},
    )
    assert ok.status_code == 200
    assert (await db.execute(select(UserPreference))).scalar_one().email == "alice@example.test"


# @verifies REQ-0039
async def test_the_daily_cap_is_bounded_at_the_edge(user_client):
    """
    One to ten. The database has no such constraint — a seed can write anything,
    including a zero that silences the participant completely — so this bound applies to
    participants and not to operators.
    """
    assert (await user_client.put("/preferences/me", json={"max_per_day": 0})).status_code == 422
    assert (await user_client.put("/preferences/me", json={"max_per_day": 11})).status_code == 422
    assert (await user_client.put("/preferences/me", json={})).status_code == 422
    assert (await user_client.put("/preferences/me", json={"max_per_day": 10})).status_code == 200


# @verifies REQ-0059
async def test_omitted_fields_are_left_as_they_were(user_client, db):
    """
    Everything except `max_per_day` is optional and absent means "unchanged" — with one
    exception: `max_per_day` is required on every request, so a client that only wants
    to change a language must know the current cap and send it back.
    """
    db.add(
        make_preference(
            USER_SUB,
            lang="it",
            max_per_day=5,
            channel_email=True,
            email="alice@example.test",
            consents={"enabled_notification_kinds": ["meter_anomaly"]},
        )
    )
    await db.commit()

    await user_client.put("/preferences/me", json={"max_per_day": 6})

    stored = (await db.execute(select(UserPreference))).scalar_one()
    assert stored.max_per_day == 6
    assert stored.lang == "it"
    assert stored.channel_email is True
    assert stored.email == "alice@example.test"
    assert stored.consents["enabled_notification_kinds"] == ["meter_anomaly"]


# @verifies REQ-0058
async def test_an_unsupported_language_is_ignored_rather_than_stored(user_client, db):
    db.add(make_preference(USER_SUB, lang="it"))
    await db.commit()

    await user_client.put("/preferences/me", json={"max_per_day": 3, "lang": "de"})

    assert (await db.execute(select(UserPreference))).scalar_one().lang == "it"


# @verifies REQ-0002
async def test_preferences_need_a_token(client):
    assert (await client.get("/preferences/me")).status_code == 401
    assert (await client.get("/preferences/catalog")).status_code == 401
    assert (await client.put("/preferences/me", json={"max_per_day": 3})).status_code == 401
