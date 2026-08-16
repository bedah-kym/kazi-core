# Telegram

Hook Kazi up to Telegram so it can send text, media, and inline keyboards
through a bot. Four steps.

## 1. Create a bot

Message [@BotFather](https://t.me/BotFather) on Telegram, send `/newbot`, and
copy the token it gives you.

## 2. Add the token

In your `.env`:

```bash
TELEGRAM_BOT_TOKEN=123456:ABC-DEF...
```

## 3. Point the webhook

Tell Telegram where your bot lives:

```bash
https://api.telegram.org/bot<TOKEN>/setWebhook?url=<YOUR_DOMAIN>/api/telegram/webhook/
```

Replace `<TOKEN>` with the bot token and `<YOUR_DOMAIN>` with your public URL.

## 4. Restart

The connector is auto-discovered on boot — no manual routing, no config edits.
If the token is missing, boot logs a warning instead of crashing.

## What you get

| Action | What it does |
|---|---|
| `send_telegram_message` | Text, with MarkdownV2 (bold, italic, code, links) |
| `send_telegram_media` | Photo, video, document, or audio |
| `send_telegram_keyboard` | Inline keyboards (callback + URL buttons) |
| `edit_telegram_message` | Edit a message you sent |
| `delete_telegram_message` | Delete a message you sent |
| `telegram_health` | Check the bot is alive |

The agent calls these like any other tool — no special syntax.

## Next

- [Notifications](notifications.md) — route alerts to Telegram
- [Configuration](configuration.md) — the full env reference
