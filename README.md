<div align="center">
  <img src="assets/kazi-core.png" alt="Kazi Core Engine Mascot" width="200"/>
  <h1>Kazi Core</h1>

  <p>
    <img src="https://img.shields.io/badge/python-3.11%20%7C%203.12-blue" alt="Python" />
    <img src="https://img.shields.io/badge/license-MIT-green" alt="License" />
    <img src="https://github.com/bedah-kym/kazi-core/actions/workflows/main.yml/badge.svg" alt="CI" />
    <a href="https://codecov.io/gh/bedah-kym/kazi-core"><img src="https://codecov.io/gh/bedah-kym/kazi-core/branch/main/graph/badge.svg" alt="codecov" /></a>
    <a href="https://kazi-core.readthedocs.io/"><img src="https://readthedocs.org/projects/kazi-core/badge/?version=latest" alt="Docs" /></a>
    <img src="https://img.shields.io/badge/status-v0.7.0-informational" alt="Status" />
  </p>

  <p><strong>Your own AI agent. Your own server. Your own data.</strong></p>
</div>

> 💡 **Kazi** is Swahili for *work*. You say what needs doing — Kazi does it,
> asks before the risky parts, and keeps the receipts.

---

## What is Kazi?

Kazi is a self-hosted engine that turns a chat message into **completed work**:

```
intent → plan → act → approve → audit → remember
```

You ask in plain language. Kazi plans the steps, runs them through a governed
shell and the tools you've wired in, pauses for your sign-off on anything
sensitive, records what happened in an append-only trail, and remembers it for
next time.

Think of it less like a chatbot and more like a **private chief-of-staff for
your own tools** — one with hands, a memory, and a boss.

## The philosophy: governed autonomy

Most agents ask you to trust the model. Kazi is built so you don't have to:

- **The sandbox is the boundary, not the prompt.** One governed pair of hands
  (`run_command`) inside isolation profiles, with network off by default and a
  host allowlist. Safe commands run silently; bounded ones ask inline;
  destructive ones wait for a durable approval — with a workspace snapshot to
  roll back to.
- **Nothing self-authorizes.** "Permission once" is per-rule and scoped, and it
  lapses when behavior drifts. Learning loops propose; a human grants.
- **Receipts over promises.** Every sensitive action leaves an append-only
  audit trail that survives restarts and can be replayed or undone.
- **The core is PR-only.** The agent can improve its skills and workflows; it
  can never rewrite its own safety layer. Sandbox, risk gate, approvals, and
  receipts are changed only by a human-reviewed PR.
- **Done means verified** *(in flight — v0.8)*. A completion claim is checked
  against what actually changed — tool results and receipts — before it is
  stored or shown. The agent never grades its own homework.

This is the line between an assistant you babysit and one you can leave alone.

## What you can do with it

- **Run your home lab** — *"is the internet down?"* → pings the gateway, checks DNS, and offers to restart the Pi-hole: safe checks run silently, the restart waits for one tap, the receipt tells you what changed.
- **Run a side business** — a WhatsApp order becomes an invoice, a payment, and a receipt — with every money step waiting for you.
- **Run your life** — *"email John the invoice, remind me Thursday, and block my calendar for the trip."* One message, done.
- **Teach it a routine** — walk it through a job once, save it as a skill, and let it run on a schedule you approve once.
- **Do research and get an artifact** — a finished, formatted report instead of a wall of links.

## What's new in v0.7 — the teammate you can name

- **Skills and routines.** Repeated work is promoted into staged skill drafts;
  routines have an owner and pause when nobody is around.
- **Versioned workflows with standing grants.** Every run is bound to a
  definition version, and a grant is scoped to version, trigger and capability,
  so it lapses when the workflow drifts or starts failing.
- **Personas.** Named specialists with deterministic scope and a risk ceiling,
  managed from a dashboard. The handoff runtime between them ships too; a way
  to start one from chat comes next.
- **A shell that asks at the boundary.** Sandboxed commands with no network run
  without a prompt; leaving the sandbox asks. Untrusted output taints the room
  and raises the approval tier.

Read the [v0.7 brief](docs/v0.7-brief.md) and the [changelog](CHANGELOG.md).

## What's new in v0.6 — the governed shell

Kazi grew hands, and they're on a leash:

- **`run_command` through a deliberately dumb sidecar** — non-root,
  read-only rootfs, no capabilities, network off by default. All policy lives
  in Kazi's orchestration layer, next to the rest of the governance.
- **Isolation profiles** — `open` / `standard` / `locked`, with per-room and
  per-user overrides.
