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

## Next

- [Telegram](telegram.md) — send alerts to Telegram instead
- [Operate a Workflow](operate-a-workflow.md) — see approval notifications in action
