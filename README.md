# CELINE Nudging Tool

Notification service for the CELINE platform. Receives events from other services, evaluates rules with custom Python evaluators, renders templated messages, and delivers notifications via web push or email. Handles deduplication, frequency limiting, user preferences, and scheduled delivery.

## Event Flow

```
Event -> Engine -> Orchestrator -> Publisher (web push / email)
                                      |
Scheduler -> ScheduledEvent ----------+
```

1. **Engine** — evaluates rules using per-rule Python evaluators, selects matching rules, renders Jinja2 message templates per channel and language
2. **Orchestrator** — applies delivery policies (suppression, deduplication, frequency limits, user preferences, per-community overrides)
3. **Publisher** — sends via web push (VAPID) or email (SMTP)
4. **Scheduler** — processes due scheduled events on a polling loop

## Features

- Rule-based engine with per-rule Python evaluators and Jinja2 templates
- Two delivery channels: web push (VAPID) and email (SMTP)
- Delivery suppression and deduplication (daily / weekly / monthly / yearly scopes)
- Per-user notification preferences with kind-level opt-in/opt-out
- Per-community rule overrides
- Scheduled event delivery
- Notification catalog with i18n support (`active_kinds.yaml`)
- Seed-based rule/template management via CLI
- OPA-enforced access control
- PostgreSQL persistence via SQLAlchemy async

## Quick Start

```bash
uv sync
task alembic:migrate
task seed                    # seed rules from ./seed directory
task run                     # runs on port 8016
```

## Configuration

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://...host.docker.internal:15432/nudging` | PostgreSQL async URL |
| `DEFAULT_LANG` | `en` | Default notification language |
| `MAX_PER_DAY_DEFAULT` | `3` | Default max notifications per day |
| `SCHEDULER_POLL_SECONDS` | `30.0` | Scheduler polling interval |
| `SEED_DIR` | `./seed` | Directory containing rule definitions |
| `ORCHESTRATOR_URL` | `http://api.celine.localhost/nudging` | Public base URL |
| `VAPID_PUBLIC_KEY` | — | VAPID public key (base64url) |
| `VAPID_PRIVATE_KEY` | — | VAPID private key (base64url) |
| `VAPID_SUBJECT` | `mailto:dev@example.com` | VAPID contact URI |
| `SMTP_HOST` | — | SMTP server hostname |
| `SMTP_PORT` | `587` | SMTP server port |
| `SMTP_USERNAME` | — | SMTP authentication username |
| `SMTP_PASSWORD` | — | SMTP authentication password |
| `SMTP_USE_TLS` | `true` | Use STARTTLS |
| `EMAIL_FROM` | — | Sender email address |
| `OIDC__*` | (from celine-sdk) | OIDC settings (audience: `svc-nudging`) |

## CLI

```bash
nudging-cli seed apply ./seed   # seed rules and templates
nudging-cli vapid               # generate VAPID keys
```

## Documentation

| Document | Description |
|---|---|
| [Architecture](docs/architecture.md) | Event flow, engine/orchestrator/publisher, database models |
| [Engine](docs/engine.md) | Rule evaluation, evaluators, templates, dedup, seed format |
| [API Reference](docs/api-reference.md) | All endpoints: admin, notifications, preferences, webpush |
| [Development](docs/development.md) | Local setup, VAPID keys, seed management, testing |

## License

Apache 2.0 — Copyright © 2025 Spindox Labs
