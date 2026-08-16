# Architecture

What the system *is*. What it must **do** is [`docs/specifications/`](specifications/index.md),
where each statement carries an identifier and a test that names it.

## Event Flow

The nudging pipeline processes events through sequential stages:

| Stage | Component | Responsibility |
|---|---|---|
| 1. Ingestion | `POST /admin/ingest-event` | Validates the incoming event payload |
| 2. Rule Evaluation | `src/celine/nudging/engine/engine_service.py` | Matches event against rules, runs per-rule Python evaluators, renders messages |
| 3. Orchestration | `src/celine/nudging/orchestrator/orchestrator.py` | Applies suppression, dedup, frequency limits, user preferences |
| 4. Delivery | `src/celine/nudging/publishers/web/worker.py`, `src/celine/nudging/publishers/email/worker.py` | Sends web push or email |

Scheduled events follow the same pipeline but are triggered by the background scheduler instead of direct ingestion.

## Components

### Engine

The engine receives an event and:

1. Resolves matching rules by `rule_id` or event scenario
2. Loads each rule's custom Python evaluator from the seed directory
3. Evaluates whether the rule should fire given the event payload
4. Applies per-community rule overrides if configured
5. Renders one Jinja2 title and body for the resolved language — **not per channel**: web
   push and email send the same rendered strings

### Orchestrator

The orchestrator decides whether and how to deliver a notification:
- Checks per-user notification preferences (enabled, channels, per-kind opt-in/opt-out)
- Applies deduplication using `rule_id:user_id:community_id:scope` keys
- Enforces frequency limits (`max_per_day`)
- Emits delivery jobs for each applicable channel

### Publishers

- **Web push** (`src/celine/nudging/publishers/web/worker.py`) — sends VAPID-authenticated push via pywebpush
- **Email** (`src/celine/nudging/publishers/email/worker.py`) — sends via SMTP with TLS/SSL support

### Scheduler

`scheduler.py` runs as a background task, polling `scheduled_events` every `SCHEDULER_POLL_SECONDS`. Due events are processed through the engine pipeline in batches.

## Database Models

PostgreSQL (async via SQLAlchemy + asyncpg):

| Table | Purpose |
|---|---|
| `rules` | Rule definitions: id, kind, nudge_type, severity, definition (JSONB) |
| `rule_overrides` | Per-community overrides for rules |
| `templates` | Jinja2 title and body per rule and language, unique on (rule, lang) |
| `user_preferences` | Per-user preferences: enabled, channels, language, per-kind settings, max_per_day |
| `nudges_log` | Event processing log |
| `notifications` | Delivered notifications with read/deleted status |
| `delivery_log` | Per-channel delivery attempt records |
| `web_push_subscriptions` | Browser push subscription endpoints per user/community |
| `scheduled_events` | Future events to be processed at `trigger_at`, idempotent on `external_key` |

## Authorization

Rego, evaluated **in process** through `celine.sdk.policies` (`regorus`) —
`policies/celine/nudging/authz.rego`. No OPA server is involved.

- `is_ingest` — the `nudging.ingest` scope, or any administrator
- `is_admin` — the `nudging.admin` scope, **or** membership of the `admin` group
  (realm-level or organisation-level)
- User endpoints — ownership in SQL, matching the token's `sub` *or* its
  `preferred_username`. The bundle also publishes a `filters` rule, and nothing reads it.

The bundle **fails closed**: the service will not start without it, and an evaluation that
does not return an explicit `true` denies. See
[REQ-0003 – REQ-0010](specifications/identity-and-authorisation.md).

## Stack

- Python >= 3.12, FastAPI, uvicorn
- SQLAlchemy 2 (async) + asyncpg
- Alembic for migrations
- pywebpush for VAPID web push
- Jinja2 for template rendering
- Pydantic settings
