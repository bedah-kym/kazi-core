# Notifications

One pipeline, every channel. Events — reminders, payments, system alerts,
messages — route to in-app, email, or WhatsApp based on user preference.

## Channels

| Channel | Delivery |
|---|---|
| In-app | Real-time WebSocket push |
| Email | Via the mail connector |
| WhatsApp | Via the WhatsApp connector |

A built-in debounce stops notification spam when one event fires many times.

## What triggers them

- Reminders ("remind me to call John in 10 minutes")
- Workflow approval requests (durable checkpoints notify operators)
- Payment events (invoices, disputes)
- System alerts

## Configuring

Channels are per-user preference, not global flags. The routing logic lives in
the `notifications/` app; connectors for the actual delivery (email, WhatsApp)
come from the connector registry.

## In-app notification center

Logged-in users get a full inbox at `/notifications/` (view:
`notifications.views.notification_center`). It renders the same records
as the JSON API below, with unread and event-type filters, pagination,
deep links into chat rooms and invoices, and mark-read/dismiss actions.
The chat header bell still shows a compact dropdown for quick checks.

The machine surface stays at:

- `GET /notifications/api/` — paginated JSON list (`page`, `per_page`,
  `event_type`, `unread_only`)
- `GET /notifications/api/counts/` — unread counts
- `POST /notifications/api/<pk>/read/`, `/notifications/api/read-all/`,
  `/notifications/api/<pk>/dismiss/`

## Next

- [Telegram](telegram.md) — send alerts to Telegram instead
- [Operate a Workflow](operate-a-workflow.md) — see approval notifications in action
