# Kazi vNext: Equipping the LLM to Handle Unpredictable Requests

Status: draft for implementation
Owner: Bedan
Supersedes framing of: `kazi-shell-exec-design.md` (kept as a subsystem, not the headline feature — see Section 10)

## 0. Philosophy Shift

The old framing was "add shell access so Kazi can do things without a connector." That's too narrow, and it's not the actual problem. The real problem: **Kazi's pipeline assumes it always knows, in advance, what capability a request needs and what shape the response takes.** It doesn't. A user will ask to ping an ISP, sing a lullaby, debug a container, or something nobody on this project has thought of yet — and today's architecture only has a good answer for the first case, by accident.

The philosophy for vNext: **don't build one more feature, build the LLM a bigger set of legitimate moves, plus the judgment to pick between them, plus governance that scales with however unpredictable the request gets.** Concretely, that means four capability gaps, not one:

1. Kazi can't reach a capability that has no registered connector (execution gap)
2. Kazi always answers in text regardless of what the request actually wants (modality gap)
3. Kazi has no first-class way to *generate* non-text content — audio, images — without improvising (generation gap)
4. Anything Kazi improvises today is thrown away; nothing it learns compounds (memory gap)

Sections 3–7 below are one pillar per gap, plus the governance layer all four share.

---

## 1. What's Broken Today (mapped to Kazi's current architecture)

From Kazi's own architecture docs:

```
Client -> ChatConsumer -> OrchestrationCoordinator -> ContextManager
  -> Agent Loop (think -> act -> observe)
  -> Tool Executor (security gates + capability checks)
  -> Connectors
```

Three concrete gaps in this pipeline, each traced to a real line in the docs:

- **"Tool Executor... routes to the correct connector"** — implies a request either matches a registered connector or it doesn't. There's no documented path for "no connector matches, but the request is still reasonable."
- **"LLM sees result, generates natural language response"** (the documented data-flow example) — output shape is hardcoded to text. There's no decision point asking whether the response should be a voice note, an image, or a file.
- **Memory is 3-tier: hot context, entity tracking, persistent summaries** — all episodic ("what happened"). Nothing procedural ("how to do X"), so anything Kazi figures out ad hoc today is gone the moment the session ends.

---

## 2. The Four Pillars

```
                     Governance Hook Layer (Pillar A)
                     pre/post-tool-call interception, risk tiers, autopilot
                                    |
        -----------------------------------------------------------
        |                  |                    |                 |
   Pillar B           Pillar C             Pillar D            Pillar E
   Capability Reach   Modality Selection   Generation +        Procedural Memory
   (sandboxed exec    (what shape should   Delivery Connectors + Curator
   backends)          the response take)   (TTS/image/etc,     (learn from B & D,
                                            typed, not shell)   stage, promote, prune)
```

All four sit on top of the existing Agent Loop / Tool Executor / Connector Registry — none of them replace it.

---

## 3. Pillar A — Governance Hook Layer (foundation)

Every other pillar introduces a new way for Kazi to act on the world (run a shell command, call a generation API, spin up a backend). All of them need the same kind of gate, so build it once, generically, instead of once per pillar.

- Implement as a hook registered against the Tool Executor: `before_tool_call(action, params) -> tier` and `after_tool_call(action, result)`, not code hardcoded into `security_policy.py` for one specific action.
- **Risk tiers** (same shape regardless of which pillar triggered the call):

