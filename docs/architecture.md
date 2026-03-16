# Architecture

## Event Flow

The nudging pipeline processes events in three sequential stages:

| Stage | Component | Responsibility |
|---|---|---|
| 1. Ingestion | `POST /ingest-event` | Validates the incoming `DigitalTwinEvent` payload |
| 2. Rule Evaluation | `engine/engine_service.py` | Matches event against rules, selects nudge type, renders message |
| 3. Orchestration | `orchestrator/orchestrator.py` | Applies suppression, dedup, preferences |
| 4. Delivery | `publishers/web/worker.py` | Sends web push notification via pywebpush |

## Components

### Engine

The engine receives a `DigitalTwinEvent` and:
1. Looks up matching rules from the database (seeded from `seed/rules.yaml`)
2. Evaluates rule conditions against the event's `facts` payload
3. Selects a `NudgeType` and `Severity`
4. Renders a Jinja2 message template for the target language

### Orchestrator

The orchestrator decides whether and how to deliver a nudge:
- Checks per-user notification preferences (`max_per_day`, language)
- Applies deduplication: prevents duplicate nudges within a scope window (daily/weekly/monthly/yearly)
- Applies suppression: respects global delivery limits
- Emits the delivery record for the publisher

### Publisher

The publisher registry holds registered delivery channels. Currently only `web` is registered, which:
- Looks up the user's web push subscription endpoint
- Constructs a VAPID-signed push message
- Calls the push service via pywebpush

## Database Models

| Model | Description |
|---|---|
| `NudgeRule` | Rule definition: event type, conditions, nudge type, severity |
| `NudgeTemplate` | Jinja2 message template per language and nudge type |
| `UserPreference` | Per-user preferences: language, max per day, enabled flag |
| `WebPushSubscription` | Browser push endpoint, keys per user/community |
| `NudgeDelivery` | Delivery record: nudge, user, timestamp, status, dedup key |

## Stack

- Python 3.11
- FastAPI + Hypercorn
- SQLAlchemy 2 (async) + asyncpg
- PostgreSQL 16
- Jinja2 for template rendering
- pywebpush for VAPID web push
