
# Mathia.OS - Agents & Orchestration Architecture

## Overview

Mathia.OS is an AI-powered operating system that uses an **LLM-first orchestration loop** to handle user requests across social media, finance, travel, and productivity domains. The system follows a three-stage pipeline: **Parse → Route → Execute**, with safety gates, multi-turn state, and streaming responses throughout.

---

## System Architecture

```
User (WebSocket)
     │
     ▼
ChatConsumer (consumers.py)
     │
     ├── Validation & Encryption (AES-256 GCM)
     ├── Moderation Queue (Celery → toxic-bert)
     │
     ▼
Orchestration Decision Gate
     │
     ├── @mathia mention OR AI room → Route to AI
     │
     ▼
┌─────────────────────────────────────────────┐
│            ORCHESTRATION PIPELINE           │
│                                             │
│  1. ContextManager.get_context_prompt()     │
│     └── 3-tier memory (hot/notes/daily)     │
│                                             │
│  2. plan_user_request()                     │
│     ├── general_chat → LLM stream           │
│     ├── needs_clarification → ask user      │
│     ├── structured_action → intent pipeline │
│     └── automation_request → workflow agent  │
│                                             │
│  3. IntentParser.parse()                    │
│     └── LLM → JSON intent + confidence      │
│                                             │
│  4. MCPRouter.route()                       │
│     ├── Security & rate limit checks        │
│     ├── Confidence gates                    │
│     ├── Dialog state merging                │
│     └── Connector dispatch                  │
│                                             │
│  5. Connector.execute()                     │
│     └── Gmail, WhatsApp, Payments, Travel…  │
│                                             │
│  6. Response synthesis & streaming          │
│     └── Buffered chunks → WebSocket         │
└─────────────────────────────────────────────┘
     │
     ▼
Client receives ai_stream frames
```

---

## Core Agent Components

### 1. Intent Parser (`orchestration/intent_parser.py`)

**Purpose:** Convert natural language into structured JSON intents.

- **Class:** `IntentParser` (singleton, thread-safe)
- **Entry point:** `parse_intent(message, user_context) → Dict`
- **LLM:** Claude 3 Sonnet at temperature 0.1 (deterministic)
- **Output format:**
  ```json
  {
    "action": "search_hotels",
    "confidence": 0.85,
    "parameters": { "location": "Nairobi", "check_in_date": "2026-12-25" },
    "missing_slots": [],
    "clarifying_question": "",
    "raw_query": "original message"
  }
  ```

**Confidence Adjustment:**
| Factor | Adjustment |
|--------|-----------|
| Missing required param | -0.20 each |
| Missing optional param | -0.05 each |
| Context can infer value | +0.10 each |
| Final range | Clamped to [0.0, 1.0] |

**Personalization (Phase 3C):** Loads user correction history to customize system prompt (e.g., "user typically travels with 4 passengers").

**Fallback:** Rule-based email regex detection when confidence < 0.45.

---

### 2. MCP Router (`orchestration/mcp_router.py`)

**Purpose:** Validate, gate, and dispatch intents to the correct connector.

- **Class:** `MCPRouter` (singleton, thread-safe)
- **Entry point:** `route_intent(intent, user_context) → Dict`

**Pipeline:**
1. **Validate** — rate limits (100/hr per user), security policy, capability gates
2. **Missing params** — asks clarification for high-risk actions with missing required params
3. **Confidence gates:**
   - `>= 0.75` → execute directly
   - `< 0.75` + high-risk → ask clarification
   - `< 0.45` → fallback/reject
4. **Dialog state merge** — reuses params from previous turn (6-hour TTL in Redis)
5. **Connector dispatch** → execute action
6. **Cache result** — Redis, 5-minute TTL

---

### 3. LLM Client (`orchestration/llm_client.py`)

**Purpose:** Unified LLM interface with dual-provider fallback and token budgets.

**Providers:**
| Role | Primary | Fallback |
|------|---------|----------|
| Planner (parsing) | Claude 3 Sonnet (Anthropic) | Llama 3.1 8B (HuggingFace) |
| Executor | Llama 3.1 8B (HuggingFace) | Claude 3 Sonnet (Anthropic) |

**Token Budget:** 50K tokens/user/hour (atomic increment in cache). Estimated at ~4 chars/token. Checked before each request.

**Caching:** SHA256 hash of (system + user + temp + max_tokens + json_mode + user_id + room_id). TTL 600s. Only caches low-temperature or JSON-mode requests.

**Streaming:** Async generators for both Claude (SSE content_block_delta) and HuggingFace (OpenAI-compatible).

