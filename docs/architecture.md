# Kazi Architecture

## Overview

Kazi is an agent-first runtime. When a user sends a message, it flows through
the chat consumer into the agent loop, which selects tools, executes them
through a governed executor, and returns the reply. The agent loop is the only
path for a chat turn.

```
Client (WebSocket / HTTP)
  |
  v
ChatConsumer (Django Channels)
  |
  v
OrchestrationCoordinator (post-@Kazi routing)
  |
  v
ContextManager (memory assembly)
  |
  v
Agent Loop (ReAct: think -> act -> observe)
  |
  v
Tool Executor (capability + safety gates)
  |
  +--> Connectors (plugins — your code goes here)
  +--> Governed shell (run_command -> sandbox sidecar)
  +--> Ad-hoc workflow handoff (execute_adhoc_workflow)
```

## Core Components

### Agent Loop (`orchestration/agent_loop.py`)

The brain. Implements a ReAct (Reasoning + Acting) loop:

1. **Think** — LLM receives the conversation, context, and available tools
2. **Act** — LLM decides to call a tool (or respond directly)
3. **Observe** — Tool result comes back, LLM sees it
4. **Repeat** — Until the task is done or limits are hit

Every chat turn runs this loop; there is no planner, intent parser or second
routing pipeline to plan and act on the same message twice. A multi-step request
the agent composes is handed to `execute_adhoc_workflow`, which starts and runs
the ad-hoc workflow.

Safety limits (on by default; an operator can switch them off in
Settings > Capabilities):
- Max 10 iterations per loop
- Max 15 tool calls per loop
- 2-minute loop timeout, 30 seconds per tool
- 50k token budget
- 2 retries per failed tool, with exponential backoff and a per-service circuit breaker

### OrchestrationCoordinator (`orchestration/coordinator.py`)

The routing facade that owns everything after `@Kazi` routing: directives,
pending confirmations (durable agent-loop approvals), the agent loop, and
general chat. The WebSocket consumer delegates to it via injected async
callbacks, so it has no Channels dependency of its own.

### Tool Executor (`orchestration/tool_executor.py`)

The gateway between the agent and connectors. Before executing any tool:

1. Resolves action aliases (e.g., "email" -> "send_email")
2. Checks the capability gate
3. Checks security policy (prompt injection, restricted params)
4. Sanitizes parameters
5. Verifies action risk level
6. Routes to the correct connector
7. Handles errors and timeouts

### Governed Shell (`orchestration/shell_exec/`)

`run_command` reaches the host through a *deliberately dumb* sidecar
(`python Backend/manage.py run_shell_exec`, run as its own uvicorn process) that
holds no policy: it checks a shared token and runs the command in the room's
workspace. All policy lives in Kazi's orchestration layer. The orchestrator
fails closed: leaving `SHELL_EXEC_TOKEN` unset disables `run_command` entirely.

On the Docker profiles (`standard`, `locked`) the sidecar runs each command in a
container: non-root, read-only root filesystem, `--cap-drop=ALL`,
no-new-privileges, network off by default, with memory and PID caps. The `open`
profile uses a direct host subprocess instead.

Isolation profiles, resolved per room (override) else the global default:

| | `open` | `standard` (default) | `locked` |
|---|---|---|---|
| Backend | direct subprocess | Docker | Docker |
| Root filesystem | full | read-only | read-only |
| Network | full | off by default | none |

`open` is never the default and logs a loud boot warning — it runs unsandboxed
on the host, so only enable it on a disposable box. The per-room workspace
persists across commands, and a destructive command is snapshotted first so it
can be rolled back.

### Connector Registry (`orchestration/connector_registry.py`)

Auto-discovers connectors from:
1. Built-in connectors in `orchestration/connectors/`
2. Legacy hardcoded connectors (some defined in `orchestration/tool_router.py`,
   formerly `mcp_router.py`; the old name is a deprecation shim)
3. Pip-installed packages with `kazi.connectors` entry points
4. Example connectors under `examples/connectors/` when demo mode is on

Entry-point connectors cannot shadow an action name owned by a built-in
connector — the conflicting registration is refused and logged.

### Security Policy (`orchestration/security_policy.py`)

Multi-layer protection:
- **Prompt injection detection** — regex patterns for common injection attempts
- **Parameter sanitization** — strips restricted keys (user_id, token, api_key, etc.)
- **Action blocking** — prevents high-risk actions when conditions aren't met
- **Room access validation** — ensures users can only act in their chatrooms

### Action Catalog (`orchestration/action_catalog.py`)

Single source of truth for all available actions. Each entry defines:
- Action name and aliases
- Parameter schema (types, required/optional)
- Risk level and confirmation policy
- Capability gate (permission required)

Connectors can dynamically register additional entries via `register_actions()`.

### Memory System

Three tiers:
1. **Hot context** (`chatbot/context_manager.py`) — recent messages, active conversation
2. **Entity tracking** (`orchestration/memory_state.py`) — extracted entities, facts
3. **Persistent summaries** — long-term memory stored in the database

### LLM Client (`orchestration/llm_client.py`)

Provider-agnostic LLM interface with three providers:
- Anthropic Claude (primary; native Messages API with tool-calling)
- DeepSeek (OpenAI-compatible, full tool-calling via an Anthropic↔OpenAI adapter)
- Hugging Face Inference API (text-only fallback)

Features: token budgeting, response caching, and automatic fallback between
providers. `create_message` / `stream_message` accept an explicit `provider` +
`model`; when no provider is given, the client falls back Anthropic → DeepSeek →
Hugging Face, resolving each provider's own model name on the way down.

