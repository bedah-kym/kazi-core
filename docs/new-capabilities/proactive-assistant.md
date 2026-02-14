## Proactive Personal/Room-Aware Assistant – Design

### Goals
- Make Mathia feel “personal” and proactive: notice user type, routines, and pain points; suggest workflows/reminders/invoices/meetings without being spammy.
- Keep cost/infrastructure low (no self-hosted GPU); leverage RAG + lightweight models.
- Preserve transparency and user control (opt-in, premium toggle, easy off/forget).

### Value
- Higher activation/retention: users discover features (invoices, reminders, workflows, bookings).
- Tailored suggestions: based on user category (business/freelancer/etc.), room context, and past behavior.
- Safer automation: suggestions constrained to allowed tools/workflows.

---
## Data Inputs (what we store and why)
1) **Onboarding profile (explicit)**
   - User category (business, freelancer, student, hobbyist, ops, finance, travel planner, support, sales, engineering, product).
   - Industry vertical (dropdown + free-text).
   - Timezone, working hours, preferred channels (email/WhatsApp), currency.
   - Top 3 goals (short text): e.g., “collect payments”, “book meetings”, “trip planning”.

2) **Room context (explicit)**
   - Pinned notes, room summaries, decisions, TODOs.

3) **Behavioral signals (implicit, minimal)**
   - Feature usage counts: invoices created, reminders set, workflows run, emails sent.
   - Recent intents (types, not content) for trend detection.

4) **Business docs (RAG)**
   - Policies, product FAQs, playbooks, onboarding guide.

5) **Optional “persona” mini-profile (opt-in)**
   - Preferred tone (concise/friendly/formal), response length, nudge frequency (low/med/high).

**Storage:** all in Postgres tables plus vector index (FAISS/pgvector) with metadata filters (`owner=user_id`, `room_id`, `type=profile|note|summary|signal`). Avoid storing raw secrets; hash or redact emails/phones when not required for actions.

---
## Retrieval Layers (RAG)
- **Business index:** shared docs.
- **Room index:** pins + summaries for that room.
- **User index:** onboarding profile, persona prefs, key signals (as short facts), recent summaries.
- Retrieval filter per turn:
  - business k=4
  - room k=3 (room_id filter)
  - user k=3 (owner filter, only if user consented)
  - cap total context tokens (~1200–1500).
- Prompt sections: Persona/tools guardrails → Business context → User profile snippets → Room context → Recent history tail → User message.

---
## Proactive Triggers
1) **Idle nudge** (front-end timer or server-side heartbeat):
   - If no message for X minutes and user is active, send one contextual prompt:
     - “Want me to set up a quick invoice template?” (if they’re a business and never used invoices)
     - “Schedule your next client check-in?” (if category = freelancer, high meeting usage)
   - Respect nudge frequency preference; never more than 1 per session.

2) **Milestone detection** (Celery periodic job):
   - New workflow created → suggest a follow-up (add schedule/webhook).
   - Payment webhook received → offer receipt email or balance check.
   - Travel search done → propose itinerary save or notification.

3) **Pattern detection (lightweight)**
   - Simple rules on usage counters; no ML required initially.
   - Example: If reminders=0 and user is “ops” → suggest a daily reminder template.

---
## Model Choices (keep cheap)
- Stay with current Claude/HF for main responses.
- Optional “affect/psychology” hint: small local classifier (e.g., `distilbert-base-uncased` fine-tuned on tone categories) to choose nudge style; run locally to avoid per-call cost.
- No fine-tune required to ship MVP; combine with RAG. Fine-tune later if tone consistency is lacking.

---
## Consent, Premium, Safety
- Flags:
  - `PROFILE_RAG_ENABLED` (per user).
  - `PROACTIVE_ASSISTANT_ENABLED` (per user, premium).
  - `NUDGE_FREQUENCY`: low/med/high; default low.
- UI:
  - Toggle in settings; “Forget my profile” button → delete user-owned vectors and profile rows.
  - Clear banner: “Proactive suggestions use your profile and room notes; you can turn this off anytime.”
- Safety:
  - Never suggest actions outside capability catalog.
  - Enforce withdrawal policy; don’t propose financial actions without policy present.
  - Rate-limit proactive messages.

---
## Phased Rollout
### Phase A (MVP: awareness nudges)
- Add onboarding fields (category, vertical, goals, timezone, currency, channel preference).
- Store profile + consent flags.
- Add room/user vector indexes and retrieval filters (reuse RAG scaffolding).
- Simple idle nudge rule: if no messages for 5–10 min, suggest one feature tailored by category & unused features.
- “Forget profile” + toggle in settings.

### Phase B (signals & smarter suggestions)
- Track feature usage counters per user.
- Add event-driven facts to user index (workflow created, invoice sent, booking made).
- Add persona prefs (tone, nudge frequency).
- Add pattern-based playbooks (if X=0 and category=Y → suggest Z).

### Phase C (tone polish / optional fine-tune)
- Small tone fine-tune or LoRA for consistent Mathia voice.
- Optional affect classifier for choosing nudge style.

---
## Work Packages (dev tasks)
1) Data model: `UserProfileContext` (onboarding fields, consent flags), `UserSignal` (usage facts), `RoomContextNote` (pins/summaries vectorized).
2) Vector store: extend planned FAISS/pgvector with owner/room metadata filters.
3) Ingestion: hooks on pin, daily summary, key events → embed + store.
4) Retrieval: update LLM wrapper to merge business + user + room contexts when flags allow.
5) Proactive service: idle timer + rule-based nudge generator; rate-limit per user/session.
6) UI/Settings: toggles, nudge frequency, forget-me.
7) Safety: redact PII before embedding where possible; enforce tool whitelist in prompt.

---
## Open Questions
- Exact idle interval and nudge cadence (start with 10 min, 1 nudge/session).
- Which features to spotlight per category (create a small mapping table).
- Storage choice: stick to FAISS first, move to pgvector if concurrency demands.
