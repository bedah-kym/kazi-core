
# Mathia.OS - Agents & Orchestration Architecture

## Overview

Mathia.OS is an AI-powered operating system built on a **fully agentic ReAct loop** — the LLM autonomously reasons, selects tools, observes results, and iterates until the task is complete. The system handles requests across communication, finance, travel, and productivity domains with dynamic multi-tool chaining, self-correction, and parallel execution.

A legacy "classic" pipeline (Parse → Route → Execute) is retained as a fallback, controlled by the `AGENT_LOOP_ENABLED` feature flag.

---

## Delivery Sweep Status (Updated 2026-03-20)

### Phase 1 — Profile & Settings Reliability (Completed)
- Fixed profile/bio image updates by using unique avatar filenames and accepting `image/jpg` in upload handling.
- Fixed settings profile form failure path where invalid submissions could hit an undefined context variable.
- Files: `Backend/users/avatar_views.py`, `Backend/users/auth_views.py`, `Backend/users/feature_views.py`.

### Phase 2 — Memory & Invite Consistency (Completed)
- Fixed agent memory summary propagation so `memory_summary` is passed into `run_agent_loop`.
- Fixed invite sending flow to avoid persisting invites when outbound email fails.
- Files: `Backend/chatbot/consumers.py`, `Backend/users/views.py`.

### Phase 3 — Agent Runtime Hardening (Completed)
- Added capability + approval override propagation in preferences used by agent orchestration.
- Added HuggingFace fallback paths in LLM client for both sync and streaming calls.
- Blocked sub-agent execution of tools that require explicit confirmation (must be done in main loop).
- Files: `Backend/orchestration/user_preferences.py`, `Backend/orchestration/llm_client.py`, `Backend/orchestration/agent_loop.py`.

### Phase 4 — Security & Workflow Hardening (Completed)
- Hardened prompt-injection detection and recursive parameter sanitization.
- Corrected tool safety check wiring and added parameter-level high-risk injection guard.
- Enforced workflow action/service validation, capability gates, trigger/room access checks, and Temporal failure state updates.
- Added connector-level robustness fixes and regression tests for security policy behavior.
- Files: `Backend/orchestration/security_policy.py`, `Backend/orchestration/tool_executor.py`, `Backend/workflows/activity_executors.py`, `Backend/workflows/views.py`, `Backend/workflows/temporal_integration.py`, `Backend/orchestration/tests.py`, `Backend/orchestration/connectors/invoice_connector.py`, `Backend/orchestration/connectors/intersend_connector.py`, `Backend/chatbot/consumers.py`, `Backend/orchestration/agent_loop.py`.

### Phase 5 — Unified Notification Service (Completed)
- Built new `notifications` Django app with unified `Notification` model covering payments, reminders, messages, and system events.
- Added `NotificationService` dispatch layer: checks per-event/per-channel preference matrix → creates DB row → pushes via WebSocket → queues Celery tasks for email/WhatsApp.
- Added per-user `NotificationConsumer` WebSocket (`ws/notifications/`) for real-time delivery, separate from ChatConsumer.
- Added granular notification preferences (`notify_matrix`) extending existing `notification_preferences` JSONField — per-event-type x per-channel (in_app, email, WhatsApp).
- Integrated at 3 payment events, reminder delivery, and 3 chat message broadcast points (text/file/voice) with offline user detection and 5-min debounce.
- Added REST API: list (paginated/filterable), counts, mark-read, mark-all-read, dismiss.
- Upgraded frontend: WebSocket-first notification delivery with poll fallback, scrollable notification center dropdown, mark-all-read button.
- Added notification preferences UI tab in settings with per-event grouped checkboxes.
- Files: `Backend/notifications/` (new app), `Backend/Backend/settings.py`, `Backend/Backend/asgi.py`, `Backend/Backend/urls.py`, `Backend/chatbot/consumers.py`, `Backend/chatbot/tasks.py`, `Backend/chatbot/static/js/notifications.js`, `Backend/chatbot/templates/chatbot/chatbase.html`, `Backend/orchestration/user_preferences.py`, `Backend/payments/services.py`, `Backend/users/feature_views.py`, `Backend/users/templates/users/settings.html`.

