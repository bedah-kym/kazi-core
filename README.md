<div align="center">
  <img src="assets/kazi-core.png" alt="Kazi Core Engine Mascot" width="200"/> 
  <h1>Kazi Core</h1>
  
  <p>
    <img src="https://img.shields.io/badge/python-3.11%20%7C%203.12-blue" alt="Python" />
    <img src="https://img.shields.io/badge/license-MIT-green" alt="License" />
    <img src="https://github.com/bedah-kym/kazi-core/actions/workflows/main.yml/badge.svg" alt="CI" />
    <a href="https://codecov.io/gh/bedah-kym/kazi-core"><img src="https://codecov.io/gh/bedah-kym/kazi-core/branch/main/graph/badge.svg" alt="codecov" /></a>
    <a href="https://kazi-core.readthedocs.io/"><img src="https://readthedocs.org/projects/kazi-core/badge/?version=latest" alt="Docs" /></a>
    <img src="https://img.shields.io/badge/status-early%20access-orange" alt="Status" />
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

You ask in plain language. Kazi plans the steps, runs them through the tools
and services you've wired in, pauses for your sign-off on anything sensitive,
records what happened, and remembers it for next time.

Think of it less like a chatbot and more like a **private chief-of-staff for
your own tools**.

## What you can do with it

- **Run your life** — *"Email John the invoice, remind me to follow up Thursday, and block my calendar for the trip."* One message, done.
- **Run a side business** — a WhatsApp order becomes an invoice, a payment, and a receipt — with every money step waiting for your approval.
- **Run your home lab** — point it at your services, scripts, and APIs; chat becomes the control plane.
- **Automate the boring dev chores** — file the issue, run the build, open the PR, watch the deploy.
- **Set standing jobs** — *"Watch my inbox for refund requests and draft the reply, but ask me before sending."* Runs for weeks, not seconds.
- **Do research and get an artifact** — a finished, formatted report instead of a wall of links.

Whatever tool you can imagine, one connector file wires it in — and the whole
loop applies to it instantly.

## Why Kazi over the big frameworks

Because it's **yours**:

- **Self-hosted** — data, keys, and conversations never leave your box.
- **Guardrails in the bones** — prompt-injection defense, parameter sanitization, risk gates, and durable human approval before anything sensitive.
- **Durable by design** — workflows survive restarts, approvals survive crashes, and every sensitive action leaves a receipt you can undo.
- **Bring your own everything** — LLM (Claude, DeepSeek, Hugging Face), payment rail, messenger, and connectors.

## Quick start

```bash
git clone https://github.com/bedah-kym/kazi-core.git && cd kazi-core
docker compose up --build -d db redis web celery_worker celery_beat
docker compose exec web python Backend/manage.py migrate
docker compose exec web python Backend/manage.py createsuperuser
```

Add your LLM key to a `.env` in the repo root, open `http://localhost:8000`,
and start talking. No keys handy? `bash scripts/demo.sh` boots the whole thing
in demo mode. → [run-locally](docs/run-locally.md)

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

Weather · currency · web search · Gmail · WhatsApp · Telegram · payments
(double-entry ledger + invoices) · Calendly · multi-modal travel · reminders ·
contacts · notes · skills. Plus durable workflows, a 3-tier memory system,
room linking, receipts, and a unified notification pipeline.

## Docs

1. [run-locally](docs/run-locally.md) — boot in 10 minutes, no real keys
2. [add-a-connector](docs/add-a-connector.md) — one file + one test
3. [add-a-workflow](docs/add-a-workflow.md) — author the JSON; the runtime does the rest
4. [operate-a-workflow](docs/operate-a-workflow.md) — approve, reject, rerun, replay
5. [deploy-safely](docs/deploy-safely.md) — production checklist

Everything else — [architecture](docs/architecture.md), [contracts](docs/contracts/README.md),
[eval](docs/eval.md), [trace](docs/trace.md), [CHANGELOG](CHANGELOG.md) — lives in [`docs/`](docs/).

## Status

Early access — breaking changes possible before v1.0. Container releases are
cosign-signed with SBOM + SLSA provenance. See [CHANGELOG](CHANGELOG.md) for history.

## Contributing · Security · License

Contributions welcome (connectors especially) — [CONTRIBUTING](CONTRIBUTING.md).
Vulnerabilities: [SECURITY](SECURITY.md) (private disclosure).
MIT — [LICENSE](LICENSE).
