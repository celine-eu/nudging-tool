"""The authorisation rules, evaluated against the real `policies/celine/nudging/authz.rego`.

Unlike `../celine-grid`, this service **fails closed**: `_extract_bool` defaults to
`False`, so an evaluation that returns nothing denies. That makes an *allow* here real
evidence rather than the indistinguishable-from-broken result it is there. The
session-wide `policy_engine_is_loaded` fixture still refuses to run the suite without a
bundle, because a suite of denials that pass for the wrong reason is the other half of
the same mistake.

Where a docstring in `security/policies.py` and the Rego disagree, **the Rego is what
runs** and these tests pin the Rego.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from celine.nudging.security.policies import (
    _extract_bool,
    _make_policy_input,
    _subject_from_user,
    require_admin,
    require_ingest,
)
from tests.fakes import make_admin, make_user

_PACKAGE = "celine.nudging.authz"


@pytest.fixture
def engine(policy_engine_is_loaded):
    return policy_engine_is_loaded


def _query(engine, user, rule: str, action: str = "access") -> bool:
    policy_input = _make_policy_input(user, action=action)
    raw = engine.evaluate(
        f"data.{_PACKAGE}.{rule}", engine._build_input_dict(policy_input)
    )
    return _extract_bool(raw)


# ---------------------------------------------------------------------------
# The bundle is loaded, and it is this repository's bundle
# ---------------------------------------------------------------------------


# @verifies REQ-0003
def test_the_authz_package_is_the_one_that_loaded(engine):
    """
    The service refuses to start without a bundle — `init_policy_engine()` raises on a
    missing directory — so there is no permissive fallback to fall into. What is worth
    asserting is therefore that the package that loaded is the one every dependency
    queries by name: a bundle that loads but does not define `celine.nudging.authz`
    would deny every request rather than allow it, which is safe and completely opaque.
    """
    assert engine.has_package(_PACKAGE)


# @verifies REQ-0003
def test_a_missing_policies_directory_stops_the_service_rather_than_allowing(
    monkeypatch, tmp_path
):
    from celine.nudging.security import policies as policies_module

    monkeypatch.setattr(
        policies_module.settings.policies, "policies_dir", tmp_path / "absent"
    )
    with pytest.raises(ValueError, match="does not exists"):
        policies_module.init_policy_engine()


# @verifies REQ-0003
def test_an_unset_policies_directory_stops_the_service_too(monkeypatch):
    from celine.nudging.security import policies as policies_module

    monkeypatch.setattr(policies_module.settings.policies, "policies_dir", None)
    with pytest.raises(ValueError, match="POLICIES_DIR not set"):
        policies_module.init_policy_engine()


# ---------------------------------------------------------------------------
# Failing closed
# ---------------------------------------------------------------------------


# @verifies REQ-0004
def test_an_evaluation_that_returns_nothing_denies():
    """
    This is the property that makes an *allow* mean something here. Every shape the
    engine can hand back other than an explicit `true` — an empty result, a missing
    expression, a non-boolean — has to reach `False`, because the alternative is a
    permission granted by a typo in a query string.
    """
    assert _extract_bool({}) is False
    assert _extract_bool({"result": []}) is False
    assert _extract_bool({"result": [{}]}) is False
    assert _extract_bool({"result": [{"expressions": []}]}) is False
    assert _extract_bool({"result": [{"expressions": [{"value": None}]}]}) is False
    assert _extract_bool({"result": [{"expressions": [{"value": "true"}]}]}) is False
    assert _extract_bool(None) is False


# @verifies REQ-0004
def test_an_explicit_true_is_the_only_grant():
    assert _extract_bool({"result": [{"expressions": [{"value": True}]}]}) is True
    assert _extract_bool({"result": [{"expressions": [{"value": False}]}]}) is False


# @verifies REQ-0004
def test_a_query_for_a_rule_that_does_not_exist_denies(engine):
    """
    Regorus answers an undefined rule with an empty result rather than an error, so a
    renamed rule in the bundle is silently a denial everywhere. That is the safe
    direction, and it is the reason the package assertion above exists: nothing else
    would tell you.
    """
    raw = engine.evaluate(
        f"data.{_PACKAGE}.is_superuser",
        engine._build_input_dict(_make_policy_input(make_admin(), action="admin")),
    )
    assert _extract_bool(raw) is False


# ---------------------------------------------------------------------------
# Who is admin
# ---------------------------------------------------------------------------


# @verifies REQ-0005
def test_the_admin_scope_grants_admin(engine):
    assert _query(engine, make_admin(), "is_admin", "admin") is True


# @verifies REQ-0005
def test_the_admin_group_grants_admin(engine):
    """
    Two independent sources, and a participant carries neither. The group is read
    through `extract_groups`, which also flattens organisation-level groups — so a
    member of an organisation whose org group is `admin` is an administrator here.
    """
    assert _query(engine, make_admin(by_group=True), "is_admin", "admin") is True


# @verifies REQ-0005
def test_an_organisation_level_admin_group_grants_admin(engine):
    user = make_user(sub="user-org-admin")
    user.claims["organization"] = {"celine": {"groups": ["/admin"]}}
    assert _query(engine, user, "is_admin", "admin") is True


# @verifies REQ-0007
def test_a_participant_is_not_an_admin(engine):
    assert _query(engine, make_user(), "is_admin", "admin") is False


# @verifies REQ-0007
def test_a_service_account_is_not_an_admin_by_being_a_service(engine):
    """
    A service account is a subject like any other. Being machine-issued grants nothing:
    `svc-flexibility` may ingest and may not administer, and the only thing that would
    change that is the scope on its token.
    """
    assert _query(engine, make_ingest(), "is_admin", "admin") is False


def make_ingest():
    from tests.fakes import make_ingest_service

    return make_ingest_service()


# ---------------------------------------------------------------------------
# Who may ingest
# ---------------------------------------------------------------------------


# @verifies REQ-0006
def test_the_ingest_scope_grants_ingest(engine):
    assert _query(engine, make_ingest(), "is_ingest", "ingest") is True


# @verifies REQ-0006
def test_an_admin_may_also_ingest(engine):
    """
    `is_ingest` is defined as its own scope *or* `is_admin`. The reverse does not hold —
    see the ingest-only service above — which is the asymmetry that keeps a sender's
    token from reading everyone's notifications.
    """
    assert _query(engine, make_admin(), "is_ingest", "ingest") is True
    assert _query(engine, make_admin(by_group=True), "is_ingest", "ingest") is True


# @verifies REQ-0007
def test_a_participant_may_not_ingest(engine):
    assert _query(engine, make_user(), "is_ingest", "ingest") is False


# @verifies REQ-0006
def test_an_ingest_service_may_not_administer(engine):
    assert _query(engine, make_ingest(), "is_admin", "admin") is False


# ---------------------------------------------------------------------------
# allow, which is not authorisation
# ---------------------------------------------------------------------------


# @verifies REQ-0008
def test_any_identified_non_anonymous_subject_is_allowed(engine):
    """
    `allow` says only that somebody is there. Every participant satisfies it, which is
    why no route depends on it: the two dependencies query `is_admin` and `is_ingest`
    directly. A route that guarded itself with `allow` would be open to every logged-in
    user in the realm.
    """
    assert _query(engine, make_user(), "allow") is True
    assert _query(engine, make_ingest(), "allow") is True
    assert _query(engine, make_user(), "is_admin", "admin") is False


# @verifies REQ-0008
def test_a_subject_with_no_id_is_not_allowed(engine):
    nobody = make_user(sub="")
    assert _query(engine, nobody, "allow") is False


# ---------------------------------------------------------------------------
# What the subject is built from
# ---------------------------------------------------------------------------


# @verifies REQ-0009
def test_a_service_account_is_typed_as_a_service(engine):
    """
    The type is what the `filters` rule branches on, and `is_service_account()` reads
    `preferred_username`. A service whose username does not carry the
    `service-account-` prefix is typed USER and would be handed a row filter meant for a
    person.
    """
    assert _subject_from_user(make_ingest()).type.value == "service"
    assert _subject_from_user(make_user()).type.value == "user"


# @verifies REQ-0005
def test_scopes_are_accepted_as_a_string_or_a_list():
    """
    Keycloak sends a space-separated string; some IdPs send a list. Both have to reach
    the policy as a list, because the Rego tests membership with `in` — a raw string
    would match nothing and quietly demote every administrator.
    """
    from celine.nudging.security.policies import _scopes_from_user

    user = make_user(scope="nudging.admin nudging.ingest")
    assert _scopes_from_user(user) == ["nudging.admin", "nudging.ingest"]

    listed = make_user()
    listed.claims["scope"] = ["nudging.admin"]
    assert _scopes_from_user(listed) == ["nudging.admin"]

    absent = make_user()
    absent.claims["scope"] = None
    assert _scopes_from_user(absent) == []


# @verifies REQ-0010
def test_the_bundle_declares_a_row_filter_for_a_user_and_none_for_a_service(engine):
    """
    The bundle publishes `filters`, and **nothing in this service reads it** — the
    notification routes filter on the caller's identifiers in SQL instead. It is pinned
    here so that the day someone wires it up they find out what it already says, rather
    than discovering that a service account is given an empty filter and therefore sees
    every row.
    """
    user_filters = engine.evaluate(
        f"data.{_PACKAGE}.filters",
        engine._build_input_dict(_make_policy_input(make_user(sub="alice"))),
    )
    value = user_filters["result"][0]["expressions"][0]["value"]
    assert value == [{"field": "user_id", "operator": "eq", "value": "alice"}]

    service_filters = engine.evaluate(
        f"data.{_PACKAGE}.filters",
        engine._build_input_dict(_make_policy_input(make_ingest())),
    )
    assert service_filters["result"][0]["expressions"][0]["value"] == []


# ---------------------------------------------------------------------------
# The dependencies, which is where a denial becomes a 403
# ---------------------------------------------------------------------------


# @verifies REQ-0007
def test_require_admin_raises_403_for_a_participant(engine):
    with pytest.raises(HTTPException) as exc:
        require_admin(user=make_user(), engine=engine)
    assert exc.value.status_code == 403


# @verifies REQ-0007
def test_require_ingest_raises_403_for_a_participant(engine):
    with pytest.raises(HTTPException) as exc:
        require_ingest(user=make_user(), engine=engine)
    assert exc.value.status_code == 403


# @verifies REQ-0005
def test_require_admin_returns_the_caller_when_the_policy_grants_it(engine):
    admin = make_admin()
    assert require_admin(user=admin, engine=engine) is admin


# @verifies REQ-0006
def test_require_ingest_returns_the_caller_when_the_policy_grants_it(engine):
    service = make_ingest()
    assert require_ingest(user=service, engine=engine) is service


# @verifies REQ-0008
def test_the_policy_is_asked_about_nudging_and_nothing_else():
    """
    Every decision in this service is made about one resource — `userdata:nudging` — and
    the action is the only thing that varies. There is no per-notification or
    per-community authorisation: a caller who may administer may administer everything.
    """
    admin_input = _make_policy_input(make_admin(), action="admin")
    assert admin_input.resource.id == "nudging"
    assert admin_input.resource.type.value == "userdata"
    assert admin_input.action.name == "admin"
    assert _make_policy_input(make_user()).action.name == "access"