### Phase 5b — Test Suite Fixes (Completed)
- Fixed injection regex in `agent_loop.py` and `security_policy.py` — multi-qualifier strings like "ignore all previous instructions" were not matched.
- Aligned 3 scenario test assertions with actual streaming event kinds (`text_delta` instead of `text`).
- All 61 critical tests passing (orchestration, notifications, agentic unit + scenario tests).

### Next
- Phase 6 planned: data migration to backfill `PaymentNotification` → `Notification`, remove dual-write once stable.

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
┌──────────────────────────────────────────────────────┐
│              AGENTIC ORCHESTRATION                   │
│                                                      │
│  1. ContextManager.get_context_prompt()              │
│     └── 3-tier memory (hot/notes/daily)              │
│                                                      │
│  2. Agent Loop (agent_loop.py)                       │
│     ┌──────────────────────────────────────┐         │
│     │  messages = [system, history, user]  │         │
│     │  tools = get_tool_definitions(user)  │         │
│     │                                      │         │
│     │  while iterations < MAX (10):        │         │
│     │    response = LLM(messages, tools)   │         │
│     │                                      │         │
│     │    if end_turn → stream final text   │         │
│     │                                      │         │
│     │    if tool_use:                       │         │
│     │      ├── safety gate (confirm?)      │         │
│     │      ├── execute_tool()              │         │
│     │      ├── append result to messages   │         │
│     │      └── stream progress to user     │         │
│     │                                      │         │
│     │    → loop back (LLM sees results)    │         │
│     └──────────────────────────────────────┘         │
│                                                      │
│  3. Tool Executor (tool_executor.py)                 │
│     ├── Resolves tool → connector                    │
│     ├── Security policy + capability gates           │
│     ├── Confirmation gates (high-risk)               │
│     ├── Dedup (cached results for same input)        │
│     └── Connector.execute()                          │
│                                                      │
│  4. Streaming (callbacks → WebSocket)                │
│     ├── Text chunks → broadcast_chunk()              │
│     ├── Thinking events → emit_progress()            │
│     ├── Tool call events → emit_progress()           │
│     └── Final response → broadcast_chunk(final)      │
│                                                      │
│  FALLBACK (conversation_mode == "classic"):          │
│     plan_user_request() → IntentParser → MCPRouter   │
└──────────────────────────────────────────────────────┘
     │
     ▼
Client receives ai_stream frames

