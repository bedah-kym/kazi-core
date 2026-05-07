# Demo Mode

`KAZI_DEMO_MODE=true` boots the runtime in a configuration that needs
**no real API keys**. It is the right way to evaluate Kazi Core for the
first time and the default mode the example workflow pack runs in.

## What changes

Setting the env var flips on:

| Behavior | Off | On |
|---|---|---|
| Example connectors under `examples/connectors/*/` | not loaded | auto-discovered and registered |
| Boot banner | none | a warning banner is logged so demo data is never confused with real data |
| Future: mock LLM provider | uses configured provider | scripted responses for the demo prompt *(landing in v0.4 M5-2 hardening)* |
| Future: mock connector responses | real | recorded *(landing in v0.4 M5-1 demo pack)* |

## How to enable

In `.env`:

```bash
KAZI_DEMO_MODE=true
```

Or directly:

```bash
KAZI_DEMO_MODE=true docker compose up
```

Watch the boot logs — you will see the banner:

```
================================================================
  KAZI_DEMO_MODE=true  —  example connectors enabled, no real
  credentials required. DO NOT use this configuration in
  production. Set KAZI_DEMO_MODE=false (or unset it) to boot
  with the normal connector set.
================================================================
```

## How to disable

Unset the variable, or:

```bash
KAZI_DEMO_MODE=false docker compose up
```

## Testing your own example connector

Drop a directory under `examples/connectors/<your_name>/` containing
a `*_connector.py` file with a `BaseConnector` subclass:

```
examples/connectors/
└── your_name/
    ├── README.md
    └── your_connector.py
```

With `KAZI_DEMO_MODE=true`, the runtime auto-loads it on startup. See
[`examples/connectors/echo/`](../examples/connectors/echo/) for the
canonical template.

## Production warning

`KAZI_DEMO_MODE=true` should **never** be set in production. The boot
banner is intentionally noisy so this is hard to miss. Example
connectors are not credential-protected and example workflows do not
exercise risk gates the way the real connector set does.

## See also

- [`examples/README.md`](../examples/README.md) — what lives under
  `examples/`
- [`writing-a-connector.md`](writing-a-connector.md) — the full guide to
  building your own
