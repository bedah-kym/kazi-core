# Mathia Agentic Transformation Plan

## Goal
Transform Mathia from an **intent-classification + routing** system into a **fully agentic AI** comparable to Claude — where the LLM autonomously reasons, selects tools, observes results, and iterates until the task is complete.

## Current State (Baseline)
- LLM classifies intent once → deterministic router executes → returns result
- Multi-step workflows are pre-planned before execution (no mid-flight adaptation)
- 26 actions across 12 services, all with connectors already built
- Safety layers, memory, streaming, and Temporal workflows all functional
- ~40-50% of the way to agentic behavior

## Target State (100%)
- LLM is in an autonomous **think → act → observe → think** loop
- Dynamically chains any combination of tools based on intermediate results
- Self-corrects on errors, retries with different strategies
- Composes multi-tool sequences on the fly (no pre-planning required)
- Maintains conversational context across the entire loop
- All existing safety/confirmation gates preserved

---

## Phase 1: Tool Schema Layer (Foundation)
**Goal:** Convert ACTION_CATALOG into Claude-compatible tool definitions so the LLM can natively call tools.

### 1.1 Build Tool Schema Generator
- **File:** `Backend/orchestration/tool_schemas.py` (new)
- Convert each entry in `ACTION_CATALOG` into Claude function-calling format:
  ```python
  {
      "name": "search_flights",
      "description": "Search for flights between two cities",
      "input_schema": {
          "type": "object",
          "properties": {
              "origin": {"type": "string", "description": "Departure city or airport code"},
              "destination": {"type": "string", "description": "Arrival city or airport code"},
              "departure_date": {"type": "string", "description": "Date in YYYY-MM-DD format"},
          },
          "required": ["origin", "destination", "departure_date"]
      }
  }
  ```
- Add rich `description` fields to every param in ACTION_CATALOG (the LLM needs these to know what to pass)
- Generate schemas dynamically from ACTION_CATALOG so there's still a single source of truth
- Filter tools based on user's capability gates (allow_email, allow_travel, etc.)

### 1.2 Enrich ACTION_CATALOG Descriptions
- **File:** `Backend/orchestration/action_catalog.py` (modify)
- Add detailed `description` to every param (currently only has type and required)
- Add `return_schema` to each action describing what the tool returns (so the LLM knows what to expect)
- Example:
  ```python
  "params": {
      "origin": {"type": "string", "required": True, "description": "Departure city name or 3-letter IATA airport code"},
      "destination": {"type": "string", "required": True, "description": "Arrival city name or 3-letter IATA airport code"},
      "departure_date": {"type": "string", "required": True, "description": "Departure date in YYYY-MM-DD format"},
      "return_date": {"type": "string", "required": False, "description": "Return date in YYYY-MM-DD format for round trips"},
      "passengers": {"type": "integer", "required": False, "description": "Number of passengers, defaults to 1"},
  },
  "return_description": "Returns a list of flight options with airline, times, price_ksh, stops, and booking_url",
  ```

### 1.3 Build Tool Executor Bridge
- **File:** `Backend/orchestration/tool_executor.py` (new)
- Single async function: `execute_tool(tool_name, tool_input, context) -> dict`
- Resolves tool_name to connector (reuses existing MCPRouter connector map)
- Applies safety checks (security_policy.should_block_action, capability gates)
- Applies confirmation gates (high-risk actions still require user confirmation)
- Returns standardized result dict
- This replaces the need for intent_parser + mcp_router for tool calls

### Deliverables
- [ ] `tool_schemas.py` with `get_tool_definitions(user_id) -> List[Dict]`
- [ ] Enriched ACTION_CATALOG with param descriptions and return descriptions
- [ ] `tool_executor.py` with `execute_tool(name, input, context) -> Dict`
- [ ] Unit tests for schema generation and tool execution

---

## Phase 2: Agent Loop Core (The Big Shift)
**Goal:** Build the ReAct-style agent loop that replaces the current parse→route→done pipeline.