┌──────────────────────────────────────────────────────┐
│           NOTIFICATION SERVICE                        │
│                                                      │
│  NotificationService.notify(user, event, title, ...) │
│     │                                                │
│     ├── Load notify_matrix from user preferences     │
│     ├── in_app? → Notification.objects.create()      │
│     │             → push to ws/notifications/ group  │
│     ├── email?  → deliver_notification_email.delay() │
│     └── whatsapp? → deliver_notification_whatsapp()  │
│                                                      │
│  Integration points:                                 │
│     ├── payments/services.py (deposit/withdraw/inv)  │
│     ├── chatbot/tasks.py (reminder delivery)         │
│     └── chatbot/consumers.py (offline msg notify)    │
│                                                      │
│  NotificationConsumer (ws/notifications/)             │
│     ├── Per-user channel group                       │
│     ├── Real-time push on notification.push event    │
│     └── Client actions: mark_read, dismiss           │
└──────────────────────────────────────────────────────┘
```

---

## Core Agent Components

### 1. Agent Loop (`orchestration/agent_loop.py`) — PRIMARY

**Purpose:** ReAct-style autonomous agent loop — the core orchestration engine. Replaces the old parse→route→done pipeline.

- **Entry point:** `run_agent_loop(message, context, on_chunk, on_tool_call) → AsyncGenerator[AgentEvent]`
- **Architecture:** Think → Act → Observe → Think (iterative)
- **LLM:** Claude Sonnet 4.6 (complex/multi-turn) or Claude Haiku 4.5 (simple first-turn, ≤25 words)

**Loop Mechanics:**
1. Build messages array: system prompt + conversation history + user message
2. Attach tool definitions filtered by user capability gates
3. Call LLM with tools → inspect stop_reason
4. `end_turn` → stream final text, done
5. `tool_use` → safety gate → execute tool → append result → loop back
6. LLM sees results and decides next action autonomously

**Guardrails:**
| Limit | Value |
|-------|-------|
| Max iterations | 10 |
| Max tool calls | 15 per loop |
| Wall clock timeout | 120 seconds |
| Token budget | 50K per loop (warning at 80%) |
| Tool timeout | 30 seconds per tool |
| Max retries per tool | 2 |
| Dedup | Identical tool+input returns cached result |

**AgentEvent Types:** `thinking`, `tool_call`, `tool_result`, `text_chunk`, `confirmation_needed`, `error`, `done`

**Confirmation Pause/Resume:**
- High-risk tools pause the loop, store state in Redis (`orchestration:agent_state:{room_id}:{user_id}`, 10-min TTL)
- User confirms → loop resumes from stored state
- User cancels → loop terminates gracefully

---

### 2. Tool Schema Layer (`orchestration/tool_schemas.py`)

**Purpose:** Convert ACTION_CATALOG entries into Claude-compatible tool definitions.

- **Entry point:** `get_tool_definitions(user_id) → List[Dict]`
- Generates schemas dynamically from ACTION_CATALOG (single source of truth)
- Filters tools by user capability gates (allow_email, allow_travel, etc.)
- Rich `description` fields on every parameter so the LLM knows what to pass

---

### 3. Tool Executor (`orchestration/tool_executor.py`)

**Purpose:** Execute tool calls with safety checks, routing, and audit.

- **Entry point:** `execute_tool(tool_name, tool_input, context) → Dict`
- Resolves tool_name → connector (reuses existing connector map)
- Enforces: security policy, capability gates, approval policies, rate limits
- User-configurable approval overrides via `approval_overrides` in preferences
- Sanitizes tool results: caps at 8K chars, strips injection patterns
- Records action receipts for audit trail
- Returns standardized result dict

---

### 4. Agent System Prompt (`orchestration/agent_prompts.py`)

**Purpose:** Build the system prompt that instructs the agent LLM.

**Injected Context:**
- Agent identity and capabilities
- Available tools and their descriptions
- User preferences (tone, verbosity, locale)
- Memory context (entities, recent actions, notes)
- Conversation history
- Uploaded document text
- Instructions for: observation, error recovery, multi-step chaining, confirmation behavior

**Prompt Caching:** `cache_control: {"type": "ephemeral"}` on system prompt block for ~90% cost savings on cached tokens.

---

### 5. Meta-Tools (Advanced Capabilities)

**Sub-Agent Delegation (`delegate_task`):**
- Spawns a focused sub-loop with scoped tool set
- Limits: 5 iterations, 8 tool calls
- Shares parent's token budget
- Use case: complex tasks like full trip planning (flights sub-loop + hotels sub-loop + itinerary)

**Temporal Handoff (`handoff_to_workflow`):**
- Creates a Workflow + starts Temporal execution for long-running async tasks
- Use case: "Monitor this payment and notify me when it completes"
- Bridges synchronous agent loop with durable async workflows

**Claude Web Search:**
- Native `web_search_20250305` server-side tool (replaces SearchConnector)
- Rate-limited to 10/day per user

---

### 6. LLM Client (`orchestration/llm_client.py`)

**Purpose:** Unified LLM interface with native tool_use support, dual-provider fallback, and token budgets.

**Providers & Model Routing:**
| Complexity | Model | When |
|------------|-------|------|
| Simple (≤25 words, single tool) | Claude Haiku 4.5 | First-turn, clear intent |
| Complex / multi-turn | Claude Sonnet 4.6 | Multi-step, ambiguous, follow-ups |
| Fallback | Llama 3.1 8B (HuggingFace) | When Anthropic unavailable |

**Tool Use Support:**
- `create_message(messages, tools)` — synchronous with tool_use handling
- `stream_message(messages, tools)` — async streaming with tool_use blocks

**Token Budget:** 50K tokens/user/hour (atomic increment in cache). Per-loop tracking with hard cap and 80% warning.

**Prompt Caching:** System prompt + tool definitions cached via `use_prompt_cache=True`.

**Caching:** SHA256 hash of (system + user + temp + max_tokens + json_mode + user_id + room_id). TTL 600s.

---

### 7. Intent Parser (`orchestration/intent_parser.py`) — FALLBACK ONLY

**Status:** Retained for "classic" mode fallback. Not used in agentic mode.

**Purpose:** Convert natural language into structured JSON intents (legacy pipeline).

- **Entry point:** `parse_intent(message, user_context) → Dict`
- Used when `conversation_mode == "classic"` or agent loop fails

---

### 8. MCP Router (`orchestration/mcp_router.py`) — SIMPLIFIED

**Status:** Routing logic replaced by tool_executor. Safety/state logic retained.

**Retained features:** Rate limits, security policy, dialog state cache (6-hour TTL), connector dispatch.

---

### 12. Contacts System (`orchestration/contact_tools.py` + `chatbot/contact_api.py`)

**Purpose:** User contact management with agent tool integration and REST API.

**Agent Tools (internal, no connector needed):**
| Tool | Risk | Description |
|------|------|-------------|
| `lookup_contact` | Low | Search contacts by name; falls back to workspace members if none found |
| `save_contact` | Low | Create contact with dedup by email/phone; source: `ai_extracted` |

**REST API:**
| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/contacts/?room_id=X` | GET | List contacts (global + room-scoped + linked rooms) |
| `/api/contacts/` | POST | Create contact (manual source) |
| `/api/contacts/<id>/` | PATCH | Update contact fields |
| `/api/contacts/<id>/` | DELETE | Delete contact (ownership verified) |
| `/api/contacts/search/?q=&room_id=` | GET | Autocomplete search (name/email/phone, max 10) |

