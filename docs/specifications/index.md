# Requirements

What this service must do, stated so that a test can name it.

These were **distilled from the code, not written before it** — see
[ADR-0001](../decisions/ADR-0001-requirements-are-read-out-of-the-code.md). Every one is
something `nudging-tool` does today and something a reader would want to stay true; none
is an aspiration.

## Five of them describe a defect

They are written as behaviour anyway, because a requirement that described the *intended*
behaviour would be an unverified wish, and the trace matrix would report it as covered.
Each names its issue, and fixing one means changing its requirement and its test in the
same change.

| | | |
|---|---|---|
| REQ-0029 | a participant with both a generic and a community preference row makes ingest a `500` | [#33](https://github.com/celine-eu/nudging-tool/issues/33) |
| REQ-0024 | four seeded rules can never fire — their `dedup_window` is not a frequency | [#34](https://github.com/celine-eu/nudging-tool/issues/34) |
| REQ-0032 | a template variable the facts do not carry renders as an empty string, and is delivered | [#35](https://github.com/celine-eu/nudging-tool/issues/35) |
| REQ-0038 | `MAX_PER_DAY_DEFAULT` does not apply to a participant with no preference row | [#36](https://github.com/celine-eu/nudging-tool/issues/36) |
| REQ-0039 | the daily cap counts only web deliveries, so email is unbounded | [#37](https://github.com/celine-eu/nudging-tool/issues/37) |

## How a requirement is verified

A test declares what it covers with a `@verifies REQ-####` tag in its docstring:

```python
async def test_another_participant_s_notification_is_not_found(other_user_client, db):
    """@verifies REQ-0055"""
```

The mapping is a projection of the two and is never written by hand. the harness profile
names no traceability provider, so until the harness checker is available in this
checkout the projection is a grep — `--include='*.py'` because `__pycache__` matches
otherwise:

```bash
grep -rho --include='*.py' "@verifies REQ-[0-9]\{4\}" tests/ | sort | uniq -c
```

It has to be read **both ways**: a requirement no test declares is unverified, and a tag
naming a requirement that does not exist is a typo — and a typo in a trace tag is
indistinguishable from coverage until someone reads the matrix.

Adding a requirement means adding a `REQ-####` here **and** a test declaring it, in the
same change.

## The requirements

| | |
|---|---|
| REQ-0001 – REQ-0010 | [identity and authorisation](identity-and-authorisation.md) — who the caller is and what they may do |
| REQ-0011 – REQ-0019 | [ingest](ingest.md) — what a sender must send, and what it is told |
| REQ-0020 – REQ-0032 | [rule evaluation](rule-evaluation.md) — which rule fires, and what it says |
| REQ-0033 – REQ-0041 | [suppression](suppression.md) — the decisions not to send |
| REQ-0042 – REQ-0049 | [delivery](delivery.md) — web push, email, and what is recorded |
| REQ-0050 | [operability](operability.md) — starting, degrading and failing |
| REQ-0051 – REQ-0062 | [participants](participants.md) — notifications, preferences, subscriptions |
| REQ-0063 – REQ-0067 | [scheduling](scheduling.md) — events that fire later |
| REQ-0068 – REQ-0075 | [seeding](seeding.md) — rules, templates and the catalogue |

## What is not covered

Uncovered by the suite, and therefore unverified whatever this document says:

- **The CLI** — `src/celine/nudging/cli/seed.py` and `src/celine/nudging/cli/vapid.py`. `seed apply` shares its upserts with
  the HTTP route (REQ-0072); its argument handling and the VAPID key generation are not
  exercised.
- **The migrations.** The suite builds its schema from `Base.metadata`, so a model that
  has drifted from `alembic/versions/` would not be caught. See
  [ADR-0003](../decisions/ADR-0003-the-database-is-real-and-sqlite.md).
- **PostgreSQL itself** — the same ADR. One consequence is load-bearing and is stated in
  REQ-0035.

## What is not here

- **Why** a choice was made — [`docs/decisions/`](../decisions/index.md).
- What the system *is* — [`docs/architecture.md`](../architecture.md).
- A trap that is true of the code and not obvious from it — the companion's knowledge.
- Anything broken — the issue tracker.
