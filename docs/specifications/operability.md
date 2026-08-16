# Operability

Starting, degrading and failing.

---

### REQ-0050 — startup loads the policy bundle, seeds, and starts the scheduler, in that order

The lifespan does three things and two of them can stop the process:

1. `init_policy_engine()` — raises without a bundle (REQ-0003), so the service does not
   come up unauthorised.
2. `auto_seed()` — reads `SEED_DIR` through the same loader as everything else, so a
   missing or malformed `active_kinds.yaml` raises here (REQ-0069). A service that
   started without a catalogue would suppress every notification as an unknown kind.
3. `run_scheduler()` — a background task. Shutdown sets its stop event and **awaits the
   task**, so a poll in flight finishes rather than being cancelled mid-dispatch.

`auto_seed` is skipped, with a log line, when `SEED_DIR` is unset or names a directory
that does not exist. That is the one way to start without a catalogue, and it is
deliberate.

`GET /health` reports only that the process is up. It checks nothing — not the database,
not the seed, not the scheduler — so it is a liveness probe and must not be read as a
readiness one: a service whose PostgreSQL has gone answers `{"status": "ok"}` and fails
every request behind it.