**Scoping:** Contacts can be global (`room=null`) or room-scoped. Room-scoped contacts are visible in linked rooms. Global/room toggle available at creation time.

**Deduplication:** `save_contact` checks existing contacts by email then phone before creating.

---

### 13. Room Linking (`chatbot/linked_rooms_api.py`)

**Purpose:** Bidirectional room linking for shared context across conversations.

**Model:** `RoomContext.related_rooms` — ManyToMany self-referential field.

**REST API:**
| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/rooms/<room_id>/linked/` | GET | List linked rooms + linkable rooms |
| `/api/rooms/<room_id>/linked/` | POST | Link a room (body: `{target_room_id}`) |
| `/api/rooms/<room_id>/linked/<target_id>/` | DELETE | Unlink a room |

**Behavior:**
- Links are **bidirectional** — linking A→B also links B→A
- Notes from linked rooms appear under "LINKED ROOM CONTEXT" in agent prompts
- `RoomNote.is_private` field excludes sensitive notes from linked room sharing
- Contacts from linked rooms are accessible via contact search
- User must be participant in both rooms to create a link

---

### 14. Notification Service (`notifications/`)

**Purpose:** Unified notification dispatch across all event types with per-user real-time WebSocket delivery, granular preferences, and multi-channel output.

**Model:** `Notification` — unified record for payments, reminders, messages, system events.
- `event_type`: `payment.deposit`, `payment.withdrawal`, `payment.invoice`, `payment.error`, `reminder.due`, `message.unread`, `message.mention`, `system.info`, `system.warning`
- `severity`: info, success, warning, error
- Typed nullable FKs: `related_invoice`, `related_journal`, `related_reminder`, `related_room`, `related_message`
- State: `is_read`, `read_at`, `is_dismissed`
- Delivery tracking: `delivered_ws`, `delivered_email`, `delivered_whatsapp`

**Service:** `NotificationService.notify(user, event_type, title, body, severity, related_*)`
1. Load user's `notify_matrix` for the event type
2. If `in_app` → create DB row → push via WebSocket group `notifications_{user.id}`
3. If `email` → queue `deliver_notification_email` Celery task (Mailgun/Gmail)
4. If `whatsapp` → queue `deliver_notification_whatsapp` Celery task (Twilio)

**WebSocket Consumer:** `NotificationConsumer` at `ws/notifications/`
- Per-user channel group, separate from ChatConsumer
- On connect: send initial `unread_count`
- Client actions: `mark_read`, `mark_all_read`, `dismiss`
- Multiple tabs = same group = all receive events

**Preferences:** `notify_matrix` inside existing `UserProfile.notification_preferences` JSONField:
```json
{
    "notify_matrix": {
        "payment.deposit": {"in_app": true, "email": true, "whatsapp": false},
        "message.unread": {"in_app": true, "email": false, "whatsapp": false},
        ...
    }
}
```

**REST API:**
| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/notifications/api/` | GET | List (paginated, filterable by event_type, unread_only) |
| `/notifications/api/counts/` | GET | Unified + legacy counts |
| `/notifications/api/<id>/read/` | POST | Mark single read |
| `/notifications/api/read-all/` | POST | Mark all read |
| `/notifications/api/<id>/dismiss/` | POST | Dismiss |

