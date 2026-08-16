# Participants

What a person can see and change: their notifications, their preferences, their browser
subscriptions.

Ownership here is enforced in SQL on the identifiers in the token (REQ-0010), not by the
policy bundle — the bundle's `filters` rule says the same thing and nothing reads it.

---

## Notifications

### REQ-0051 — a caller lists only their own, newest first, without the deleted ones

`GET /notifications`, ordered by `created_at` descending, excluding soft-deleted rows
always. Another participant's notifications are not in the list and cannot be reached.

Each row carries its rule metadata — `family`, `type`, `severity` — copied at creation
rather than joined, so a client filtering on severity is filtering on a snapshot taken
when the message was written.

### REQ-0052 — the list can be paged and narrowed to the unread

`limit` (1..200, default 50), `offset` (≥ 0), `unread_only`. The bounds are enforced at
the edge, so a caller cannot ask for the whole table in one request.

### REQ-0053 — marking as read is idempotent, and a deleted notification is `410`

`PUT /notifications/{id}` sets `read_at` once; a second call changes nothing. If the
notification was soft-deleted the answer is `410 Gone` — safe to be specific, because the
caller is the one who deleted it.

### REQ-0054 — deleting is a soft delete and is idempotent

`DELETE /notifications/{id}` sets `deleted_at` and answers `204`, whether or not it was
already deleted, keeping the first timestamp.

The row stays because it is the only link between a delivery-log entry and the message a
person saw.

### REQ-0055 — somebody else's notification is `404`, not `403`

Both for a notification owned by another participant and for one that does not exist. A
`403` would confirm that the id names a real notification, which is not something a
stranger should be able to establish by guessing.

### REQ-0056 — a click is authenticated by a signed token, not by a session

`POST /notifications/track-click` is outside the middleware (REQ-0001), because the
service worker handling a push click may have no session at all. The token from the push
payload is the whole of its access control: anything the verifier accepts can mark any
notification as clicked.

- `<payload>.<signature>`, both base64url without padding. The payload is **not
  encrypted** — it is a notification id, readable by whoever already received that
  notification.
- HMAC-SHA256 over the encoded payload, compared in constant time. Signing is
  deterministic, so a retry is the same token.
- A malformed token, a tampered payload, or a signature from another secret is `400`. A
  valid token for a notification that no longer exists is `404`.

The secret is `CLICK_TRACKING_SECRET`, falling back to `VAPID_PRIVATE_KEY`. So **rotating
the VAPID key invalidates every token in flight** for an operator who never set the first
one, and clicks on already-delivered notifications stop being recorded — silently,
because a rejected token is a `400` nobody reads. With neither configured, signing raises
*inside the delivery*, failing every web push at the moment of sending.

### REQ-0057 — the first click is the one that is kept

`clicked_at` and `click_action` are written once; a second click changes neither. An
empty or absent action is recorded as `default`.

Soft-deletion is not checked, so a participant who deletes a message and then clicks its
push still lands on the row — `clicked_at` can be later than `deleted_at`, which is the
truth about what happened.

## Preferences

### REQ-0058 — a participant with no row is given the defaults, and no row is written

`GET /preferences/me` answers `lang: DEFAULT_LANG`, `max_per_day: 3`, `channel_email:
false`, and every active kind enabled — the same thing the orchestrator assumes
(REQ-0038) — without storing anything. Most participants have no row, because one is
created only by a write.

The endpoint knows four languages: `en`, `it`, `es`, `ca`. A stored value outside that
set is reported as the default rather than echoed back, and an unsupported language sent
in an update is ignored rather than stored.

### REQ-0059 — a write stores the row under the token's subject

`PUT /preferences/me`. `max_per_day` is required on every request (1..10); everything else
is optional and absent means unchanged.

A row found under an alias — the participant's `preferred_username` — is **renamed** to
the `sub` rather than duplicated, unless a `sub` row already exists, in which case the
canonical one wins and the alias row is left behind. That leftover is one way a
participant ends up with the two rows that break language resolution (REQ-0029).

Enabling the email channel requires a syntactically valid address, checked at the edge
rather than at delivery — an email channel with no address is a preference that silently
does nothing.

### REQ-0060 — the catalogue is the list of what may be refused

`GET /preferences/catalog` returns one entry per active kind with `label`, `description`
and `cadence` already resolved to one language, plus `enabled` and `editable`. The client
never sees the i18n maps.

An update stores only kinds the catalogue lists, so an invented kind does not poison the
row, and appends every non-editable kind whether or not it was sent (REQ-0037).

What the catalogue does *not* list cannot be refused at all (REQ-0036).

## Subscriptions

### REQ-0061 — a subscription is registered against the caller and nobody else

`POST /webpush/subscribe`. There is no `user_id` field in the request body, so this is
structural rather than checked.

Re-subscribing the same endpoint refreshes the keys and re-enables it rather than adding
a row — a browser rotates its keys without changing its endpoint, and a second row would
mean pushing twice, once with keys that no longer decrypt. It is also the way back for a
subscription disabled after a `410` (REQ-0046).

The unique key is (user, endpoint, community), so a global subscription and a
community-scoped one can coexist for one browser.

Because a participant answers to two identifiers (REQ-0010), a subscribe writes **one row
per identifier** — deliberately, so that a nudge addressed by either id finds a
subscription. One browser therefore holds two rows, and disabling one leaves the other
live.

`GET /webpush/vapid-public-key` returns the configured public key to any authenticated
participant. It is public by definition, and it is served from configuration rather than
derived — so a mismatched key pair hands out a key that every subscription will later
fail to verify.

### REQ-0062 — unsubscribing disables, and says nothing about what exists

`POST /webpush/unsubscribe` sets `enabled = false` for the caller's own subscription with
that endpoint and community. The row stays, so delivery-log rows pointing at it keep
meaning something.

It answers `ok` whether or not anything matched — an endpoint that was never registered,
an already-disabled one, and another participant's endpoint are one answer, so a caller
cannot learn whether an endpoint exists.
