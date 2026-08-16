# Delivery

The two ways a message leaves this service, and what is recorded about it. `delivery_log`
is the only record that an attempt happened, so its contents are as much the subject here
as the send.

---

### REQ-0042 — the web job is unconditional and the email job is not

One notification becomes one job per channel:

- **web**, always — `channel_web` on the preference row is *not consulted*, so a
  participant cannot switch the web channel off through this path;
- **email**, when the facts name explicit recipients, or the participant set
  `channel_email` **and** left an address.

The exception is an email-only ingest (REQ-0014): a `user_id` beginning `email-ingest:`
together with explicit recipients gets no web job. Both halves are required — recipients
under a real participant, or a synthetic id with no recipients, still get one.

The web destination is `web:<user>` or `web:<user>:<community>`, and that string is what
the daily cap counts (REQ-0039), so its shape is load-bearing rather than cosmetic.

Jobs of one notification share its `nudge_id`, `notification_id` and `dedup_key` and
differ only in channel, destination and their own id.

Only `web` and `email` have publishers. `Channel` also declares `telegram` and
`whatsapp` — storable on a preference row — and asking for either raises.

### REQ-0043 — explicit recipients are filtered, de-duplicated and kept in order

`facts.email_recipients` must be a list; entries are trimmed, matched against a simple
address pattern, de-duplicated case-insensitively keeping the first spelling, and kept in
the order given.

**An unparseable address is dropped silently.** A sender whose list is half typos is told
nothing; a sender whose list is *entirely* typos gets `missing_target` (REQ-0013).

Explicit recipients replace the participant's own address rather than adding to it.

### REQ-0044 — a push goes to every enabled subscription of that participant

Selected by user id and `enabled`. A disabled subscription and another participant's are
excluded in SQL rather than by the push service rejecting them.

When the job carries a community, subscriptions for that community **and** those with no
community are included — a browser that subscribed before the participant joined a
community keeps working. When the job carries none, the community filter is not applied
at all, so a community-scoped subscription receives it. Scoping down is filtered; scoping
up is not.

### REQ-0045 — a push carries the message, the ids, and a signed click token

`{title, body, data: {url, nudge_id, rule_id, notification_id, click_tracking_token}}`.
The token is verifiable and names exactly the notification it was minted for; it is
`null` when the job has no notification id. `url` defaults to `/`.

The token is what lets a service worker report a click from a page with no session
(REQ-0056).

### REQ-0046 — a subscription the push service has forgotten is disabled

`404` and `410` mean the endpoint is gone for good, and it is disabled so that it is not
retried for every notification for ever — each retry being a request to somebody else's
server.

Any other failure, including an exception carrying no response, leaves the subscription
enabled: a `503` during an outage is not evidence that a browser has gone.

The same handling applies to an administrator's test push (REQ-0005).

### REQ-0047 — an absent recipient and an absent configuration are failures, not errors

Recorded and returned rather than raised:

| Situation | Recorded |
|---|---|
| the participant has no enabled subscription | `failed`, `no_subscriptions` |
| `VAPID_PRIVATE_KEY` is unset | `failed`, `Missing VAPID_PRIVATE_KEY` |
| `SMTP_HOST` or `EMAIL_FROM` is unset | `failed`, `Missing SMTP_HOST` / `Missing EMAIL_FROM` |

The first is the ordinary case for anyone who never allowed notifications in their
browser, so `delivery_log` reports failures for every such participant and a genuine
outage looks the same.

The other two mean a service deployed without configuration keeps accepting events and
silently delivers nothing.

### REQ-0048 — one delivery-log row per attempt, whatever the outcome

The row's id is the job's id, and it carries the nudge id, the destination, the status,
the error and `sent_at` when it was sent.

The web publisher records its channel as **`webpush`** while the job's channel is `web`.
Nothing depends on the agreement, because the cap counts by destination prefix rather
than by channel.

### REQ-0049 — email is `text/plain`, over STARTTLS unless SSL is configured

Subject is the rendered title, body the rendered body. `SMTP_USE_SSL` selects implicit
TLS and makes `SMTP_USE_TLS` irrelevant; the default is STARTTLS on port 587. With no
`SMTP_USERNAME` the login step is skipped rather than attempted empty — an
unauthenticated relay is a supported configuration.

Every exception is caught and recorded, so one channel failing never stops the other from
being tried.
