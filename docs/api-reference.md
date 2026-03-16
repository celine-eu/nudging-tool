# API Reference

Interactive OpenAPI docs: `http://localhost:8000/docs`

## Event Ingestion

### `POST /ingest-event`

Ingest a Digital Twin event and trigger rule evaluation, orchestration, and delivery.

**Request body:**

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

**Response codes:**

| Code | Meaning |
|---|---|
| `200` | Nudge created and at least one delivery dispatched |
| `202` | Nudge created but all deliveries suppressed by orchestrator |
| `204` | No matching rule found for this event type |
| `400` | Unknown scenario or rule resolution failure |
| `409` | Delivery suppressed by deduplication |
| `422` | Missing or invalid facts fields |

---

## Web Push

### `GET /webpush/vapid-public-key`

Returns the VAPID public key for browser push subscription setup.

**Response:** `{"public_key": "BNF..."}`

### `POST /webpush/subscribe`

Register a browser push subscription.

```json
{
  "user_id": "user-it",
  "community_id": "COMM1",
  "subscription": {
    "endpoint": "https://fcm.googleapis.com/fcm/send/...",
    "keys": {
      "p256dh": "...",
      "auth": "..."
    }
  }
}
```

### `POST /webpush/unsubscribe`

Remove a push subscription by user and community.

### `POST /webpush/send-test`

Send a test push notification to a registered subscription.

```json
{
  "user_id": "user-it",
  "community_id": "COMM1"
}
```

---

## Notifications

### `GET /notifications`

List delivered notifications for a user.

**Query params:**
- `user_id` (required)
- `community_id` (required)
- `limit` — default 20
- `offset` — pagination offset

### `GET /notifications/{id}`

Get a single notification delivery record.

---

## Preferences

### `GET /preferences`

Get notification preferences for a user/community pair.

**Query params:** `user_id`, `community_id`

### `PUT /preferences`

Update notification preferences.

```json
{
  "user_id": "user-it",
  "community_id": "COMM1",
  "language": "en",
  "max_per_day": 5,
  "enabled": true
}
```
