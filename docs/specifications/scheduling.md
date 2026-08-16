# Scheduling

An event stored now and dispatched later by a polling loop inside the API process. There
is no caller to answer at trigger time and no response code — the only record of what
happened is the row's own `status` and `last_error`.

---

### REQ-0063 — a scheduled event is validated when it is stored, not when it fires

`POST /admin/scheduled-events` requires the ingest permission and the same facts contract
as a live event (REQ-0012): non-empty facts carrying `facts_version` and `scenario`,
otherwise `422`.

Checking now rather than at trigger time is what makes the violation answerable: one
discovered by the scheduler is a `failed` row nobody is watching.

The time scope (REQ-0020) is **not** checked here, so an event with an unusable `time`
is accepted and fails at dispatch.

### REQ-0064 — `external_key` makes scheduling idempotent

A second post with the same key rewrites the existing row — event type, participant,
community, trigger time and facts — and resets it to `pending` with no `dispatched_at`
and no `last_error`. A sender that retries, or that recomputes a reminder when a
commitment changes, gets one row.

This works on an event that has already been dispatched: re-posting makes it pending
again and the reminder goes out again, with deduplication (REQ-0035) as the only thing
stopping a second notification for the same period.

The idempotence is **opt-in**: without a key, two identical posts are two rows.

### REQ-0065 — the loop takes due, pending events, oldest first, in bounded batches

Every `SCHEDULER_POLL_SECONDS`, at most twenty per poll, ordered by `trigger_at`,
selecting `status = 'pending'` and `trigger_at <= now`, with `FOR UPDATE SKIP LOCKED` so
that two replicas do not take the same row.

`dispatched` and `failed` are therefore terminal: **a failed dispatch is never retried**
and somebody has to reset the row by hand.

The loop waits on its stop event with a timeout rather than sleeping, so shutdown is
immediate rather than delayed by a full poll interval.

### REQ-0066 — a dispatched event runs the same path as an ingested one

The stored facts become a `DigitalTwinEvent` and go through the engine and the
orchestrator unchanged, so a scheduled reminder is deduplicated, rate-limited and
preference-filtered exactly like a live event.

"Dispatched" means the engine ran, not that a message was sent: an event whose rule
declined, or whose notification was suppressed, ends in the same state as one that
reached a browser.

### REQ-0067 — one failing event does not take the batch or the loop with it

An exception during dispatch marks that row `failed` with the error text in `last_error`,
leaves `dispatched_at` unset, and the remaining events in the batch are still processed.
A successful dispatch clears a previous `last_error`.

An exception escaping the poll entirely is caught by the loop, which continues. Nothing
restarts this task: if it exits, the only symptom is that reminders stop arriving.