---

### 4. Workflow Planner (`orchestration/workflow_planner.py`)

**Purpose:** Break multi-step natural language requests into executable step sequences.

**Process:**
1. Parse message for workflow intent (delimiters: "then", "and then", "next")
2. Break into up to 7 steps
3. Determine service & action per step using alias maps
4. LLM fills parameters for each step
5. Execute sequentially or in parallel

**Confidence Thresholds:**
| Range | Behavior |
|-------|----------|
| >= 0.85 | Auto-execute |
| 0.60 – 0.85 | Ask one clarifying question |
| 0.40 – 0.60 | Ask all missing details |
| < 0.20 | Reject, ask to rephrase |

**Idempotency:** 90-second cache per workflow hash prevents duplicate submissions.

---

### 5. Workflow Agent (`workflows/workflow_agent.py`)

**Purpose:** LLM-based conversational agent for creating multi-step workflow definitions through chat.

- Creates `WorkflowDraft` records (status: draft → awaiting_confirmation → confirmed)
- Outputs full workflow JSON definitions with steps, triggers, and policies
- Links drafts to chatroom for conversation context

---

### 6. Temporal Integration (`workflows/temporal_integration.py`)

**Purpose:** Durable, scheduled, and webhook-triggered workflow execution.

**Workflow: `DynamicUserWorkflow`**
- Accepts workflow_id, definition JSON, trigger_data
- Creates WorkflowExecution DB record
- Iterates steps: check condition → execute activity → store result in context
- Retry policy: 2s initial, 30s max, 3 attempts, 5-min timeout per step

**Trigger Types:**
| Type | Mechanism |
|------|-----------|
| Manual | REST API call |
| Webhook | External service → registered URL |
| Schedule | Temporal Schedule (CRON-based, timezone-aware) |

**Fallback:** If Temporal is unavailable, `DeferredWorkflowExecution` model queues workflows for Celery replay with exponential backoff (30s base, 10min max, 6 retries).

---

### 7. Activity Executor (`workflows/activity_executors.py`)

**Purpose:** Execute individual workflow steps by routing to the correct connector.

**Features:**
- Service/action routing to all orchestration connectors
- Parameter resolution via `{{step_id.field}}` templating
- Dependency checking (`depends_on` list)
- Condition evaluation (safe AST-based, no exec())
- Policy enforcement (withdrawal limits, phone allowlists)
- Action receipt recording for audit trail

---

## Connectors (Service Integrations)

### Communication
| Connector | File | Capabilities |
|-----------|------|-------------|
| **GmailConnector** | `gmail_connector.py` | OAuth2 email send, token refresh, HTML/text body |
| **WhatsAppConnector** | `whatsapp_connector.py` | Twilio-based messaging, media URLs, mock mode |

### Payments
| Connector | File | Capabilities |
|-----------|------|-------------|
| **ReadOnlyPaymentConnector** | `payment_connector.py` | Balance, transactions, invoice status (read-only) |
| **InvoiceConnector** | `invoice_connector.py` | Create invoices via IntaSend, multi-channel notifications |
| **IntersendPayConnector** | (via router) | Payment links, withdrawals, status checks |

### Travel
All inherit from `BaseTravelConnector` (`base_travel_connector.py`) which provides query caching, rate limiting (100/hr per provider per user), retry with exponential backoff, and parallel fetch.

| Connector | Purpose |
|-----------|---------|
| **TravelBusesConnector** | Bus ticket search |
| **TravelHotelsConnector** | Hotel/accommodation search |
| **TravelFlightsConnector** | Flight search (Amadeus API) |
| **TravelTransfersConnector** | Airport/ground transfers |
| **TravelEventsConnector** | Events and activities |
| **ItineraryConnector** | Create/view/add-to itineraries, booking |

### Utilities
| Connector | Purpose |
|-----------|---------|
| **CalendarConnector** | Calendly OAuth, fetch events, booking links |
| **SearchConnector** | Web search (10/day limit), Claude fallback |
| **WeatherConnector** | OpenWeatherMap lookups |
| **GiphyConnector** | GIF search (PG-13) |
| **CurrencyConnector** | ExchangeRate-API conversion |
| **ReminderConnector** | Scheduled reminders (DB + async) |
| **QuotaConnector** | Usage tracking per feature |

---

## Safety & Security Layers

### Security Policy (`orchestration/security_policy.py`)

**Prompt Injection Detection** — regex patterns block:
- "ignore all/previous/system instructions"
- "jailbreak", "bypass safety/filters"
- "reveal api_key/token/secret"
- SQL/database patterns, file:// URLs, localhost access