**Integration Points:**
- `payments/services.py` — deposit (line 270), withdrawal (line 310), invoice paid (line 434)
- `chatbot/tasks.py` — reminder delivery after status='sent'
- `chatbot/consumers.py` — text/file/voice message broadcast → `notify_room_message()` for offline users (5-min debounce via Redis)

**Frontend:** `notifications.js` — WebSocket-first with 30s poll fallback, scrollable notification center dropdown, sound alerts, mark-read/dismiss via WebSocket.

---

### RETIRED / FALLBACK Components

| Component | Status | Notes |
|-----------|--------|-------|
| `workflow_planner.py` | **Fallback** | Active when `AGENT_LOOP_ENABLED=False`; ~1740 lines with confidence gates |
| `data_synthesizer.py` | Retired | Agent loop — LLM synthesizes responses natively |
| `manager_verifier.py` | Retired | Agent loop — self-verification via observation |

---

### 9. Workflow Agent (`workflows/workflow_agent.py`)

**Purpose:** LLM-based conversational agent for creating multi-step workflow definitions through chat.

- Creates `WorkflowDraft` records (status: draft → awaiting_confirmation → confirmed)
- Outputs full workflow JSON definitions with steps, triggers, and policies
- Links drafts to chatroom for conversation context

---

### 10. Temporal Integration (`workflows/temporal_integration.py`)

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

