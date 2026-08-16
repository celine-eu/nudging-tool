# Identity and authorisation

Who the caller is, and what the policy bundle lets them do. Two mechanisms, and they are
independent: a middleware that decides whether a request is authenticated at all, and a
Rego bundle evaluated in process that decides whether it may proceed.

---

### REQ-0001 — every route needs a token except a fixed, exact list

`AuthMiddleware` runs before every dependency. The open list is a literal set —
`/health`, `/docs`, `/redoc`, `/openapi.json`, `/favicon.ico`,
`/notifications/track-click` — plus anything under the `/static/` prefix.

It is matched exactly, not by route pattern: `/health/` with a trailing slash is closed,
and `/static` without one is closed.

`/notifications/track-click` is the one place a caller reaches a notification without
proving who they are. What stands in for authentication there is the signed token in the
body (REQ-0056).

### REQ-0002 — a missing token and an unusable one are the same answer

Both are `401` with `WWW-Authenticate: Bearer`. The detail distinguishes a missing header
(`Missing Authorization header`) from a token that would not verify (`Invalid or expired
token`), and nothing distinguishes *why* the second happened — expired, wrong realm,
wrong audience and nonsense are one answer.

Verification is `celine.sdk.auth.JwtUser.from_token` against the configured JWKS, with
audience `svc-nudging`.

### REQ-0003 — the service does not start without its policy bundle

`init_policy_engine()` raises when `POLICIES_DIR` is unset or names a directory that does
not exist, and the lifespan does not catch it. **This is the opposite of `../celine-grid`
and `../flexibility-api`**, which log and continue with a permissive fallback.

The consequence is worth stating positively: here, an *allow* is evidence that the policy
ran. The suite still refuses to start without a loaded bundle, and asserts that the
package the dependencies query by name — `celine.nudging.authz` — is the one that loaded.
A bundle that loads without it would deny everything, which is safe and completely opaque.

### REQ-0004 — an evaluation that does not return an explicit `true` denies

`_extract_bool` defaults to `False`. An empty result, a missing expression, a
non-boolean value, a query naming a rule the bundle does not define: every one of them is
a denial.

This is what makes a renamed Rego rule a lockout rather than a hole.

### REQ-0005 — an administrator is one by scope or by group

`is_admin` is granted by the `nudging.admin` scope **or** membership of the `admin` group.
Groups are read with `extract_groups`, which flattens organisation-level groups too, so a
member of an organisation whose org group is `admin` is an administrator here.

Scopes arrive as a space-separated string or as a list, and both reach the policy as a
list — a raw string would match nothing under Rego's `in` and would silently demote every
administrator.

An administrator sees every participant's notifications, may seed, and may push test
messages to anyone.

### REQ-0006 — ingest is its own permission, and an administrator has it

`is_ingest` is granted by the `nudging.ingest` scope **or** by `is_admin`. The reverse
does not hold: a sender's token may put events in and may not read notifications, seed, or
send test pushes.

That asymmetry is the whole of the separation between the four sending services and an
operator.

### REQ-0007 — a participant has neither permission

A logged-in participant carries no scope and no group, so every `/admin` route answers
`403` with a message naming the missing permission. Being a service account grants
nothing by itself either.

### REQ-0008 — `allow` says only that somebody is there, and no route uses it

The bundle's `allow` is true for any subject with an id and a non-anonymous type — every
participant satisfies it. No dependency queries it: `require_admin` and `require_ingest`
query `is_admin` and `is_ingest` directly.

It is pinned so that a future route guarded by `allow` is recognised as open to every
logged-in user in the realm rather than as protected.

Every decision is made about one resource, `userdata:nudging`, with the action as the only
variable. There is no per-notification or per-community authorisation.

### REQ-0009 — a subject is typed a service by its username

`is_service_account()` trusts `preferred_username` starting with `service-account-`. The
type is what the bundle's `filters` rule branches on, so a service whose username does not
carry that prefix is typed as a user.

### REQ-0010 — a participant answers to their subject and to their username

Notifications, preferences and subscriptions are owned by *either* the token's `sub` or
its `preferred_username`, because the four sending services do not agree on which they
put in an event. Both are accepted as the caller's own.

Two consequences, both real:

- `POST /webpush/subscribe` writes **one row per identifier** (REQ-0061).
- `PUT /preferences/me` writes under the `sub` and migrates an alias row to it
  (REQ-0059).

The bundle also publishes a `filters` rule — an empty list for a service, a
`user_id = <subject>` predicate for a user — and **nothing in this service reads it**. It
is pinned so that whoever wires it up finds out that a service account is given no filter
and would therefore see every row.
