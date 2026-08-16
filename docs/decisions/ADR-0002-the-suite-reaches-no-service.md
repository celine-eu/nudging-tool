# ADR-0002 — the suite reaches no service, and the Rego bundle is the exception

**Date:** 2026-08-15
**Status:** accepted

## Context

This service talks to PostgreSQL, Keycloak's JWKS, a web-push service and an SMTP relay.
A suite that needed any of them would be a suite nobody runs before pushing, and the
argument for testing this repository at all is that its failures are invisible — a suite
that is skipped removes the only thing that would notice.

Two sibling repositories had already answered this under the same constraint:
`../celine-grid` (247 tests, ~11s) and `../celine-policies` (480 tests, <7s). Inventing a
third shape would have cost the reader who moves between them.

## Decision

Fake every outbound boundary at the narrowest point that leaves our code running, and
**evaluate the real Rego bundle**.

| Real thing | Replaced by | Left real |
|---|---|---|
| PostgreSQL | in-memory SQLite carrying `Base.metadata` | every query, the schema, the constraints |
| Keycloak JWKS | a token registry replacing `JwtUser.from_token` | the middleware, the open-path list, the 401 branches |
| a web-push service | a callable replacing `pywebpush.webpush` | subscription selection, the payload, the delivery log |
| an SMTP relay | an object replacing the `smtplib` module | message construction, the TLS branch, the delivery log |
| `policies/celine/nudging/authz.rego` | **nothing** | the whole bundle |

The bundle is real because `celine.sdk.policies` evaluates Rego **in process** through
`regorus` — no server, no socket — so there is nothing to fake and no cost to not faking
it. `conftest.py` calls the same `init_policy_engine()` the service calls at startup, and
refuses to run the suite unless the `celine.nudging.authz` package is in the loaded
bundle.

Environment is pinned in `conftest.py` **before** the first `celine.nudging` import,
because `settings.py` builds its `Settings()` at import time and `src/celine/nudging/db/session.py` builds
the engine from it. That includes `DATABASE_URL` — pointed at an unroutable DSN so a
missed override fails rather than connects — and `SMTP_HOST`, emptied so that a test
reaching the email publisher by accident fails locally instead of dialling a developer's
relay.

## Consequences

**The suite runs anywhere, in seconds, with no `docker compose`.** That is the property
being bought, and it is the reason the tests will still be run in a year.

**An allow from the policy means something here**, unlike in `../celine-grid`: this
service fails closed (REQ-0003, REQ-0004). The loaded-bundle guard is kept anyway, because
a bundle that failed to load would deny everything and every `403` in the suite would pass
for the wrong reason.

**Nothing tests the wiring to the real services.** A JWKS misconfiguration, an
unreachable push endpoint, a relay that rejects the sender address: none of them appear
here. What is faked is what the fake asserts — `pywebpush` is called with the right
arguments, not that a browser received anything.

**A fake can drift from the library it stands for.** `FakeWebPush` raises the *real*
`WebPushException` for exactly this reason: the publisher catches it by name, and a
look-alike would escape the handler and quietly change what is being tested.

Superseded only by a decision to run the real services, which would mean accepting that
the suite is not run on every change.
