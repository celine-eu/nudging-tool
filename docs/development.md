# Development

## Prerequisites

- Python >= 3.12 and `uv`
- `task` (go-task)
- PostgreSQL at `localhost:15432` (credentials `postgres:securepassword123`)

## Local Setup

```bash
uv sync
task alembic:migrate       # apply database migrations
task seed                  # seed rules and templates
task run                   # start on port 8016
```

## Taskfile Commands

| Command | Description |
|---|---|
| `task run` | Start dev server on port 8016 |
| `task debug` | Start with debugger (port 48016) |
| `task test` | Run pytest |
| `task seed` | Seed rules from `./seed` directory |
| `task alembic:migrate` | Apply all pending migrations |
| `task alembic:sync-model` | Generate new Alembic migration |
| `task alembic:reset` | Reset DB to base |
| `task release` | Run semantic-release |

## VAPID Key Generation

```bash
nudging-cli vapid
```

Set the generated keys as `VAPID_PUBLIC_KEY` and `VAPID_PRIVATE_KEY` environment variables.

## Seed Data Management

Rules are stored in `seed/rules/<rule_id>/` directories, each containing:
- `rule.yaml` — rule definition
- `evaluate.py` — custom Python evaluator
- `templates/` — Jinja2 templates per channel and language

Apply seeds via:
```bash
task seed
# or directly:
nudging-cli seed apply ./seed --client-id celine-cli --client-secret celine-cli
```

The CLI calls `POST /admin/seed/apply` to upsert rules and templates.

## Database Migrations

```bash
task alembic:migrate                          # apply pending
task alembic:sync-model -- "description"      # create new migration
task alembic:reset                            # reset to base
```

## Running Tests

```bash
task test
# or: uv run pytest -q
```

## Project Layout

```
src/celine/nudging/
  main.py                         # FastAPI app factory (create_app)
  settings.py                     # Pydantic settings
  scheduler.py                    # Background scheduled event processor
  api/routes/
    meta.py                       # /health
    webpush.py                    # /webpush/* (subscribe, unsubscribe, vapid)
    notifications.py              # /notifications (user-facing)
    preferences.py                # /preferences/me, /preferences/catalog
    admin/
      ingest.py                   # /admin/ingest-event
      webpush.py                  # /admin/webpush/send-test
      notifications.py            # /admin/notifications
      scheduled_events.py         # /admin/scheduled-events
      seed.py                     # /admin/seed/apply
  engine/
    engine_service.py             # Rule evaluation and template rendering
  orchestrator/
    orchestrator.py               # Delivery policy enforcement
    preferences.py                # User preference resolution
  publishers/
    web/worker.py                 # Web push delivery
    email/worker.py               # Email delivery
  db/
    models.py                     # SQLAlchemy ORM models
    session.py                    # Async session management
    auto_seed.py                  # Auto-seed on startup
  cli/
    main.py                       # CLI entry point (nudging-cli)
policies/                         # OPA .rego policy files
seed/                             # Rule definitions and templates
  rules/                          # Per-rule directories
  active_kinds.yaml               # Notification kind catalog
alembic/                          # Database migrations
```
