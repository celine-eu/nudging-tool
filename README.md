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

The service follows `celine.sdk.posture`: **only `CELINE_ENV=dev` accepts the development
defaults** below (`task run` exports it). Unset, or any other value, is hardened and
`create_app()` refuses to start while the database password is a local one, the client secret
equals the client id, the issuer is the SDK's local default, or `VAPID_PUBLIC_KEY`,
`VAPID_PRIVATE_KEY` or `CLICK_TRACKING_SECRET` is unset — see REQ-0081.

| Variable | Default | Description |
|---|---|---|
| `CELINE_ENV` | — (hardened) | `dev` relaxes the posture; `ENVIRONMENT` is read when it is empty |
| `DATABASE_URL` | `postgresql+asyncpg://...host.docker.internal:15432/nudging` | PostgreSQL async URL |
| `DEFAULT_LANG` | `en` | Default notification language |
| `MAX_PER_DAY_DEFAULT` | `3` | Default max notifications per day |
| `SCHEDULER_POLL_SECONDS` | `30.0` | Scheduler polling interval |
| `SEED_DIR` | `./seed` | Directory containing rule definitions |
| `ORCHESTRATOR_URL` | `http://api.celine.localhost/nudging` | Public base URL |
| `VAPID_PUBLIC_KEY` | — | VAPID public key (base64url) |
| `VAPID_PRIVATE_KEY` | — | VAPID private key (base64url) |
| `VAPID_SUBJECT` | `mailto:dev@example.com` | VAPID contact URI |
| `CLICK_TRACKING_SECRET` | — | Signs click-tracking tokens; required outside dev (in dev it falls back to the VAPID private key) |
| `SMTP_HOST` | — | SMTP server hostname |
| `SMTP_PORT` | `587` | SMTP server port |
| `SMTP_USERNAME` | — | SMTP authentication username |
| `SMTP_PASSWORD` | — | SMTP authentication password |
| `SMTP_USE_TLS` | `true` | Use STARTTLS |
| `EMAIL_FROM` | — | Sender email address |
| `CELINE_OIDC_*` | (from celine-sdk) | OIDC settings; client id, secret and audience default to `svc-nudging` (dev only) |

## CLI

```bash
nudging-cli seed apply ./seed   # seed rules and templates
nudging-cli vapid               # generate VAPID keys
```

## Documentation

| Document | Description |
|---|---|
| [Requirements](docs/specifications/index.md) | What the service must do — each traced to a test |
| [Decisions](docs/decisions/index.md) | Why the testing and requirement choices were made |
| [Architecture](docs/architecture.md) | Event flow, engine/orchestrator/publisher, database models |
| [Engine](docs/engine.md) | Rule evaluation, evaluators, templates, dedup, seed format |
| [API Reference](docs/api-reference.md) | All endpoints: admin, notifications, preferences, webpush |
| [Development](docs/development.md) | Local setup, VAPID keys, seed management, testing |

## License

Apache 2.0 — Copyright © 2025 Spindox Labs
