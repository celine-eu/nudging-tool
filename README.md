# CELINE Nudging Tool

Backend service that transforms Digital Twin events into personalized push notifications for renewable energy community participants.

## Event Flow

```
DigitalTwinEvent -> Engine -> Orchestrator -> Publisher (web push)
```

1. **Engine** — evaluates rules against the incoming event, selects matching nudge type and severity, renders a Jinja2 message template
2. **Orchestrator** — applies delivery policies (suppression, deduplication, frequency limits, user preferences)
3. **Publisher** — sends the notification via the registered delivery channel (currently: `web` via VAPID web push)

## Features

- Rule-based engine with YAML-seeded rules, templates, and preferences
- Delivery suppression and deduplication (daily / weekly / monthly / yearly scopes)
- Web push (VAPID) via pywebpush
- Configurable per-user notification preferences
- PostgreSQL persistence via SQLAlchemy async

## Quick Start

**Docker (recommended):**
```bash
docker compose up --build
```

Starts: `db` (PostgreSQL on port 5433), `initdb` (schema + seed), `api` (port 8000).

**Local Python:**
```bash
uv sync
python -m db.init_db        # create schema and seed data
hypercorn api.main:app --bind 0.0.0.0:8000
```

## Configuration

| Variable | Description | Default |
|---|---|---|
| `DATABASE_URL` | PostgreSQL async URL | required |
| `DEFAULT_LANG` | Default notification language | `en` |
| `MAX_PER_DAY_DEFAULT` | Default max notifications per day | `3` |
| `VAPID_PUBLIC_KEY` | VAPID public key (base64url) | required |
| `VAPID_PRIVATE_KEY` | VAPID private key (base64url) | required |
| `VAPID_SUBJECT` | VAPID contact (mailto: or https:) | required |

## Documentation

| Document | Description |
|---|---|
| [Architecture](https://celine-eu.github.io/projects/nudging-tool/docs/architecture) | Event flow, engine/orchestrator/publisher components, database models |
| [Engine](https://celine-eu.github.io/projects/nudging-tool/docs/engine) | Rule evaluation, NudgeType/Severity, Jinja2 templates, dedup scopes, YAML seed format |
| [API Reference](https://celine-eu.github.io/projects/nudging-tool/docs/api-reference) | All endpoints: ingest-event, webpush, notifications, preferences |
| [Development](https://celine-eu.github.io/projects/nudging-tool/docs/development) | Local setup, VAPID key generation, seed management, running tests |

## License

Apache 2.0 — Copyright © 2025 Spindox Labs