### 2.1 Build Agent Loop Engine
- **File:** `Backend/orchestration/agent_loop.py` (new)
- Core function: `run_agent_loop(message, context, on_chunk, on_tool_call) -> str`
- Architecture:
  ```
  messages = [system_prompt, ...history, user_message]
  tools = get_tool_definitions(user_id)

  while iterations < MAX_ITERATIONS:
      response = await llm.create_message(messages, tools)

      if response.stop_reason == "end_turn":
          # LLM is done — stream final text to user
          yield final_text
          break

      if response.stop_reason == "tool_use":
          for tool_call in response.tool_calls:
              # Safety gate
              if requires_confirmation(tool_call.name) and not confirmed:
                  yield confirmation_prompt
                  pause_for_user_confirmation()
                  continue

              # Execute tool
              result = await execute_tool(tool_call.name, tool_call.input, context)

              # Append tool result to messages
              messages.append(tool_result_message(tool_call.id, result))

              # Stream progress to user
              yield progress_update(tool_call.name, result)

          # Loop back — LLM sees tool results and decides next action
  ```

### 2.2 Agent System Prompt
- **File:** `Backend/orchestration/agent_prompts.py` (new)
- Build a system prompt that tells the LLM:
  - You are Mathia, an AI assistant with access to tools
  - You can call tools to take actions on behalf of the user
  - Always explain what you're doing and why
  - For high-risk actions (payments, emails), explain and confirm before executing
  - Use search results to inform next steps (e.g., pick cheapest flight, then email it)
  - If a tool returns an error, try a different approach or ask the user
  - Never fabricate data — always use tool results
- Inject user preferences (tone, verbosity, locale) into prompt
- Inject memory context (entities, recent actions, notes) into prompt
- Inject conversation history

### 2.3 Iteration Limits & Cost Controls
- MAX_ITERATIONS = 10 (prevent infinite loops)
- MAX_TOOL_CALLS_PER_TURN = 15
- Token budget enforcement (reuse existing llm_client token tracking)
- Timeout: 120 seconds total per agent loop invocation
- If limit reached, gracefully tell user what was accomplished and what remains

### 2.4 Confirmation Pause/Resume
- When a high-risk tool is encountered mid-loop:
  1. Pause the loop
  2. Store loop state (messages, pending tool call) in Redis
  3. Stream confirmation prompt to user
  4. When user confirms, resume loop from stored state
  5. Execute the tool and continue
- **Key:** `orchestration:agent_state:{room_id}:{user_id}` in Redis with 10-min TTL

### Deliverables
- [ ] `agent_loop.py` with `run_agent_loop()` async generator
- [ ] `agent_prompts.py` with system prompt builder
- [ ] Confirmation pause/resume via Redis state
- [ ] Iteration and cost limits
- [ ] Unit tests for loop mechanics (mock LLM + mock tools)

---

## Phase 3: Consumer Integration (Wire It Up)
**Goal:** Replace the current orchestration path in `consumers.py` with the agent loop.

### 3.1 Replace Orchestration Path in Consumer
- **File:** `Backend/chatbot/consumers.py` (modify)
- Current flow (lines ~827-1500):
  ```
  parse_intent() → plan_user_request() → _execute_intent() → synthesize_response()
  ```
- New flow:
  ```
  agent_loop.run_agent_loop(ai_query, context, on_chunk=broadcast_chunk)
  ```
- The agent loop handles everything: intent understanding, tool selection, execution, error recovery, and response synthesis — all in one unified flow
- Keep `broadcast_chunk()` and `emit_progress()` — pass them as callbacks to the agent loop

### 3.2 Streaming Integration
- Agent loop yields chunks as they come:
  - **Text chunks** from LLM reasoning → `broadcast_chunk()`
  - **Tool call events** → `emit_progress("executing", "started", "Searching flights...")`
  - **Tool result summaries** → `emit_progress("executing", "completed", "Found 5 flights")`
  - **Final response** → `broadcast_chunk(final_text, is_final=True)`
- Frontend receives same WebSocket frame types (no frontend changes needed)

