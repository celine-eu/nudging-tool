# Suppression

The decisions **not** to send. This is the part of the service where correct and broken
look identical from outside: the sender does not wait, the recipient cannot know what did
not arrive, and nothing downstream is code.

Three independent mechanisms, in the order they apply: deduplication in the engine, then
the participant's kinds, then the daily cap.

---

### REQ-0033 — a duplicate is one rule, one person, one community, one period

The key is `<rule>:<user>:<community>:<scope>`. A missing community is an empty segment
rather than a dropped one, so `rule:user::2026-08-15` cannot collide with a community
literally named the empty string.

Everything in the key widens the suppression by its absence. The user is in it because
dedup is per person: the same rule firing for two members of a community sends two
notifications.

### REQ-0034 — the window names the period, and the sender's date decides its granularity

`definition.dedup_window` maps to a scope segment:

| Window | Scope |
|---|---|
| `always` | a fresh UUID — never suppressed |
| `once`, `one` | the literal `once` — suppressed for ever |
| `hourly` | `facts.hour`, or the current UTC hour |
| `daily`, `weekly`, `monthly`, `yearly` | the sender's `time`/`date`/`week`/`period` |
| anything else (e.g. `two_weeks`) | the same as above |
| nothing to fall back on | the current UTC month |

The window name does **not** set the granularity. A rule declared `monthly` that receives
`time: 2026-08-15` deduplicates per day, because the scope is the string it was given.
What normally keeps the two consistent is REQ-0024, which only selects the rule when the
two agree — and which also makes `always`, `once` and `two_weeks` unreachable through
ingest.

### REQ-0035 — the database is what refuses the duplicate

The `nudges_log.dedup_key` unique constraint. The engine inserts and catches the
violation; it does not select first, so two workers racing on the same event both compute
the same key and exactly one insert survives.

The duplicate is recorded as a `suppressed_dedup` audit row naming the key, and no
notification is written. That row is the only evidence anywhere in the platform that a
duplicate was stopped.

**The branch is entered by matching the literal `uq_nudges_dedup_key` in the driver's
error text.** PostgreSQL puts the constraint name there and SQLite does not, so a rename
in a migration would turn every duplicate into an unhandled `500` with nothing failing.
The suite pins the literal against the model and the migration, and simulates
PostgreSQL's wording for that one constraint — see
[ADR-0003](../decisions/ADR-0003-the-database-is-real-and-sqlite.md).

### REQ-0036 — a participant receives a kind only if it is in their enabled list

A rule declares `definition.kind`; the orchestrator suppresses the notification when that
kind is known and not in the participant's enabled list, recording `kind_disabled` on
every job and marking the notification `suppressed`.

Two ways a rule escapes this entirely, and both are the current state of the seed:

- a rule with **no kind**, or a blank one, is matched against nothing and always
  delivered;
- a rule whose kind is **not in `active_kinds.yaml`** is likewise always delivered. The
  catalogue lists three kinds; ten of the kinds the shipped rules declare are not among
  them, so most notifications cannot be refused by anyone.

A stored consent naming a kind the catalogue no longer lists is dropped rather than
carried forward.

### REQ-0037 — some kinds may not be refused, and a community preference wins

A catalogue entry with `editable: false` is added back to the enabled list whatever the
participant chose, and reported as enabled. Weather alerts (`extr_event`) are the one such
kind: a civil-protection alert is not a preference.

Where a participant has both a community-scoped preference row and a generic one, the
community-scoped row is used — `community_id IS NULL` sorts last. (The same pair of rows
breaks language resolution: REQ-0029.)

### REQ-0038 — a participant who has chosen nothing receives everything

Opting in is the default. No preference row, no `consents`, or a `consents` value of the
wrong shape all mean every active kind — so a corrupted consents blob silently re-enables
what a participant switched off.

The cap for a participant with no row is a literal `3` in the orchestrator, **not**
`MAX_PER_DAY_DEFAULT`; that setting only fills in the column for *seeded* rows. The two
values agree today. Filed as [#36](https://github.com/celine-eu/nudging-tool/issues/36).

### REQ-0039 — the daily cap counts web deliveries sent today to this destination

`sent_today < max_per_day`, where the count is `delivery_log` rows that are `sent`, whose
`sent_at` is today, and whose destination starts with `web:<user>` or
`web:<user>:<community>`.

Three narrowings, each a way for the cap to be wrong:

- a suppressed or failed attempt does not consume the allowance;
- a participant in two communities has two allowances, because the prefix differs — while
  the community-less prefix is a prefix of both;
- **an email delivery matches no prefix**, so it neither consumes the allowance nor is
  checked against it. A participant who opted into email is capped on push and unbounded
  on email, and email-only ingest is entirely uncapped. Filed as
  [#37](https://github.com/celine-eu/nudging-tool/issues/37).

The participant-facing bound is 1..10 (`PUT /preferences/me`). The database has no such
constraint, so a seed can write `0`, which silences that participant completely.

### REQ-0040 — over the cap, every job is logged and the notification is marked

One `suppressed` delivery-log row per job that would have been sent, with `rate_limited`
and no `sent_at`, and the notification's status becomes `suppressed` rather than staying
`pending` — otherwise a participant's list would show an undelivered message as though it
were still on its way.

### REQ-0041 — a notification reports the best outcome of its deliveries

One `sent` makes it `sent`, however many channels failed. All-suppressed is `suppressed`.
Anything else is `failed`.

So a participant whose email bounced but whose push arrived sees nothing wrong — which is
right — and an operator reading `notifications.status` cannot see the bounce either. The
per-channel truth is in `delivery_log`.
