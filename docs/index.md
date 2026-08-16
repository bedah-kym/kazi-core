# Kazi Core

*Karibu* — welcome.

**The self-hostable runtime for human-supervised agent workflows.** Agents that
call their own tools, run on your own infrastructure, and keep your data in
your stack — no vendor lock-in, no data leaving your server.

## Why people pick Kazi

- **The agent remembers.** A 3-tier memory system (hot / warm / cold) persists
  what it learns about your rooms, contacts, and preferences across conversations.
- **Nothing important runs on autopilot.** High-risk actions pause at durable
  human checkpoints and only resume after you approve.
- **You own it.** Your server, your data, your payment rail. Security — injection
  detection, parameter sanitization, risk gates — ships in the core.

[See the full feature tour →](features.md)

## Start in 10 minutes (no API keys)

The fastest way to see it work is the demo driver:

```bash
git clone https://github.com/bedah-kym/kazi-core.git
cd kazi-core
bash scripts/demo.sh
```

This boots the stack in demo mode, seeds a workflow, and prints the commands to
drive it through a full approve → run → replay loop.

[Step-by-step walkthrough →](run-locally.md) ·
[Manual setup with your own LLM key →](quickstart.md)

## Build something

- [Add a Connector](add-a-connector.md) — teach Kazi a new skill in one file
- [Writing a Connector](writing-a-connector.md) — the full reference
- [Add a Workflow](add-a-workflow.md) — sequence tools with approval gates
- [Operate a Workflow](operate-a-workflow.md) — approve, rerun, replay safely
- [Deploy Safely](deploy-safely.md) — take it to production

## Help it grow

Kazi is early access and community-governed. The fastest contribution is a
connector — see [Add a Connector](add-a-connector.md) and the
[Contributing guide](https://github.com/bedah-kym/kazi-core/blob/main/CONTRIBUTING.md).
Report bugs, write docs, or add a connector; every merged PR shapes the roadmap.

## Reference

- [Architecture](architecture.md) — how requests flow through the engine
- [Configuration](configuration.md) — every environment variable, grouped by feature
- [Connector API Reference](connector-api-reference.md)
- [Contracts](contracts/README.md)
- [Trace CLI](trace.md)