- **Two-tier escalation** — safe auto-runs, bounded prompts inline, destructive
  pauses for durable approval (with the diff), and out-of-envelope commands are
  denied outright.
- **A persistent workspace** — scripts and solved jobs accumulate per room, so
  the agent gets better at your chores without a new connector.
- **A learning foundation** — nightly telemetry rollups, preference mining from
  your approve/deny history, and an initiative ladder that proposes instead of
  acting.

Read the [v0.6 brief](docs/v0.6-brief.md) for the story behind it.

## Where it's going

- **v0.8 — turns that own the truth.** A persisted turn state machine, a typed
  stream protocol, and a verification layer so "done" is decided by the record,
  not the narration. →
  [proposal](docs/proposals/2026-10-turn-state-and-verification.md) · [epic #227](https://github.com/bedah-kym/kazi-core/issues/227)

## Why Kazi over the big frameworks

Because it's **yours**:

- **Self-hosted** — data, keys, and conversations never leave your box.
- **Governed by construction** — sandbox boundary, risk gates, durable human
  approval, prompt-injection defense, and parameter sanitization.
- **Verified, not vibes** — append-only receipts and (soon) completion checks
  against the record; nothing self-authorizes.
- **Durable by design** — workflows survive restarts, approvals survive crashes,
  and every sensitive action leaves a trail you can replay.
- **Bring your own everything** — LLM (Claude, DeepSeek, Hugging Face), payment
  rail, messenger, and connectors.

## Quick start

**Prerequisites:** git, Docker, and one LLM API key (Anthropic / DeepSeek / Hugging Face).

```bash
git clone https://github.com/bedah-kym/kazi-core.git && cd kazi-core
cp .env.example .env            # 1. paste your LLM key into .env
docker compose up --build -d    # 2. boots db, redis, web, celery — auto-migrates + seeds the bot
docker compose exec web python Backend/manage.py createsuperuser   # 3. make your login
```

Open `http://localhost:8000`, log in, and say hi to **Kazi** — your General
room routes messages to the AI automatically.

> **No keys handy?** `bash scripts/demo.sh` boots a demo instead → [run-locally](docs/run-locally.md).
> **Stuck, or want the full walkthrough** (troubleshooting, non-Docker setup, Temporal)?
> → [Quick Start guide](docs/quickstart.md).

> **Note:** the bot's username is `kazi` (lowercase), and the same name appears
> in the docker image and database defaults. It's the AI assistant built into
> Kazi Core — same system, just the name we gave the bot.

## Add a tool in one file

Drop a connector in `Backend/orchestration/connectors/` and it auto-registers on restart:

```python
from orchestration.base_connector import BaseConnector


class MyConnector(BaseConnector):
    name = "my_service"
    version = "0.1.0"
    actions = ["do_something"]

    async def execute(self, parameters, context):
        return {"status": "success", "message": "Done"}
```

→ [add-a-connector](docs/add-a-connector.md)

## Ships with

A governed shell (sandboxed `run_command` + persistent workspace) · durable
workflows · a 3-tier memory · skills · append-only receipts · a unified
notification pipeline · connectors for weather, currency, web search, Gmail,
WhatsApp, Telegram, payments (double-entry ledger + invoices), Calendly, travel,
reminders, contacts, and notes.

## Docs

1. [run-locally](docs/run-locally.md) — boot in 10 minutes, no real keys
2. [add-a-connector](docs/add-a-connector.md) — one file + one test
3. [add-a-workflow](docs/add-a-workflow.md) — author the JSON; the runtime does the rest
4. [operate-a-workflow](docs/operate-a-workflow.md) — approve, reject, rerun, replay
5. [deploy-safely](docs/deploy-safely.md) — production checklist

Everything else — [architecture](docs/architecture.md), [contracts](docs/contracts/README.md),
[eval](docs/eval.md), [trace](docs/trace.md), [CHANGELOG](CHANGELOG.md) — lives in [`docs/`](docs/).

## Status

Early access — breaking changes possible before v1.0. Current release:
**v0.7.0 (the teammate you can name)**. Container releases are cosign-signed with SBOM +
SLSA provenance. See [CHANGELOG](CHANGELOG.md) for history and
[roadmap](docs/roadmap.md) for what's next.

## Contributing · Security · License

Contributions welcome (connectors especially) — [CONTRIBUTING](CONTRIBUTING.md).
Vulnerabilities: [SECURITY](SECURITY.md) (private disclosure).
MIT — [LICENSE](LICENSE).
