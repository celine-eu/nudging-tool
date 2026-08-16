# Ingest

`POST /admin/ingest-event` is the door every other service comes through:
`../flexibility-api`, `../celine-grid`, `../digital-twin` and `../celine-webapp`, all via
`celine.sdk.nudging.client`.

**No sender reads the answer.** In `../flexibility-api` at least, scheduling a nudge is
explicitly best-effort: a failure is logged and must not fail the action that triggered
it. So the status codes below are documentation for a human reading a log, and the tests
are the only thing keeping them true.

---

### REQ-0011 — an event with no facts is refused

`422`, before anything else. `facts` is where everything this service needs lives — it
fetches no domain data of its own.

### REQ-0012 — facts must carry `facts_version` and `scenario`

Both, and both non-empty. Checked twice, by two implementations that do not agree:

- at the edge (`src/celine/nudging/engine/rules/contract.py`), where truthiness is enough, so `facts_version: 1`
  passes;
- in the engine, where they must be **strings**, so the same event is refused one layer in.

The edge check is what produces the `422`, and it is the only rejection in this service
that writes **no audit row at all** — an event refused there leaves no trace anywhere.

### REQ-0013 — an event must name a participant or an email recipient

With no `user_id`, the facts must carry at least one parseable address in
`email_recipients`. Otherwise `422` with `missing_target`.

The address filter (REQ-0043) runs first, so a list where *every* entry is a typo is
indistinguishable from an empty one and produces this error. A list where only some
entries are typos is the silent case.

### REQ-0014 — an email-only event gets a stable synthetic participant

`email-ingest:<sha256 of the sorted, lower-cased addresses>[:16]`. Used by
`../celine-grid` to alert a DSO operator who has no account here.

Sorting and lower-casing before hashing is what makes deduplication work for a person the
service has never met: `[A, B]` and `[b, a]` are one recipient set and therefore one
dedup key. Reordering the list would otherwise send the same alert twice.

The `email-ingest:` prefix is also what suppresses the web delivery (REQ-0042) — there is
no browser subscribed to a synthetic id.

### REQ-0015 — `facts` is the contract and `payload` is the older shape

`facts` is used when non-empty; `payload` is the fallback. An event setting both is
decided by `facts` alone, and its `payload` is silently ignored.

### REQ-0016 — the answer says what became of the event

| Code | Meaning |
|---|---|
| `200` | at least one nudge was created and at least one delivery job was built |
| `202` | nudges were created and **every** delivery was suppressed |
| `204` | rules ran and none triggered |
| `400` | no enabled rule claims that scenario at that frequency |
| `409` | every rule that would have fired was a duplicate |
| `422` | the facts are unusable: missing, contractless, no target, no time scope, or a required fact absent |
| `500` | anything else, including a raising evaluator or a raising template |

`200` and `202` are the distinction the service exists for: in both cases a participant
will see the message in their list, and in only one did a channel take it.

### REQ-0017 — every engine outcome is written to `nudges_log`, including the silent ones

One row per evaluation, whatever it decided: `created`, `not_triggered`, `missing_facts`,
`unknown_scenario`, `suppressed_dedup`. The row carries the scenario, the facts version,
the facts themselves and a `details` object naming the reason.

This is the only record in the platform that an event arrived and did nothing, and
nothing reads it — so the reason strings are for a person with a database session.

A refusal's row gets a synthetic `dedup_key` prefixed `attempt:` and carrying a fresh
UUID, because `dedup_key` is unique and two malformed events from the same sender on the
same day would otherwise collide.

A created nudge writes its `nudges_log` row and its `notifications` row in **one commit**.

### REQ-0018 — ingest requires the ingest permission

`require_ingest`, so a sender's service account or an administrator (REQ-0006). A
participant is `403`.

### REQ-0019 — one event may produce several notifications, and they are independent

Every enabled rule claiming the scenario at the inferred frequency is evaluated, and each
produces its own result. One rule failing its required facts, being disabled for the
community, or being a duplicate does not stop another from being delivered — the response
reports both halves.
