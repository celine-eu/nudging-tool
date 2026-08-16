"""The four boundaries this service has, and the row builders the suite shares.

Each fake sits at the narrowest point that still leaves our code running:

| Real thing | Faked as | Left real |
|---|---|---|
| Keycloak JWKS | `FakeJwt` replacing `JwtUser.from_token` | the middleware, the open-path list |
| a web-push service | `FakeWebPush` replacing `pywebpush.webpush` | subscription selection, payload, delivery log |
| an SMTP server | `FakeSmtp` replacing the `smtplib` module | message construction, TLS branch, delivery log |
| PostgreSQL | SQLite (`conftest.py`) | every query, the schema, the constraints |
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from celine.sdk.auth import JwtUser

from celine.nudging.db.models import (
    Notification,
    NudgeLog,
    Rule,
    Template,
    UserPreference,
)

# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


def make_user(
    *,
    sub: str = "user-alice",
    email: str | None = "alice@example.test",
    preferred_username: str | None = None,
    scope: str = "",
    groups: list[str] | None = None,
) -> JwtUser:
    """A participant token: no scopes, no groups unless a test asks for them."""
    claims: dict[str, Any] = {
        "sub": sub,
        "scope": scope,
        "email": email,
        "preferred_username": preferred_username or sub,
    }
    if groups:
        claims["groups"] = groups
    return JwtUser(
        sub=sub,
        email=email,
        preferred_username=preferred_username or sub,
        claims=claims,
        token=f"token-{sub}-{uuid4().hex[:8]}",
    )


def make_admin(sub: str = "user-admin", *, by_group: bool = False) -> JwtUser:
    """An administrator, by scope or by group — the Rego grants either."""
    if by_group:
        return make_user(sub=sub, groups=["admin"])
    return make_user(sub=sub, scope="nudging.admin")


def make_service(
    *, sub: str = "svc-sender", client_id: str = "svc-sender", scope: str = ""
) -> JwtUser:
    """A Keycloak client-credentials token.

    `preferred_username` is what `is_service_account()` trusts first, so a service that
    does not carry the `service-account-` prefix is typed as a user and evaluated under
    the user branch of the policy.
    """
    claims: dict[str, Any] = {
        "sub": sub,
        "scope": scope,
        "client_id": client_id,
        "preferred_username": f"service-account-{client_id}",
    }
    return JwtUser(
        sub=sub,
        email=None,
        preferred_username=f"service-account-{client_id}",
        claims=claims,
        token=f"token-{sub}-{uuid4().hex[:8]}",
    )


def make_ingest_service(sub: str = "svc-flexibility") -> JwtUser:
    return make_service(sub=sub, client_id=sub, scope="nudging.ingest")


class FakeJwt:
    """A token registry standing in for Keycloak.

    `register(user)` makes that user's opaque token string resolvable; anything else
    raises, which is the branch the middleware turns into a `401`.
    """

    def __init__(self) -> None:
        self._by_token: dict[str, JwtUser] = {}

    def register(self, user: JwtUser) -> JwtUser:
        assert user.token, "a registered user needs a token to be addressed by"
        self._by_token[user.token] = user
        return user

    def from_token(self, header: str, _oidc: Any, *_a: Any, **_kw: Any) -> JwtUser:
        if header is None or not header.strip():
            raise ValueError("JWT is missing or empty")
        token = header.split(" ")[1] if "bearer" in header.lower() else header
        try:
            return self._by_token[token]
        except KeyError as exc:
            raise ValueError("Invalid or expired token") from exc


def install_fake_jwt(monkeypatch) -> FakeJwt:
    fake = FakeJwt()
    monkeypatch.setattr(
        "celine.nudging.security.auth.JwtUser.from_token", fake.from_token
    )
    return fake


# ---------------------------------------------------------------------------
# Web push
# ---------------------------------------------------------------------------


@dataclass
class _FakeResponse:
    status_code: int
    text: str = "fake response"


def _webpush_failure(message: str, status_code: int | None) -> Exception:
    """The real `WebPushException`, carrying a response the publisher can read.

    It must be the real class: `send_webpush` catches `WebPushException` by name, so a
    look-alike would escape the handler and change what is being tested. What is faked
    is the response object hanging off it, because the publisher only ever reads
    `.response.status_code` — and a `None` response is the case where the library failed
    before it got an answer.
    """
    from pywebpush import WebPushException

    return WebPushException(
        message, response=_FakeResponse(status_code) if status_code is not None else None
    )


@dataclass
class FakeWebPush:
    """Records every call; raises for endpoints a test marks as failing."""

    calls: list[dict[str, Any]] = field(default_factory=list)
    failures: dict[str, tuple[str, int | None]] = field(default_factory=dict)

    def fail(self, endpoint: str, *, status_code: int | None, message: str = "boom"):
        self.failures[endpoint] = (message, status_code)

    def __call__(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)
        endpoint = kwargs["subscription_info"]["endpoint"]
        if endpoint in self.failures:
            message, status_code = self.failures[endpoint]
            raise _webpush_failure(message, status_code)

    @property
    def endpoints(self) -> list[str]:
        return [c["subscription_info"]["endpoint"] for c in self.calls]


# ---------------------------------------------------------------------------
# SMTP
# ---------------------------------------------------------------------------


@dataclass
class SentMail:
    to: str
    subject: str
    body: str
    used_tls: bool
    used_ssl: bool
    logged_in: str | None


class FakeSmtp:
    """Stands in for the whole `smtplib` module.

    The publisher chooses between `SMTP_SSL` and `SMTP` + `starttls()`, so both classes
    exist here and record which path was taken.
    """

    def __init__(self) -> None:
        self.sent: list[SentMail] = []
        self.fail_with: Exception | None = None

        outer = self

        class _Connection:
            use_ssl = False

            def __init__(self, host: str, port: int, context: Any = None) -> None:
                self.host = host
                self.port = port
                self._tls = False
                self._login: str | None = None

            def __enter__(self):
                return self

            def __exit__(self, *_exc: Any) -> None:
                return None

            def starttls(self, context: Any = None) -> None:
                self._tls = True

            def login(self, username: str, _password: str) -> None:
                self._login = username

            def send_message(self, msg: Any) -> None:
                if outer.fail_with is not None:
                    raise outer.fail_with
                outer.sent.append(
                    SentMail(
                        to=msg["To"],
                        subject=msg["Subject"],
                        body=msg.get_content(),
                        used_tls=self._tls,
                        used_ssl=self.use_ssl,
                        logged_in=self._login,
                    )
                )

        class _SMTP(_Connection):
            use_ssl = False

        class _SMTP_SSL(_Connection):
            use_ssl = True

        self.SMTP = _SMTP
        self.SMTP_SSL = _SMTP_SSL

    @property
    def recipients(self) -> list[str]:
        return [m.to for m in self.sent]


# ---------------------------------------------------------------------------
# Row builders
#
# Every one takes the fields a test cares about and defaults the rest to something
# valid, so a test reads as the one thing it is about.
# ---------------------------------------------------------------------------


def make_rule(
    rule_id: str = "flexibility_opportunity",
    *,
    name: str = "A rule",
    enabled: bool = True,
    family: str = "energy",
    type: str = "alert",
    severity: str = "info",
    definition: dict | None = None,
    scenarios: list[str] | None = None,
) -> Rule:
    definition = dict(definition or {})
    if scenarios is not None:
        definition.setdefault("scenarios", list(scenarios))
    return Rule(
        id=rule_id,
        name=name,
        enabled=enabled,
        family=family,
        type=type,
        severity=severity,
        definition=definition,
        scenarios=list(definition.get("scenarios") or []),
    )


def make_template(
    rule_id: str = "flexibility_opportunity",
    *,
    lang: str = "en",
    title: str = "Title",
    body: str = "Body",
) -> Template:
    return Template(
        id=f"tpl_{rule_id}_{lang}",
        rule_id=rule_id,
        lang=lang,
        title_jinja=title,
        body_jinja=body,
    )


def make_preference(
    user_id: str = "user-alice",
    *,
    community_id: str | None = None,
    lang: str = "en",
    max_per_day: int = 3,
    channel_email: bool = False,
    email: str | None = None,
    consents: dict | None = None,
) -> UserPreference:
    return UserPreference(
        id=uuid4().hex,
        user_id=user_id,
        community_id=community_id,
        lang=lang,
        max_per_day=max_per_day,
        channel_email=channel_email,
        email=email,
        consents=dict(consents or {}),
    )


def make_nudge_log(
    *,
    nudge_id: str | None = None,
    rule_id: str = "flexibility_opportunity",
    user_id: str = "user-alice",
    community_id: str | None = None,
    dedup_key: str | None = None,
    status: str = "created",
    payload: dict | None = None,
) -> NudgeLog:
    nudge_id = nudge_id or uuid4().hex
    return NudgeLog(
        id=nudge_id,
        rule_id=rule_id,
        user_id=user_id,
        community_id=community_id,
        dedup_key=dedup_key or f"{rule_id}:{user_id}::{nudge_id}",
        status=status,
        payload=dict(payload or {}),
    )


def make_notification(
    *,
    nudge_log_id: str | None = None,
    notification_id: str | None = None,
    rule_id: str = "flexibility_opportunity",
    user_id: str = "user-alice",
    family: str = "energy",
    type: str = "alert",
    severity: str = "info",
    title: str = "Title",
    body: str = "Body",
    status: str = "pending",
    created_at: datetime | None = None,
    read_at: datetime | None = None,
    clicked_at: datetime | None = None,
    deleted_at: datetime | None = None,
) -> Notification:
    return Notification(
        read_at=read_at,
        clicked_at=clicked_at,
        deleted_at=deleted_at,
        id=notification_id or uuid4().hex,
        nudge_log_id=nudge_log_id,
        rule_id=rule_id,
        user_id=user_id,
        family=family,
        type=type,
        severity=severity,
        title=title,
        body=body,
        status=status,
        created_at=created_at or datetime.now(timezone.utc),
    )


async def seed_rule(
    db,
    rule_id: str = "flexibility_opportunity",
    *,
    langs: tuple[str, ...] = ("en",),
    title: str = "Title",
    body: str = "Body",
    **rule_kwargs: Any,
) -> Rule:
    """A rule and its templates, committed. The pairing is what the engine needs."""
    rule = make_rule(rule_id, **rule_kwargs)
    db.add(rule)
    for lang in langs:
        db.add(make_template(rule_id, lang=lang, title=title, body=body))
    await db.commit()
    return rule