| Tier | Meaning | Behavior |
|---|---|---|
| Safe | Read-only, reversible, no cost | Runs immediately |
| Scoped | Mutating but bounded (writes to agent's own workspace, calls a paid API under a budget) | Runs under autopilot policy; logged |
| High-risk | Destructive, irreversible, touches anything outside the agent's own sandbox/workspace | Always pauses for human approval via Kazi's existing durable agent-loop approvals (`WorkflowApprovalRecord`) |
| Denied | Outside network/credential allowlist entirely | Blocked before it reaches the classifier |

- **Autopilot** is a per-room/per-user config (`enabled`, `max_tier_auto`, allowlists) that lets Safe and Scoped tiers skip the pause. High-risk is a hard floor — never auto-approved, matching the "explicit deny rules always win even in the most permissive mode" pattern used by comparable coding-agent sandboxes.
- This is the same design as the original shell-exec doc's Command Risk Classifier — just generalized so Pillar D's generation connectors and Pillar B's sandboxed backends both register against it instead of each inventing their own gate.

---

## 4. Pillar B — Capability Reach (Sandboxed Execution Backends)

This is what the earlier doc called "shell exec" — kept, but generalized and *not assumed to exist yet*, per your instruction.

- New action `run_shell(cmd, backend, cwd, timeout_s)`, registered in the Action Catalog, risk level high by default (routes through Pillar A).
- **Don't commit to one execution environment.** The earlier doc specified a single dedicated mini PC. That's one valid backend, not the only one worth supporting — comparable self-improving agents support several interchangeable backends (local process, Docker, SSH to a remote box, and ephemeral cloud sandboxes) selected per task rather than baked in at build time. Design the `ShellConnector` against a small backend interface (`execute(cmd) -> stdout/stderr/exit`) so a mini PC, a disposable container, or a remote sandbox are all just implementations of the same interface — start with one (the mini PC is a fine first backend), but don't hardcode the assumption into the connector itself.
- Isolation for whichever backend you start with: dedicated network segment, no route to Kazi's Postgres/Redis/credential stores, dedicated OS account, output size and timeout caps enforced at the backend, not just by Kazi.
- Existing durable approval flow handles the "pause for confirmation" case unmodified — no new approval infrastructure needed.

---

## 5. Pillar C — Output Modality Selection

The gap: nothing in the Agent Loop today asks "should this be a voice note instead of text?" This has to be an explicit decision node, not an emergent property of a smarter model.

- New step in the Agent Loop, after content is determined but before the response is rendered: `select_modality(request, content, channel_capabilities) -> {text | voice | image | file}`.
- Inputs to that decision: the request's own phrasing ("sing," "show me," "draw"), the content's nature (is it inherently melodic/visual), and what the destination channel can actually carry (see Pillar D's delivery layer — no point choosing voice if the channel can't send it).
- This step is cheap (one extra LLM decision, or a lightweight classifier) and applies to every response, not just ones that end up needing Pillar B or D — most responses will still resolve to "text," this just makes that a decision instead of a default.

---

## 6. Pillar D — Generation Connectors + Delivery Layer

Two halves: producing non-text content, and actually shipping it to the user.

### 6.1 Generation connectors (typed, first-class — not shell improvisation)
- `TextToSpeechConnector`, `MusicGenConnector`, `ImageGenConnector` as ordinary registered connectors with typed schemas, same as `WeatherConnector` today — because a capability that gets used repeatedly (singing, drawing) deserves a stable, reviewable interface, not an agent re-improvising shell/curl commands from scratch each time.
- **When no such connector exists yet**, Pillar B is the fallback — the agent uses `run_shell` to reach a local or cloud generation tool ad hoc. That fallback path is exactly what should feed Pillar E (Section 7): a shell sequence that successfully produces, say, a lullaby recording is a candidate to become `MusicGenConnector`, not something to reinvent next time.

### 6.2 Delivery layer (extends the existing Notifications module)
- Kazi's Notifications system currently documents in-app, email, and WhatsApp channels — extend it with per-channel media capability flags (`supports_voice`, `supports_image`, `max_audio_bytes`) so Pillar C's modality decision can check *before* committing to a format.
- Concrete format constraints worth building against directly: [Verified] Telegram's Bot API exposes a dedicated `sendVoice` method for OGG Opus files up to 50MB; WhatsApp's equivalent requires a session-based connection and the same OGG Opus format. Different platforms (LinkedIn, Instagram, etc.) have inconsistent or undocumented voice-message APIs — [Cannot verify] whether any additional channel Kazi might add later supports voice at all; check per-channel before assuming.
- If the target channel doesn't support the chosen modality, Pillar C's decision degrades gracefully (e.g., voice unavailable -> send text with a note, rather than failing silently).

---

## 7. Pillar E — Procedural Memory + Curator

Add a 4th memory tier, procedural, alongside Kazi's existing hot context / entity tracking / persistent summaries. This is where anything improvised by Pillar B or D goes to be evaluated before it becomes permanent.

- **Staging**: when Pillar B's fallback (or a first attempt at a new generation task) succeeds, draft it into procedural memory as a candidate — command sequence or connector call pattern, plus what it accomplished. Nothing is promoted automatically.
- **Curator**: a background maintenance pass, not a human doing manual review of every candidate. Concrete, working reference design worth copying closely: [Verified] a comparable self-improving agent runs its curator on an idle-triggered cadence (default: every 7 days, but only if the agent has also been idle for at least 2 hours), moves unused candidates through active → stale → archived states (default 30 days to stale, 90 to archived), archives rather than deletes (recoverable), supports a dry-run mode that reports what it would do without mutating anything, and lets specific skills be pinned so they're immune to auto-archival.
- **Promotion**: a human promotes a procedural-memory candidate to a real registered connector (Section 6.1) — this is the validation gate from the original shell-exec doc, now with procedural memory as its concrete home instead of an undefined "review it somehow" step.
- This directly prevents the failure mode the curator pattern above was built to solve: dozens of narrow, near-duplicate one-off shell scripts piling up and polluting context, instead of consolidating into a small number of trustworthy connectors.

---

## 8. End-to-End Example: "Sing me a lullaby"

Ties all four pillars together against the concrete case that motivated this doc.

```
1. User: "sing me a lullaby" (via Telegram)
2. Agent Loop: drafts lullaby lyrics (native text generation, no connector needed yet)
3. Pillar C — Modality Selection: request phrasing ("sing") + channel capability
   (Telegram supports sendVoice) -> modality = voice
4. Pillar D — Generation: no MusicGenConnector registered yet
   -> falls back to Pillar B: run_shell calls a local/cloud TTS engine with the lyrics
5. Pillar A — Governance: classifies this run_shell call as Scoped (external API,
   bounded cost, no destructive filesystem/network reach) -> autopilot auto-approves
6. Pillar D — Delivery: resulting audio is OGG Opus, within Telegram's 50MB sendVoice
   limit -> delivered as a native voice note, not a text message with a file link
7. Pillar E — Procedural Memory: the successful shell sequence (lyrics -> TTS call ->
   OGG Opus output) is drafted as a candidate skill for later human review/promotion
   to a proper MusicGenConnector
```

Run the same request a month from now, after promotion: steps 4–5 collapse into a single typed connector call, already gated by its own registered risk level — no shell involved at all. That collapse is the point of Pillar E.

---

## 9. Config Surface

```yaml
governance:
  autopilot_enabled: false        # opt-in per room
  max_tier_auto: "safe"           # "safe" | "scoped" — high-risk never auto

capability_reach:
  backends:
    - name: "mini-pc"
      type: "persistent"          # vs "ephemeral_container" | "remote_sandbox"
      host: "10.x.x.x"
  timeout_s_default: 120

modality:
  default: "text"
  channel_capabilities:
    telegram: { voice: true, image: true, max_audio_bytes: 52428800 }
    whatsapp: { voice: true, image: true }
    email: { voice: false, image: true }

procedural_memory:
  curator:
    enabled: true
    interval_hours: 168           # 7 days
    min_idle_hours: 2
    stale_after_days: 30
    archive_after_days: 90
    dry_run_default: false
```

---

## 10. Relationship to the Prior Shell-Exec Doc

`kazi-shell-exec-design.md` is not replaced — it's the detailed implementation spec for Pillar B's first backend (the mini PC) and Pillar A's original risk-tier table. Read it for the concrete daemon/connector wiring; read this doc for how that piece fits against the other three pillars it was written in isolation from. Where the two disagree (e.g., "one dedicated mini PC" vs. "pluggable backend interface"), this doc's framing wins — implement the mini PC as the first backend behind the interface described in Section 4, not as the only backend the connector can ever talk to.

---

## 11. Phased Rollout

**Reality check before this list: only step 1–2 are earned scope right now.** Steps 3+ describe the full vision, not a build queue — don't start Pillar E or a dedicated Pillar C infrastructure layer until the earlier steps have actually produced the problem they're meant to solve. Building lifecycle/curation tooling for skills that don't exist yet, or a modality-decision layer for an output need you haven't hit twice, is scope pulled forward from a future that hasn't happened.

1. **Pillar A alone**: generic hook layer wired into the existing Tool Executor, no new actions yet — prove the interception point works with a no-op classifier.
2. **Pillar B, backend #1**: mini PC + `ShellConnector`, gated by Pillar A, no autopilot (everything pauses for approval). **Stop here until this is real and used.**
3. **Pillar D, one connector, as a plain registered action** — not a new "modality selection" pipeline stage. Pick the single most-requested non-text need (likely TTS), register it as a typed connector (`send_voice_note`), and let the existing Agent Loop's normal tool-selection decide when to call it — that decision *is* Pillar C; don't build C as separate infrastructure.
4. **Pillar E, only after you've manually promoted 3–5 ad hoc shell solves into connectors yourself** and felt the actual pain of no staging area. Start with dry-run only.
5. **Autopilot for Safe tier**, then Scoped — same order as the original doc's phase plan, now covering all pillars' actions, not just shell.

---

## 12. Open Questions

- Should Pillar C's modality decision be a full LLM call every turn, or a cheap heuristic that only escalates to the LLM when the request looks expressive/ambiguous? Full-call is simpler to reason about; heuristic-first is cheaper at scale.
- Curator cadence above is copied from a reference default (7 days / 2 hours idle) — worth tuning once you see how often Kazi actually improvises versus how often you're online to notice.
- Multi-backend Pillar B (Section 4) raises a routing question not addressed here: how does the agent pick *which* backend for a given task when more than one is registered? Deferred until backend #2 is actually built.

---

## 13. References

- OpenHands' Docker-runtime client/server split — closest published architecture for the Pillar B connector/backend separation.
- Claude Code's sandboxing model — reference for Pillar A's tiered risk gating with a hard floor that survives even the most permissive auto-allow mode.
- A comparable self-improving agent's Curator design — concrete, working reference for Pillar E's active/stale/archived lifecycle, idle-triggered cadence, dry-run mode, and pinning.
- Telegram Bot API `sendVoice` and WhatsApp Business API's session-based voice delivery — concrete format constraints (OGG Opus, 50MB cap) for Pillar D's delivery layer.
