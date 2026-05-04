# API Reference

Interactive OpenAPI docs: `http://localhost:8016/docs`

## Admin Routes

All admin routes are under the `/admin` prefix and require service account authentication with appropriate scopes.

### `POST /admin/ingest-event`

Ingest an event and trigger rule evaluation, orchestration, and delivery.

**Request body:**
```json
{
  "event_type": "imported_up",
  "user_id": "user-it",
  "community_id": "COMM1",
  "facts": {
    "scenario": "imported_up",
    "time": "2026-01-10",
    "delta_pct": 25.0
  }
}
```

### `POST /admin/scheduled-events`

Create a scheduled event to be processed at a future time.

### `POST /admin/seed/apply`

Apply seed data (rules, templates) from a seed directory. Used by the `nudging-cli seed apply` command.

### `POST /admin/webpush/send-test`

Send a test push notification to a registered subscription.

### `GET /admin/notifications`

List notifications (admin view, not scoped to a single user).

---

## User Notifications

### `GET /notifications`

List delivered notifications for the authenticated user.

**Query params:**
- `limit` — max results
- `offset` — pagination offset

### `PUT /notifications/{id}/read`

Mark a notification as read.

### `DELETE /notifications/{id}`

Soft-delete a notification.

### `POST /notifications/track-click`

Track that a web push notification has been clicked.

```json
{
  "token": "signed-tracking-token-from-push-payload",
  "action": "default"
}
```

---

## User Preferences

### `GET /preferences/me`

Get notification preferences for the authenticated user.

### `GET /preferences/catalog`

Get the notification kind catalog (available notification types with i18n labels).

### `PUT /preferences/me`

Update notification preferences.

---

## Web Push

### `GET /webpush/vapid-public-key`

Returns the VAPID public key for browser push subscription setup.

### `POST /webpush/subscribe`

Register a browser push subscription.

### `POST /webpush/unsubscribe`

Remove a push subscription.

---

## Health

### `GET /health`

Service health check.
