# Development

## Prerequisites

- Python ≥ 3.11 (or `uv`)
- PostgreSQL 16
- Docker and Docker Compose (optional)

## Local Setup

```bash
# Install dependencies
uv sync
# OR: pip install -r requirements.txt

# Set environment variables
export DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/nudging
export VAPID_PUBLIC_KEY=<base64url-public-key>
export VAPID_PRIVATE_KEY=<base64url-private-key>
export VAPID_SUBJECT=mailto:admin@example.com

# Initialize database (drop/create + seed)
python -m db.init_db

# Start the API
hypercorn api.main:app --bind 0.0.0.0:8000
```

## Docker Setup

```bash
docker compose up --build
```

Services started:
- `db` — PostgreSQL on `localhost:5433`
- `initdb` — schema creation + seed
- `api` — FastAPI on `http://localhost:8000`

## Generating VAPID Keys

```bash
pip install pywebpush
python -c "
from py_vapid import Vapid
v = Vapid()
v.generate_keys()
print('Public:', v.public_key)
print('Private:', v.private_key)
"
```

## Seed Data Management

Seed files are loaded on `python -m db.init_db`. This destroys and recreates the database schema before inserting seed data.

Files:
- `seed/rules.yaml` — nudge rules
- `seed/templates.yaml` — Jinja2 message templates per language
- `seed/preferences.yaml` — default user preferences

For development, edit YAML files and re-run `python -m db.init_db`. Do not use this in production — use Alembic migrations instead.

## Running Tests

```bash
pytest -q
```

Manual HTTP tests using VSCode REST Client or IntelliJ HTTP client are in `tests/nudging_rules_tests.http`.

## Known Issues

- `tests/test_ingest_mock.py` fails with `no_rule_for_inferred_frequency` in default configuration — a mismatch between the test scenario and the seeded rule frequency.
- No `/health` endpoint is currently implemented.
- Only the `web` publisher channel is registered in the publisher registry.

## Project Layout

```
src/ (or top-level module path)
  api/
    main.py
    routes/
      ingest.py
      webpush.py
  config/settings.py
  db/
    models.py
    session.py
    init_db.py
    seed_db.py
    seed/
      rules.yaml
      templates.yaml
      preferences.yaml
  engine/
    engine_service.py
    rules/
      contract.py
      models.py
    templates/renderer.py
  orchestrator/
    orchestrator.py
    policies.py
    preferences.py
    models.py
  publishers/
    base.py
    registry.py
    web/worker.py
```
