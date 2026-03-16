# Mathia.OS — Task Log

## Completed

### 2026-03-15
- **Contacts feature** — `Contact` model, REST API (CRUD + search), agent tools (`lookup_contact`, `save_contact`), context panel accordion, global/room-scoped toggle at creation — `59153bf`, `225d707`
- **Room linking** — Bidirectional room linking via `RoomContext.related_rooms` M2M, REST API (link/unlink/list), linked room context in agent prompts, `RoomNote.is_private` privacy control — `7c02c67`

### 2026-03-14
- **Prompt caching fixes** — Sub-agent and tool definition caching gaps patched — `65b52b6`
- **Memory lifecycle** — Scored ranking (recency 0.3 + confidence 0.5 + semantic 0.2), search, entity bridge — `87526c3`
- **Streaming reliability** — Reverted to `create_message` with simulated streaming — `f92d75d`
- **Streaming UX** — Live markdown, visible thinking, smoother chunks — `38bf795`
- **Real-time streaming** — Agent loop switched from blocking to real-time token streaming — `520aa3c`
- **Mobile fixes** — Ghost taps from invisible overlays — `295f1f1`
- **Agentic UI redesign** — Tool timeline, smooth transitions, mobile fixes — `efda18f`

### 2026-03-13
- **Agent loop multi-turn fix** — Context loss + stale memory + workflow import — `c7b701d`
- **Phases 1-8 agentic transformation** — Complete: tool schemas, agent loop, tool executor, prompts, meta-tools, streaming, guardrails, tests — `a42a0ea` → `61fb6f8`

---

## In Progress

_(none)_

---

## Planned / Backlog

- Multi-turn context loss investigation (feedback memory: agent loop issues — awaiting logs)
- agents.md kept up to date with new features