### Model Catalog & Selection (`orchestration/model_catalog.py`)

A frozen catalog of the models the chatroom picker offers, keyed `provider/model`
with a `fast` / `high` / `vision` tier. The agent loop applies a per-room override
(see the model selector dropdown) when one is set; otherwise it picks a default
Claude model by message shape, with `Auto` routing through the provider fallback.

## Untrusted Text and Taint (`orchestration/agent_loop.py`)

Output from the shell (`run_command`), a web search, or a delegated run is
untrusted. When it enters a room the room is marked **tainted** for
`AGENT_TAINT_TTL_SECONDS` (default 900; `0` limits taint to the run that picked
it up). While tainted, external-write and credential-scoped actions move up one
approval tier, so no stored rule can skip the ask, and a shell command that
needs egress asks again even for an approved host. A cache read failure counts
as tainted. A turn whose replayed history contains untrusted output starts
tainted, and sub-agents inherit their parent's taint and acquire their own.

Tool results reach the model as delimited `tool_result` data blocks, sanitized
of injection patterns and secrets before they enter context.

## Approvals and Receipts

A high-risk action (or an explicitly gated workflow step) pauses for a human
decision. Inside the agent loop the paused call is written to a room-scoped
`WorkflowApprovalRecord` (`kind=agent_loop`) that survives Redis eviction; a
durable workflow run writes the same record with `kind=workflow` and links it to
the execution. An approval reply must be the whole message — a reply that starts
with an approval word and then says something else cancels the pending action
instead of running it.

Sensitive actions write an append-only `ActionReceipt`: the action, its
sanitized parameters, the result, the status, and whether it is reversible, plus
the `approval_basis` that let a shell command run.

## Supporting Systems

### Real-Time Transport (`chatbot/consumers.py`)
Django Channels WebSocket consumer. Handles connection auth, encryption,
message persistence, and presence; routing decisions are delegated to
`OrchestrationCoordinator`. A reply is delivered to the room whole, as the model
finished it.

### Notifications (`notifications/`)
Unified notification system with in-app, email, and WhatsApp channels.
WebSocket push for real-time delivery.

### Payments (`payments/`)
Wallet management, invoices, transaction history. IntaSend integration for
M-Pesa and card payments.

### Travel (`travel/`)
Flight, hotel, bus, transfer, and event search via Amadeus.
Itinerary management and booking.

### Workflows (`workflows/`)
Durable multi-step workflow execution via Temporal. A workflow is a versioned
JSON definition; every change appends a `WorkflowVersion` and moves the version
pointer, and an execution records the version it started with, so a live
definition is never edited in place. Triggers are webhook, schedule or manual,
and a schedule is registered with the Temporal scheduler.

Runtime additions in the current cycle:
- Durable approval checkpoints for high-risk or explicitly gated steps
- Durable agent-loop approvals: high-risk tool pauses inside the ReAct loop write a room-scoped `WorkflowApprovalRecord` (kind=`agent_loop`) so they survive Redis eviction and are auditable
- Execution detail records with current step, waiting reason, receipts, and replay hints
- Operator controls for approve, reject, cancel, rerun, and schedule pause/resume
- Deferred-run watchdog state with backoff and dead-letter reasons

Typical operator flow:
1. Start a workflow through the workflow API
2. Inspect `WorkflowExecution` for `status`, `current_step`, and `pending_approval`
3. Approve or reject a waiting step
4. Review receipts and `result_summary`
5. Rerun from the failed step only when the remaining steps are replay-safe

### Skills, Routines and Standing Grants
A skill is a `SKILL.md` folder in a `staging` / `review` / `active` lifecycle;
only `active` skills are exposed, and the agent discovers and loads them with the
`list_skills` / `load_skill` meta-tools. A routine binds one to a schedule or
event and must answer the routine contract before it can be enabled. A standing
grant is scoped to a workflow version, a trigger and a capability, and lapses on
drift, failure spikes or expiry. Internal specialist handoffs run on the
`delegate_task` sub-agent machinery with one owner per stage and a budget cap.

## Data Flow Example

User sends: "What's the weather in Nairobi?"

```
1. ChatConsumer receives WebSocket message
2. OrchestrationCoordinator routes it (post-@Kazi) into the agent loop
3. ContextManager assembles conversation history + memory
4. Agent Loop starts:
   a. LLM sees: system prompt + context + tools + user message
   b. LLM responds: tool_use(get_weather, {"city": "Nairobi"})
   c. Tool Executor:
      - Resolves "get_weather" in action catalog
      - Capability + security checks pass (low risk, no injection)
      - Routes to WeatherConnector
   d. WeatherConnector calls OpenWeather API
   e. Result: {"status": "success", "data": {"temp": 22, ...}}
   f. LLM sees result, generates natural language response
   g. Agent Loop yields "done" event
5. ChatConsumer sends the reply to the client over the WebSocket
```

A request that touches the host — "check the disk usage" — takes the same loop
and calls `run_command` instead: the Tool Executor classifies the command, the
sidecar runs it in the sandbox, and the result returns into the loop as data.

## Key Design Principles

1. **Connectors are the extension point** — everything external goes through a connector, or the governed shell
2. **The sandbox is the boundary** — not the prompt; the classifier only reduces prompts
3. **One agent loop, one path** — a chat turn cannot be planned and acted on twice
4. **Security by default** — injection detection, param sanitization, confirmation gates
5. **Model agnostic** — swap LLM providers without changing application code
6. **Fail gracefully** — tool errors become conversation, not crashes
