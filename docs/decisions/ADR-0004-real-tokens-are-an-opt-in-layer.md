# ADR-0004 — real tokens are an opt-in layer, and the default suite still reaches no service

**Date:** 2026-10-03
**Status:** accepted

## Context

Who is an administrator moved from groups to the realm role `platform-admin` (REQ-0005,
REQ-0082). The claims that decide it come from Keycloak in three shapes that look alike:
`realm_access.roles`, a retired top-level `groups` claim, and `organization.<alias>.groups`,
whose paths (`/admins`) are the same strings the retired realm groups had. The unit and API
layers use claims built by hand (`tests/fakes.py`), so they prove the policy against the
shapes someone wrote down, not against the shapes the realm issues.

ADR-0002 keeps the default suite free of every service, and that property is still wanted.

## Decision

Add `tests/integration/`, a layer that mints tokens from the **local** Keycloak, verifies
them with the service's own `JwtUser.from_token` and audience, and drives the real
middleware and Rego. It is skipped unless `NUDGING_REAL_TOKENS=1`, so `task test` and a
plain `pytest` still reach nothing. It refuses an issuer that is not on a local hostname.

A token carrying a retired realm group cannot be minted from a converged realm, so it is
passed in already minted (`NUDGING_LEGACY_ADMIN_TOKEN`); its tests skip, with that reason,
when it is not.

## Consequences

**The skip is visible, not silent.** A run without the variable reports the layer as
skipped with its reason, and must be reported that way.

**The layer depends on the local realm's dev users** (`admin` holding `platform-admin`,
`org-admin` holding only an organisation's `admins`). Each test asserts the shape it relies
on first, so drift in the realm fails as drift rather than as a wrong verdict.

**It is not run on every change.** That is the price ADR-0002 named; paying it only for
this layer keeps the default suite's property intact.
