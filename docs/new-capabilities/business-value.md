## Business Value Assessment – Proactive, Context-Aware Mathia

### Who we sell to
- **Freelancers / solo operators (core beachhead)**  
  Pain: context juggling (clients, invoices, meetings), low tooling literacy, need “done-for-you” nudges.  
  Value: proactive reminders, invoicing prompts, workflow templates, payment follow-ups.
- **SMB teams (ops, support, sales, travel/field teams)**  
  Pain: fragmented docs/processes, manual status checks, repeated scheduling/follow-ups.  
  Value: shared room context, proactive checklists, “next-best-action” nudges, payment and scheduling automations.
- **Internal champions in mid-size orgs (team leads / chiefs of staff / RevOps)**  
  Pain: adoption and compliance; need guardrails + reporting.  
  Value: opt-in profiles, consent controls, auditability, safe automations.
- **Consumer/non-tech users (secondary)**  
  Use for reminders, small errands; lower ARPU; good for viral but not primary revenue.

### Value propositions by persona
- **Freelancer:** “Your assistant that remembers clients, drafts follow-ups, and keeps cashflow moving.” (Invoices, payment completed triggers, meeting booking nudges.)
- **Ops/Support lead:** “Shared brain that surfaces stuck items and schedules next steps without tickets.” (Room context, team nudges, safe withdrawals caps.)
- **Sales/RevOps:** “No-leak follow-ups: meeting booking + email/WhatsApp touchpoints + payment links when deals close.”
- **Travel/field teams:** “Plan, store, and trigger itineraries; proactive reminders on travel days.”

### Business metrics to sell/track
- Activation: time to first automation (workflow) and first scheduled nudge accepted.
- Engagement: weekly active rooms with context pins; nudges accepted/ignored; reminders created.
- Revenue: invoices/payment links sent; conversion from nudge → payment; premium toggle adoption.
- Retention: weekly active users; repeat workflow runs; churn vs. non-proactive cohort.
- Operational: avg time saved per user (self-reported), reduced missed follow-ups (proxy: declined/ignored nudges).

### Pricing levers
- Free: basic chat + limited nudges, no personal RAG.
- Pro: personal/room RAG, proactive nudges, Mailgun/WhatsApp usage bundle, workflow schedules.
- Team: shared context across rooms, audit logs, consent controls, webhook/schedule volume, Temporal worker priority.

### Risks & mitigations
- **Privacy/creepiness:** Users may dislike proactive prompts. → Default low-frequency; explicit opt-in; “forget profile”; transparent “Why am I seeing this?”.
- **Hallucinated suggestions:** RAG gaps lead to wrong nudges. → Confidence thresholds; require retrieved sources; fall back to ask-clarify.
- **Over-nudging/spam:** Fatigue lowers retention. → Per-user rate limits; nudge frequency setting; A/B test cadence.
- **Data sensitivity (payments/withdrawals):** → Keep policy checks in code, not just LLM; enforce allowlists and caps (already in workflows).
- **Cost creep (embeddings/LLM):** → Cache embeddings, cap context, cheap models for intent, premium for generation; FAISS/pgvector to avoid vendor lock-in.
- **Adoption confusion:** Users may still not see use cases. → Onboarding asks category/goals and immediately offers 2–3 relevant templates (per persona).

### Core target (recommendation)
- Start with **freelancers and small teams** that invoice, schedule, and message clients (service businesses, agencies, coaches). Highest pain/activation, lowest sales cycle.
- Land with “payments + scheduling + reminders” bundle; expand to SMB teams with shared rooms and auditability once RAG/proactive layer proves retention.

### Success criteria (business)
- 50%+ of new users accept at least one proactive suggestion in week 1.
- >30% reduction in missed follow-ups (measured by unanswered reminders/meetings).
- ARPU uplift from Pro plan driven by RAG + proactive features usage.

### Name options (fresh, non-placeholder)
- **LumaDesk** — “Your desk-side co-worker who remembers and nudges.”
- **SignalBay** — “Harbor for the signals that keep your day on course.”
- **FlowForge** — “Forge workflows and get them done together.”
- **NudgeHub** — “Gentle pushes that ship real work.”
- **OrbitDesk** — “Keeps your work in orbit, nothing drifts.”
- **Pulseboard** — “See the pulse, act in one click.”
- **Relayroom** — “Pass the baton—tasks move, you don’t stall.”

### New candidate names (low-collision, friendly vibe) — *preliminary uniqueness, re-check before launch*
- **Toolhand** — “Hand over your tools; I get the work done.”
- **Workbuddy** — “A buddy that knows your stack and jumps in.”
- **Tandemly** — “Ride tandem on tasks—less drag, more finish.”
- **Orbitkin** — “A close orbiting helper that keeps context tight.”
- **Sparkwing** — “Spark ideas, wing the execution.”