### 3.3 Backward Compatibility
- Keep `plan_user_request()` path as fallback for conversation_mode == "classic"
- Add feature flag: `AGENT_LOOP_ENABLED` in settings (default True)
- If agent loop fails/times out, fall back to current pipeline
- Keep existing Temporal workflow path for scheduled/triggered workflows (those don't need the loop)

### 3.4 Confirmation Flow Rewrite
- Current: `pending_key` in cache with `looks_like_confirmation()`
- New: Agent loop pauses, stores state, resumes on confirmation
- Same UX for the user — they still type "yes" or "confirm"
- But now the agent can continue executing more tools after confirmation

### Deliverables
- [ ] Modified `consumers.py` with agent loop integration
- [ ] Feature flag for gradual rollout
- [ ] Backward-compatible fallback to current pipeline
- [ ] Streaming callbacks wired through
- [ ] Confirmation pause/resume working end-to-end

---

## Phase 4: Observation & Self-Correction (Intelligence)
**Goal:** The agent sees tool results and dynamically decides what to do next.

### 4.1 Result Observation
- Tool results are appended to the conversation as `tool_result` messages
- The LLM sees the full result and can:
  - Summarize results for the user
  - Pick the best option from a list
  - Chain another tool based on results (e.g., search → pick cheapest → book)
  - Ask the user to choose if ambiguous

### 4.2 Error Recovery
- If a tool returns `{"status": "error", ...}`:
  - LLM sees the error message
  - Can retry with different parameters
  - Can try an alternative approach (e.g., different search terms)
  - Can explain the error to the user and ask for help
- If a tool times out:
  - Report partial progress
  - Ask user if they want to retry
- Max 2 retries per tool per loop iteration

### 4.3 Adaptive Planning
- The LLM doesn't need a pre-built plan — it decides step by step:
  - User: "Email me the cheapest flight to Mombasa next Friday"
  - LLM thinks: "I need to search flights first"
  - Calls: `search_flights(origin="Nairobi", destination="Mombasa", departure_date="2026-03-20")`
  - Sees results: 5 flights, cheapest is KQ at 12,500 KES
  - LLM thinks: "Now I need to compose an email with this info"
  - Calls: `send_email(to=user_email, subject="Cheapest Flight to Mombasa", text="...")`
  - Confirms with user (high-risk) → user says yes → sends
  - LLM: "Done! I found and emailed you the cheapest flight."

### 4.4 Memory Updates
- After each successful tool execution, update memory_state with new entities
- After the loop completes, save a summary of what was accomplished
- Record action receipts for all tool calls (already implemented)

### Deliverables
- [ ] Tool result observation working in loop
- [ ] Error recovery with retry logic
- [ ] End-to-end multi-tool chaining tests
- [ ] Memory updates after tool execution
- [ ] At least 5 complex multi-step scenarios tested

---

## Phase 5: Advanced Agent Capabilities (Power Features)
**Goal:** Match Claude's advanced agentic features.

### 5.1 Parallel Tool Calls
- When the LLM returns multiple tool_use blocks in one response, execute them in parallel
- Example: "What's the weather in Nairobi and search flights to Mombasa"
  - Both `get_weather` and `search_flights` run concurrently via `asyncio.gather()`
- Results are appended together and the LLM sees both

### 5.2 Thinking / Reasoning Transparency
- Stream the LLM's reasoning to the user (like Claude's thinking):
  - "Let me search for flights first..."
  - "I found 5 options. The cheapest is KQ at 12,500 KES. Let me compose the email..."
- This happens naturally via the text blocks in tool_use responses
- Add `thinking` phase to progress events

### 5.3 Sub-Agent Delegation
- For complex tasks, the main agent can spawn a focused sub-loop:
  - Main agent: "I need to plan a full trip. Let me handle each part."
  - Sub-loop 1: Search and compare flights
  - Sub-loop 2: Search and compare hotels
  - Sub-loop 3: Compose itinerary from best options
- Implemented as nested `run_agent_loop()` calls with scoped tool sets
- Cost-controlled: sub-agents share the parent's token budget

### 5.4 Long-Running Task Handoff
- If a task will take too long (e.g., monitoring a payment):
  - Agent creates a Temporal workflow for the async part
  - Reports back: "I've set up monitoring. I'll notify you when the payment completes."
  - Temporal workflow triggers a WebSocket notification when done
- Bridge between agent loop (synchronous conversation) and Temporal (durable async)

### 5.5 Document/File Understanding
- Agent can read uploaded documents as context for tool calls
- "Read this PDF invoice and create a payment link for the amount"
  - Agent sees document text (from DocumentUpload.processed_text)
  - Extracts amount, description
  - Calls create_payment_link with extracted data