### 11. Activity Executor (`workflows/activity_executors.py`)

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
| Notification matrix | per-event-type x per-channel (in_app, email, whatsapp) | See defaults in `_DEFAULT_NOTIFY_MATRIX` |

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
- **Contacts** (2): lookup_contact, save_contact
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
| AI Orchestration | Agentic ReAct Loop (agent_loop.py) with tool_use |
| LLM Providers | Anthropic Claude (Sonnet 4.6 / Haiku 4.5), HuggingFace (Llama 3.1 fallback) |
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
| **Agentic Core** | |
| `Backend/orchestration/agent_loop.py` | ReAct agent loop — primary orchestration engine |
| `Backend/orchestration/agent_prompts.py` | Agent system prompt builder with context injection |
| `Backend/orchestration/tool_schemas.py` | ACTION_CATALOG → Claude tool definitions |
| `Backend/orchestration/tool_executor.py` | Tool execution with safety, dedup, audit |
| `Backend/orchestration/llm_client.py` | LLM interface with tool_use, model routing, prompt caching |
| **Consumer & Streaming** | |
| `Backend/chatbot/consumers.py` | WebSocket handler, agent loop integration (~2200 lines) |
| `Backend/chatbot/context_manager.py` | 3-tier memory system |
| `Backend/chatbot/models.py` | 13 DB models (chat, context, memory, contacts) |
| `Backend/chatbot/tasks.py` | Celery tasks (moderation, voice, reminders) |
| **Orchestration (Legacy/Support)** | |
| `Backend/orchestration/intent_parser.py` | NL → JSON intent parsing (classic mode fallback) |
| `Backend/orchestration/mcp_router.py` | Intent routing (classic mode fallback) |
| `Backend/orchestration/security_policy.py` | Injection detection, access control |
| `Backend/orchestration/memory_state.py` | Entity tracking across turns |
| `Backend/orchestration/action_catalog.py` | Single source of truth for all 40+ actions |
| `Backend/orchestration/telemetry.py` | Event logging, loop transcripts, correction signals |
| `Backend/orchestration/user_preferences.py` | Localization, style preferences |
| `Backend/orchestration/contracts.py` | Standardized response format |
| `Backend/orchestration/models.py` | ActionReceipt audit model |
| `Backend/orchestration/contact_tools.py` | Contact agent tools (lookup, save) with dedup |
| `Backend/orchestration/connectors/` | All service connectors (tools the agent calls) |
| **Contacts & Room Linking** | |
| `Backend/chatbot/contact_api.py` | REST CRUD + search for contacts |
| `Backend/chatbot/linked_rooms_api.py` | Bidirectional room linking API |
| **Workflows** | |
| `Backend/workflows/temporal_integration.py` | Temporal workflow definitions + agent handoff |
| `Backend/workflows/activity_executors.py` | Step execution with connector routing |
| `Backend/workflows/workflow_agent.py` | Conversational workflow creation |
| `Backend/workflows/models.py` | Workflow, trigger, execution models |
| `Backend/workflows/tasks.py` | Celery deferred workflow replay |
| **Notifications** | |
| `Backend/notifications/models.py` | Unified Notification model (all event types) |
| `Backend/notifications/services.py` | NotificationService dispatch (preferences → DB → WS → email/WhatsApp) |
| `Backend/notifications/consumers.py` | Per-user NotificationConsumer WebSocket |
| `Backend/notifications/tasks.py` | Celery tasks for email/WhatsApp delivery |
| `Backend/notifications/views.py` | REST API (list, counts, mark-read, dismiss) |
| `Backend/notifications/urls.py` | URL routing under `/notifications/` |
| `Backend/notifications/routing.py` | WebSocket URL patterns |
| **Domain** | |
| `Backend/payments/models.py` | Double-entry ledger, invoices |
| `Backend/payments/services.py` | Ledger, wallet, invoice services + notification dispatch |
| `Backend/travel/models.py` | Itinerary, items, bookings |
| `Backend/users/models.py` | Profiles, wallets, Calendly OAuth |
| **Tests** | |
| `Backend/orchestration/tests.py` | 7 tests: action catalog, security policy, parameter sanitization |
| `Backend/orchestration/test_agentic.py` | 39 unit tests for agentic components (tool schemas, executor, prompts, model selection, token tracking, web search) |
| `Backend/orchestration/test_agentic_scenarios.py` | 11 end-to-end scenario tests (tool chains, error recovery, confirmation, injection protection) |
| `Backend/notifications/tests.py` | 6 tests: notify_matrix normalization + NotificationService integration |
