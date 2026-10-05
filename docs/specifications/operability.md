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

### REQ-0081 — outside `CELINE_ENV=dev`, a development default refuses to start

`create_app()` runs the `celine.sdk.posture` guard before the app — and therefore the
lifespan, the database and the scheduler — exists. Only `CELINE_ENV=dev` relaxes it
(`ENVIRONMENT` is read when `CELINE_ENV` is empty); unset, `staging`, `prod` or a typo is
hardened. Hardened, startup raises `InsecureConfiguration` listing every one of:

- `DATABASE_URL` carrying a local-stack password;
- `CELINE_OIDC_CLIENT_SECRET` empty or equal to the client id (the `svc-nudging` default);
- `CELINE_OIDC_BASE_URL` / `CELINE_OIDC_JWKS_URI` left on the SDK's local Keycloak default;
- `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY` or `CLICK_TRACKING_SECRET` unset — otherwise they
  fail only when first used, after the service has come up;
- `WEBPUSH_ENDPOINT_RELAXED` enabled (REQ-0083).

The click-tracking secret has no fallback to the VAPID private key (REQ-0056). In dev the same list is one warning and the service starts. The OIDC
client id, secret and audience are read from `CELINE_OIDC_*`, so the secret can be set
without code.
