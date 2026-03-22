# Kazi Architecture

## Overview

Kazi is an agent-first AI platform. When a user sends a message, it flows through
a pipeline that understands intent, selects tools, executes actions, and streams
results back in real time.

```
Client (WebSocket / HTTP)
  |
  v
ChatConsumer (Django Channels)
  |
  v
ContextManager (memory assembly)
  |
  v
Agent Loop (ReAct: think -> act -> observe)
  |
  v
Tool Executor (safety gates + capability checks)
  |
  v
Connectors (plugins — your code goes here)
```

## Core Components

### Agent Loop (`orchestration/agent_loop.py`)

The brain. Implements a ReAct (Reasoning + Acting) loop:

1. **Think** — LLM receives the conversation, context, and available tools
2. **Act** — LLM decides to call a tool (or respond directly)
3. **Observe** — Tool result comes back, LLM sees it
4. **Repeat** — Until the task is done or limits are hit

Safety limits:
- Max 10 iterations per loop
- Max 15 tool calls per loop
- 2-minute timeout
- 50k token budget
- 2 retries per failed tool

### Tool Executor (`orchestration/tool_executor.py`)

The gateway between the agent and connectors. Before executing any tool:

1. Resolves action aliases (e.g., "email" -> "send_email")
2. Checks security policy (prompt injection, restricted params)
3. Verifies action risk level
4. Routes to the correct connector
5. Handles errors and timeouts

### Connector Registry (`orchestration/connector_registry.py`)

Auto-discovers connectors from three sources:
1. Built-in connectors in `orchestration/connectors/`
2. Legacy connectors in `orchestration/mcp_router.py`
3. Pip-installed packages with `kazi.connectors` entry points

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

Provider-agnostic LLM interface. Currently supports:
- Anthropic Claude (primary)
- HuggingFace Inference API (fallback)

Features: token budgeting, response caching, automatic fallback between providers.

## Supporting Systems

### Real-Time Transport (`chatbot/consumers.py`)
Django Channels WebSocket consumer. Handles connection auth, message routing,
streaming responses, and presence.

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
Durable multi-step workflow execution via Temporal.
Activity-based execution with retry and timeout policies.

## Data Flow Example

User sends: "What's the weather in Nairobi?"

```
1. ChatConsumer receives WebSocket message
2. ContextManager assembles conversation history + memory
3. Agent Loop starts:
   a. LLM sees: system prompt + context + tools + user message
   b. LLM responds: tool_use(get_weather, {"city": "Nairobi"})
   c. Tool Executor:
      - Resolves "get_weather" in action catalog
      - Security check passes (low risk, no injection)
      - Routes to WeatherConnector
   d. WeatherConnector calls OpenWeather API
   e. Result: {"status": "success", "data": {"temp": 22, ...}}
   f. LLM sees result, generates natural language response
   g. Agent Loop yields "done" event
4. ChatConsumer streams response to client via WebSocket
```

## Key Design Principles

1. **Connectors are the extension point** — everything external goes through a connector
2. **Security by default** — injection detection, param sanitization, confirmation gates
3. **Model agnostic** — swap LLM providers without changing application code
4. **Real-time first** — WebSocket streaming, not request-response
5. **Fail gracefully** — tool errors become conversation, not crashes
