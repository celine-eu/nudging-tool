# Nudging Tool

Servizio backend che trasforma eventi del Digital Twin in notifiche personalizzate per gli utenti della comunità energetica.

Flusso logico:

```text
DigitalTwinEvent -> Engine -> Orchestrator -> Publisher (web/webpush)
```

## Stato del progetto

- API FastAPI attiva con endpoint di ingest e webpush.
- Persistenza su PostgreSQL via SQLAlchemy async.
- Seed iniziale di regole/template/preferenze da YAML.
- Publisher registrato: `web` (implementato con invio webpush).

## Stack

- Python 3.11
- FastAPI + Hypercorn
- SQLAlchemy 2 (async) + asyncpg
- PostgreSQL 16
- Jinja2
- pywebpush

## Struttura repository

```text
nudging-tool/
├── api/
│   ├── main.py
│   └── routes/
│       ├── ingest.py
│       └── webpush.py
├── config/
│   └── settings.py
├── db/
│   ├── init_db.py
│   ├── models.py
│   ├── seed_db.py
│   ├── session.py
│   └── seed/
│       ├── preferences.yaml
│       ├── rules.yaml
│       └── templates.yaml
├── engine/
│   ├── engine_service.py
│   ├── rules/
│   │   ├── contract.py
│   │   └── models.py
│   └── templates/
│       └── renderer.py
├── orchestrator/
│   ├── models.py
│   ├── orchestrator.py
│   ├── policies.py
│   └── preferences.py
├── publishers/
│   ├── base.py
│   ├── registry.py
│   └── web/
│       └── worker.py
├── tests/
├── docker-compose.yaml
├── Dockerfile
└── requirements.txt
```

## Prerequisiti

- Docker + Docker Compose

Oppure, per esecuzione locale:

- Python 3.11
- PostgreSQL raggiungibile dalla `DATABASE_URL`

## Configurazione (`.env`)

Variabili principali:

```env
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/nudging
DEFAULT_LANG=en
MAX_PER_DAY_DEFAULT=3

VAPID_PUBLIC_KEY=<public-key>
VAPID_PRIVATE_KEY=<private-key>
VAPID_SUBJECT=mailto:you@example.com
```

Nota: con `docker compose` il DB usato dall'API è `db:5432` (configurato nel compose), non `localhost:5432`.

## Avvio rapido con Docker

```bash
docker compose up --build
```

Servizi avviati:

- `db` (PostgreSQL) su `localhost:5433`
- `initdb` (inizializzazione schema + seed)
- `api` su `http://localhost:8000`

Documentazione OpenAPI:

- `http://localhost:8000/docs`

## Avvio locale (senza Docker)

1. Crea e attiva venv

```bash
python3 -m venv .venv
source .venv/bin/activate
```

2. Installa dipendenze

```bash
pip install -r requirements.txt
```

3. Inizializza DB (drop/create + seed)

```bash
python -m db.init_db
```

4. Avvia API

```bash
hypercorn api.main:app --bind 0.0.0.0:8000
```

## Endpoint principali

### `POST /ingest-event`

Ingest di un evento arricchito dal Digital Twin.

Payload minimo:

```json
{
  "event_type": "imported_up",
  "user_id": "user-it",
  "community_id": "COMM1",
  "facts": {
    "facts_version": "1.0",
    "scenario": "imported_up",
    "time": "2026-01-10",
    "delta_pct": 25.0,
    "cur": 123.0,
    "prev": 90.0
  }
}
```

Esempio `curl`:

```bash
curl -X POST http://localhost:8000/ingest-event \
  -H "Content-Type: application/json" \
  -d '{
    "event_type": "imported_up",
    "user_id": "user-it",
    "community_id": "COMM1",
    "facts": {
      "facts_version": "1.0",
      "scenario": "imported_up",
      "time": "2026-01-10",
      "delta_pct": 25.0,
      "cur": 123.0,
      "prev": 90.0
    }
  }'
```

Possibili esiti:

- `200`: nudge creato e almeno una delivery non soppressa
- `202`: nudge creato ma delivery soppressa dall'orchestrator
- `204`: nessuna regola triggerata
- `400`: scenario non mappato / regola non risolvibile
- `409`: soppressione per dedup
- `422`: facts mancanti o contratto facts invalido

### Webpush

- `GET /webpush/vapid-public-key`
- `POST /webpush/subscribe`
- `POST /webpush/unsubscribe`
- `POST /webpush/send-test`

Esempio subscribe:

```bash
curl -X POST http://localhost:8000/webpush/subscribe \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "user-it",
    "community_id": "COMM1",
    "subscription": {
      "endpoint": "https://example.pushservice/abc",
      "keys": {
        "p256dh": "p256dh-key",
        "auth": "auth-key"
      }
    }
  }'
```

## Seed dati

Il bootstrap DB legge:

- `db/seed/rules.yaml`
- `db/seed/templates.yaml`
- `db/seed/preferences.yaml`

Comando usato anche da Docker init service:

```bash
python -m db.init_db
```

Attenzione: `db.init_db` esegue `drop_all` + `create_all` prima del seed.

## Test

```bash
pytest -q
```

Sono inclusi anche file HTTP manuali in `tests/nudging_rules_tests.http`.

## Limiti noti (stato al 17 febbraio 2026)

- Non esiste un endpoint `/health`.
- Nel registry publisher è registrato solo il canale `web`.
- Il test `tests/test_ingest_mock.py` attualmente fallisce in configurazione default per mismatch tra scenario/rule frequency (`no_rule_for_inferred_frequency`).

## Licenza

`LICENSE` (Apache 2.0)
