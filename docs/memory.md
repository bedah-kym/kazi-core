# Agent Memory

Kazi's memory is the reason it doesn't start from zero every conversation.

## The three tiers

| Tier | What it holds | Where |
|---|---|---|
| **Hot** | Room summary, active topics, recent decisions — always in context | `chatbot/context_manager.py` |
| **Warm** | Extracted entities, action items, insights with confidence scores | `orchestration/memory_state.py` |
| **Cold** | Daily and weekly compressed summaries for long-term recall | database |

The agent also tracks your preferences — tone, date format, currency, verbosity,
and more — and adapts over time.

## Tuning the budgets

These caps stop the context window from silently overflowing:

| Variable | Default | What it caps |
|---|---|---|
| `CONTEXT_PROMPT_MAX_CHARS` | `8000` | Room context injected into the system prompt |
| `HISTORY_MAX_CHARS` | `60000` | Conversation history budget |
| `HISTORY_MAX_MESSAGES` | `50` | History turns kept per loop |
| `HISTORY_COMPACTION_ENABLED` | `False` | Opt-in: trim oldest turns when over budget |

When truncation happens, Kazi emits a `context_truncated` (or
`context_compacted`) telemetry event — you can see exactly when and why.

## A quick rule of thumb

- Bumping `CONTEXT_PROMPT_MAX_CHARS` gives the agent more room context but costs
  tokens. Start at the default, raise it only when you see it truncating.
- Turn on `HISTORY_COMPACTION_ENABLED` for long-running rooms so the agent
  keeps recent turns and drops the oldest instead of failing.

See [Configuration](configuration.md) for the full list.
