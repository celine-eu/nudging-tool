# ADR-0003 — the database is real, and it is SQLite

**Date:** 2026-08-15
**Status:** accepted

## Context

Most of what this service decides, it decides in SQL: which preference row applies, how
many deliveries went out today, whether a dedup key already exists. Mocking the session
would have tested the mock.

PostgreSQL is what runs in production, and requiring it would break ADR-0002.

## Decision

Give each test an in-memory SQLite database built from `Base.metadata`, with `StaticPool`
so that every connection sees the same database, and let every query run for real.

The schema comes from the models, not from the migrations.

**One PostgreSQL behaviour is simulated, deliberately and narrowly.** `_run_single_rule`
recognises a duplicate by finding the literal `uq_nudges_dedup_key` in the driver's error
text. PostgreSQL puts the constraint name there; SQLite says `UNIQUE constraint failed:
nudges_log.dedup_key` and never names the constraint. Without help, the dedup branch would
be unreachable on SQLite and every duplicate would escape as a `500` — so the whole of
REQ-0035, the suppression this repository most needs verified, would be untested.

A `handle_error` listener in `conftest.py` rewrites SQLite's message into PostgreSQL's
wording **for that one constraint**, and `tests/unit/test_engine_dedup.py` pins the
literal against the model *and* against the migration that creates it.

## Consequences

**The migrations are not exercised.** A model that has drifted from `alembic/versions/`
passes here and fails on deploy. This is the largest gap in the suite and it is stated in
`docs/specifications/index.md` rather than left to be discovered.

**PostgreSQL-only behaviour is not exercised either**: `FOR UPDATE SKIP LOCKED` in the
scheduler is a no-op on SQLite, so REQ-0065's claim that two replicas do not take the same
row is verified as *code that asks for it*, not as behaviour under contention. Types
differ too — `JSON` rather than `JSONB`, timestamps stored as strings.

**The simulated error is the thing to watch.** It is scoped to one message and one
constraint, and the constraint name is pinned in three places, so a rename fails the suite
rather than being papered over. Widening it — a generic "make SQLite talk like
PostgreSQL" shim — would remove that guarantee, and is the change to refuse.

Superseded by running the suite against a real PostgreSQL, which would remove both gaps
and the simulation with them.
