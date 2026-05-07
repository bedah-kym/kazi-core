# Examples

Canonical starting points for building on top of Kazi Core.

Everything under this directory is **OSS, copy-friendly, and runnable
without real API keys** when the runtime boots in demo mode
(`KAZI_DEMO_MODE=true`).

## Layout

```
examples/
├── connectors/      one directory per example connector
│   └── echo/        the canonical "copy this to start" connector
└── workflows/       one directory per example workflow (coming in v0.4 M5-1)
```

## Connectors

| Example | What it shows |
|---|---|
| [`connectors/echo/`](connectors/echo/) | The minimal connector. One action, no credentials, three obvious files. Start here when adding your own. |

## Workflows

*Landing in v0.4 M5-1 — the canonical
`request → workflow → approval → receipt → replay` demo will live under
`workflows/follow_up_email/`.*

## Adding your own

1. Copy an example into a new directory.
2. Update `name`, `version`, `actions`, and the catalog entry.
3. Implement `execute()`.
4. The runtime auto-discovers anything in `examples/connectors/*/` —
   no manifest, no registration call.

Full guide: [`docs/writing-a-connector.md`](../docs/writing-a-connector.md).