### Deliverables
- [ ] Parallel tool execution via asyncio.gather
- [ ] Reasoning transparency in stream
- [ ] Sub-agent delegation for complex tasks
- [ ] Temporal handoff for long-running work
- [ ] Document-aware tool calls

---

## Phase 6: LLM Provider Optimization (Cost & Quality)
**Goal:** Optimize the LLM calls for cost, speed, and quality.

### 6.1 Upgrade LLM Client for Tool Use
- **File:** `Backend/orchestration/llm_client.py` (modify)
- Add native tool_use support to the Anthropic provider:
  ```python
  response = client.messages.create(
      model="claude-sonnet-4-6",
      messages=messages,
      tools=tool_definitions,
      max_tokens=4096,
  )
  ```
- Handle `tool_use` stop reason and extract tool calls
- Handle streaming with tool_use blocks (content_block_start, content_block_delta)

### 6.2 Prompt Caching
- Use Anthropic's prompt caching for the system prompt + tool definitions
- These stay constant across turns — cache them to reduce cost by ~90% on cached tokens
- Add `cache_control: {"type": "ephemeral"}` to system prompt block

### 6.3 Model Selection Strategy
- **Complex reasoning** (multi-step planning): Claude Sonnet 4.6
- **Simple tool calls** (single action, clear intent): Claude Haiku 4.5 (cheaper, faster)
- **Fallback**: Llama 3.1 via HuggingFace (existing fallback path)
- Auto-detect complexity: if user message is < 20 words and maps to 1 obvious tool → use Haiku

### 6.4 Token Budget Per Loop
- Track tokens across all iterations of a single agent loop
- Warn user when approaching budget: "I'm running low on my thinking budget for this request."
- Hard cap prevents runaway loops

### Deliverables
- [ ] Native tool_use in llm_client.py for Anthropic
- [ ] Prompt caching for system + tools
- [ ] Model routing (Sonnet vs Haiku)
- [ ] Per-loop token tracking

---

## Phase 7: Safety & Guardrails for Agentic Mode (Critical)
**Goal:** Ensure the agent loop is safe, auditable, and user-controlled.

### 7.1 Action Approval Policies
- **Always confirm:** Payments (create_invoice, withdraw, create_payment_link), send_email, send_whatsapp, book_travel_item
- **Auto-execute:** Read-only actions (search, check_balance, get_weather, etc.)
- **User-configurable:** Per-action approval overrides in user preferences
- Policy enforcement happens in `tool_executor.py` before every tool call

### 7.2 Loop Guardrails
- Max 10 iterations per user message
- Max 15 tool calls per loop
- Max 120 seconds wall clock
- No duplicate tool calls with identical parameters in the same loop
- If the LLM tries to call a tool that was already called with the same input, return cached result

### 7.3 Audit Trail
- Every tool call in the loop is logged:
  - tool_name, tool_input, tool_output, timestamp, iteration_number
- Stored in ActionReceipt model (existing)
- Telemetry event per tool call (existing telemetry.py)
- Full loop transcript available for debugging

### 7.4 Injection Protection
- Existing security_policy.py injection detection runs on every tool input
- Tool results are marked as `tool_result` role — LLM knows these are system-generated
- Never inject raw tool results into system prompt (prevents result-based injection)
- Sanitize all tool inputs through existing `sanitize_parameters()`

### 7.5 User Control
- User can interrupt the loop: "stop" / "cancel" mid-execution
- User can switch modes: `/mode classic` to revert to old pipeline
- User can see what tools are available: "what can you do?"
- User can disable specific tools: "don't use whatsapp"

### Deliverables
- [ ] Approval policies enforced in tool_executor
- [ ] Loop guardrails (iterations, time, dedup)
- [ ] Full audit trail per loop
- [ ] Injection protection on tool inputs and results
- [ ] User interrupt and mode switching

---

## Phase 8: Testing & Hardening
**Goal:** Comprehensive testing of the agentic system.

### 8.1 Unit Tests
- Tool schema generation from ACTION_CATALOG
- Tool executor routing and safety checks
- Agent loop mechanics (mock LLM, mock tools)
- Confirmation pause/resume
- Error recovery paths
- Iteration limits