**Sensitive Actions** — actions requiring extra scrutiny:
`send_email`, `send_message`, `send_whatsapp`, `create_invoice`, `create_payment_link`, `withdraw`, `schedule_meeting`, `book_travel_item`, `create_workflow`

**Blocking Logic:**
- `should_block_action()` — blocks if prompt injection detected AND action is sensitive
- `should_refuse_sensitive_request()` — refuses requests for legal/medical/financial advice, hacking

**Parameter Sanitization:** Strips `user_id`, `room_id`, `is_admin`, `auth`, `token`, `api_key`, `secret`, `password` from parameters.

**Room Access:** Async verification with 5-minute cache per user+room.

### Risk Levels & Confirmation

| Risk Level | Actions | Confirmation |
|------------|---------|-------------|
| **High** | send_email, send_message, create_invoice, withdraw, book_travel_item | Always required |
| **Medium** | check_invoice_status, set_reminder, create_itinerary, schedule_meeting | Sometimes |
| **Low** | Searches, checks, conversions | Never |

---

## Memory & Context System

### 3-Tier Memory (`chatbot/context_manager.py`)

| Tier | Model | Purpose | Retention |
|------|-------|---------|-----------|
| **Hot** | `RoomContext` | Summary, topics, entities, facts, preferences, episodes | Rolling |
| **Notes** | `RoomNote` | Decisions, action items, insights, references | Persistent |
| **Cold** | `DailySummary` | Daily compressed summaries, sentiment, stats | Permanent |

**Context Prompt Assembly:**
1. Room summary & active topics
2. Ranked memory facts (scored: recency 0.3 + confidence 0.5 + semantic 0.2)
3. Recent notes (decisions, action items)
4. Cross-room high-priority notes
5. Recent document text (up to 3 docs, 2000 chars each)

### Dialog State (`orchestration/mcp_router.py`)
- Redis-backed, 6-hour TTL per user+room
- Stores: last action, parameters, status
- Enables multi-turn: "same dates", partial re-queries, follow-ups
- Merges compatible previous parameters (same action or travel context)

### Conversation Memory (`chatbot/models.py → AIConversation`)
- Sliding window of last 20 exchanges
- Only last 3 used per LLM request
- Stored per user+room

### Entity Tracking (`orchestration/memory_state.py`)
- Redis-backed, 24-hour TTL
- Tracks: origin, destination, city, email, phone, amount, currency, dates, IDs
- Summary format for LLM injection: `"Recent actions: search_flights. Known entities: origin=Nairobi; ..."`

---

## Telemetry & Observability (`orchestration/telemetry.py`)

- JSONL event log at `telemetry/orchestration.jsonl`
- Thread-safe, non-blocking writes
- **Correction signals** (Phase 3C): parameter, result_selection, preference, workflow, confirmation
- Pattern loading for prompt personalization

---

## User Preferences (`orchestration/user_preferences.py`)

| Setting | Options | Default |
|---------|---------|---------|
| Tone | friendly, formal, direct, warm, casual | friendly |
| Verbosity | short, balanced, detailed | balanced |
| Directness | direct, neutral, polite | neutral |
| Date order | DMY, MDY, YMD | DMY (MDY for US) |
| Time format | 24h, 12h | 24h (12h for US) |
| Currency | USD, KES, EUR, etc. | Inferred from location |
| Capability mode | custom, conserve, balanced, max | balanced |

---

## Action Catalog (`orchestration/action_catalog.py`)

Single source of truth for all 40+ supported actions. Each entry includes:
- `action` name and `aliases`
- `service` target
- `required_params` and `optional_params`
- `risk_level` (high/medium/low)
- `confirmation_policy` (always/never)
- `capability_gate` (e.g., allow_email, allow_travel)

**Supported Action Categories:**
- **Travel** (9): search_buses, search_flights, search_hotels, search_transfers, search_events, create_itinerary, view_itinerary, add_to_itinerary, book_travel_item
- **Communication** (3): send_email, send_whatsapp, send_message
- **Payments** (6): create_invoice, create_payment_link, check_payments, check_balance, list_transactions, withdraw
- **Calendar** (2): schedule_meeting, check_availability
- **Utilities** (5): search_info, get_weather, search_gif, convert_currency, set_reminder
- **System** (1): check_quotas

---

## Streaming Architecture

