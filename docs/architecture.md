# Architecture

## Event Flow

The nudging pipeline processes events through sequential stages:

| Stage | Component | Responsibility |
|---|---|---|
| 1. Ingestion | `POST /admin/ingest-event` | Validates the incoming event payload |
| 2. Rule Evaluation | `engine/engine_service.py` | Matches event against rules, runs per-rule Python evaluators, renders messages |
| 3. Orchestration | `orchestrator/orchestrator.py` | Applies suppression, dedup, frequency limits, user preferences |
| 4. Delivery | `publishers/web/worker.py`, `publishers/email/worker.py` | Sends web push or email |

Scheduled events follow the same pipeline but are triggered by the background scheduler instead of direct ingestion.

## Components

### Engine

The engine receives an event and:
1. Resolves matching rules by `rule_id` or event scenario
2. Loads each rule's custom Python evaluator from the seed directory
3. Evaluates whether the rule should fire given the event payload
4. Applies per-community rule overrides if configured
5. Renders Jinja2 message templates per channel (web, email) and language

### Orchestrator

The orchestrator decides whether and how to deliver a notification:
- Checks per-user notification preferences (enabled, channels, per-kind opt-in/opt-out)
- Applies deduplication using `rule_id:user_id:community_id:scope` keys
- Enforces frequency limits (`max_per_day`)
- Emits delivery jobs for each applicable channel

### Publishers

- **Web push** (`publishers/web/worker.py`) — sends VAPID-authenticated push via pywebpush
- **Email** (`publishers/email/worker.py`) — sends via SMTP with TLS/SSL support

### Scheduler

`scheduler.py` runs as a background task, polling `scheduled_events` every `SCHEDULER_POLL_SECONDS`. Due events are processed through the engine pipeline in batches.

## Database Models

PostgreSQL (async via SQLAlchemy + asyncpg):

| Table | Purpose |
|---|---|
| `rules` | Rule definitions: id, kind, nudge_type, severity, definition (JSONB) |
| `rule_overrides` | Per-community overrides for rules |
| `templates` | Jinja2 message templates per rule, channel, language |
| `user_preferences` | Per-user preferences: enabled, channels, language, per-kind settings, max_per_day |
| `nudges_log` | Event processing log |
| `notifications` | Delivered notifications with read/deleted status |
| `delivery_log` | Per-channel delivery attempt records |
| `web_push_subscriptions` | Browser push subscription endpoints per user/community |
| `scheduled_events` | Future events to be processed at `fire_at` time |

## Authorization

OPA-enforced via `policies/celine/nudging/authz.rego`:
- `ingest` action — service accounts with `nudging.ingest` or `nudging.admin` scope
- `admin` action — service accounts with `nudging.admin` scope
- User endpoints — JWT-based ownership (notifications/preferences scoped to authenticated user)

## Stack

- Python >= 3.12, FastAPI, uvicorn
- SQLAlchemy 2 (async) + asyncpg
- Alembic for migrations
- pywebpush for VAPID web push
- Jinja2 for template rendering
- Pydantic settings