### 8.2 Integration Tests
- End-to-end: User message → agent loop → tool calls → streamed response
- Multi-tool chaining: search → pick → book → email
- Error scenarios: tool failure → retry → fallback
- Confirmation flow: tool paused → user confirms → tool executes → loop continues
- Rate limiting and token budget enforcement

### 8.3 Scenario Tests (Manual)
- "Find the cheapest flight to Mombasa and email it to me"
- "Check my balance, and if it's over 10k, create an invoice for 5k"
- "Search hotels in Nairobi, pick the best rated one, and add it to my itinerary"
- "What's the weather in Dubai? Also search flights there for next month"
- "Send a WhatsApp to +254... with my latest invoice details"

### 8.4 Safety Tests
- Prompt injection via tool results
- Loop runaway prevention
- Unauthorized tool access attempts
- Cross-user data isolation

### Deliverables
- [ ] Unit test suite for all new modules
- [ ] Integration test suite
- [ ] 10+ scenario tests documented and passing
- [ ] Safety test suite

---

## Implementation Order & Dependencies

```
Phase 1 (Tool Schemas)          ← No dependencies, start here
    |
Phase 2 (Agent Loop Core)      ← Depends on Phase 1
    |
Phase 3 (Consumer Integration) ← Depends on Phase 2
    |
Phase 4 (Observation)          ← Depends on Phase 3
    |          |
Phase 6 (LLM)  Phase 7 (Safety)  ← Can run in parallel
    |          |
Phase 5 (Advanced)             ← Depends on Phase 4
    |
Phase 8 (Testing)              ← Depends on all phases
```

## Estimated Effort Per Phase

| Phase | Scope | Files Changed/Created |
|-------|-------|----------------------|
| 1 - Tool Schemas | 3 files | tool_schemas.py (new), action_catalog.py (modify), tool_executor.py (new) |
| 2 - Agent Loop | 2 files | agent_loop.py (new), agent_prompts.py (new) |
| 3 - Consumer Integration | 1 file | consumers.py (modify) |
| 4 - Observation | 2 files | agent_loop.py (modify), memory_state.py (modify) |
| 5 - Advanced | 3 files | agent_loop.py (modify), temporal_integration.py (modify), agent_prompts.py (modify) |
| 6 - LLM Optimization | 1 file | llm_client.py (modify) |
| 7 - Safety | 2 files | tool_executor.py (modify), security_policy.py (modify) |
| 8 - Testing | 4+ files | tests/ (new test files) |

## What Gets Retired

| Old Component | Status | Reason |
|--------------|--------|--------|
| `intent_parser.py` | **Keep as fallback** | Used in "classic" mode and as lightweight fast-path |
| `workflow_planner.py` (ad-hoc planning) | **Retire** | Agent loop replaces pre-planned workflows |
| `data_synthesizer.py` | **Retire** | LLM synthesizes responses natively in the loop |
| `mcp_router.py` (routing logic) | **Simplify** | Tool executor handles routing directly; safety/state logic stays |
| `manager_verifier.py` | **Retire** | Agent does its own verification via observation |
| Temporal workflows | **Keep** | Still needed for scheduled/triggered/durable workflows |
| All connectors | **Keep** | They become the tools the agent calls |
| Safety/security layers | **Keep** | Enforced in tool_executor before every call |
| Memory system | **Keep** | Fed into agent system prompt |
| Streaming infrastructure | **Keep** | Agent loop yields chunks through existing broadcast |

---

## Success Criteria

The transformation is complete when Mathia can handle these without any pre-planned workflows:

1. "Find flights to Mombasa next Friday, pick the cheapest, and email it to john@example.com"
   - Agent: search_flights → analyze results → compose email → confirm → send_email

2. "Check my balance. If it's over 5000, withdraw 2000 to 0712345678"
   - Agent: check_balance → evaluate condition → withdraw (with confirmation)

3. "What's the weather in Nairobi and Dubai? Compare them and send me a WhatsApp summary"
   - Agent: get_weather(Nairobi) + get_weather(Dubai) in parallel → compose comparison → confirm → send_whatsapp

4. "Search hotels in Mombasa, add the best one to my itinerary, then search flights there"
   - Agent: search_hotels → pick best → add_to_itinerary → search_flights → present results

5. Error recovery: "Send an email to invalid-email" → agent sees error → asks user for correct email → retries