**Buffered WebSocket Streaming:**
```
LLM chunks → broadcast_chunk() buffer → WebSocket frame
```
- Accumulates tokens until buffer > 20 chars OR 200ms elapsed OR is_final
- Filters leading whitespace on first token
- Correlation IDs link chunks to requests

**WebSocket Frame Types:**
| Command | Direction | Purpose |
|---------|-----------|---------|
| `new_message` | Out | Broadcast user message |
| `ai_stream` | Out | Streaming AI chunk |
| `orchestration_step` | Out | Progress (planning → validating → executing → done) |
| `ai_message_saved` | Out | Complete AI response saved |
| `typing` | In/Out | Typing indicator |
| `fetch_messages` | In | Load older messages (cursor-based pagination) |
| `get_quotas` | In | User quota stats |

---

## Rate Limits & Quotas

| Resource | Limit | Window |
|----------|-------|--------|
| Messages | 30/min per user | Rolling |
| MCP requests | 100/hr per user | Rolling |
| Web searches | 10/day per user | Daily |
| LLM tokens | 50K/hr per user | Hourly |
| Travel API | 100/hr per provider per user | Hourly |
| Document uploads | 10 (free), 100 (pro), 10K (agency) | 10-hour rolling |

---

## Encryption

- **Algorithm:** AES-256 GCM
- **Nonce:** 12-byte CSPRNG per message
- **Storage:** JSON `{"data": "<encrypted_b64>", "nonce": "<nonce_b64>"}`
- **Key rotation:** Every 100 hours OR 1000 messages
- **Key storage:** Envelope encryption via `TokenEncryption`

---

## Moderation Pipeline

```
Message → Redis buffer → Batch (10 msgs) → Celery task → HF toxic-bert → UserModerationStatus → Auto-mute at 3+ flags
```

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Core | Python 3.11, Django 5.0 (ASGI) |
| Real-time | Django Channels, Redis (WebSockets) |
| AI Orchestration | MCP Router (Model Context Protocol) |
| LLM Providers | Anthropic Claude, HuggingFace (Llama/Mistral) |
| Database | PostgreSQL 16 |
| Cache/State | Redis |
| Async Tasks | Celery & Celery Beat |
| Durable Workflows | Temporal |
| Payments | IntaSend (M-Pesa, Cards) |
| Travel APIs | Amadeus, Buupass, Booking.com, Duffel, Eventbrite |
| Email | Gmail API (OAuth2), Mailgun |
| Messaging | Twilio (WhatsApp) |
| Calendar | Calendly (OAuth2) |
| Moderation | HuggingFace (toxic-bert) |
| Frontend | HTML5/Bootstrap (Django-served) |

---

## Key File Reference

| File | Purpose |
|------|---------|
| `Backend/chatbot/consumers.py` | WebSocket handler, main message flow (~2200 lines) |
| `Backend/chatbot/context_manager.py` | 3-tier memory system |
| `Backend/chatbot/models.py` | 12 DB models (chat, context, memory) |
| `Backend/chatbot/tasks.py` | Celery tasks (moderation, voice, reminders) |
| `Backend/orchestration/intent_parser.py` | NL → JSON intent parsing |
| `Backend/orchestration/mcp_router.py` | Intent validation, routing, execution |
| `Backend/orchestration/llm_client.py` | Dual-provider LLM with token budgets |
| `Backend/orchestration/workflow_planner.py` | Multi-step workflow planning |
| `Backend/orchestration/security_policy.py` | Injection detection, access control |
| `Backend/orchestration/memory_state.py` | Entity tracking across turns |
| `Backend/orchestration/action_catalog.py` | Single source of truth for all actions |
| `Backend/orchestration/telemetry.py` | Event logging, correction signals |
| `Backend/orchestration/user_preferences.py` | Localization, style preferences |
| `Backend/orchestration/contracts.py` | Standardized response format |
| `Backend/orchestration/models.py` | ActionReceipt audit model |
| `Backend/orchestration/connectors/` | All service connectors |
| `Backend/workflows/temporal_integration.py` | Temporal workflow definitions |
| `Backend/workflows/activity_executors.py` | Step execution with connector routing |
| `Backend/workflows/workflow_agent.py` | Conversational workflow creation |
| `Backend/workflows/models.py` | Workflow, trigger, execution models |
| `Backend/workflows/tasks.py` | Celery deferred workflow replay |
| `Backend/payments/models.py` | Double-entry ledger, invoices |
| `Backend/payments/services.py` | Ledger, wallet, invoice services |
| `Backend/travel/models.py` | Itinerary, items, bookings |
| `Backend/users/models.py` | Profiles, wallets, Calendly OAuth |
