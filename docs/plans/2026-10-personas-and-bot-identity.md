# Plan: Bot identity, persona UX, chat skill creation, admin escalation, schedule health (2026-10)

Status: draft for maintainer approval

Covers five workstreams from the 2026-10-03 direction: (A) kill the `Mathia`
name, (B) persona program with dashboard UX + avatars, (C) `@admin` chat
escalation, (D) skills in the dashboard + chat skill creation, (E) the
schedule silent-skip gap. **Boot persistence is explicitly out of scope** for
now.

---

## A. Mathia → Kazi identity sweep

**Goal:** the bot is named Kazi everywhere; `Mathia` appears nowhere in code,
prompts, templates, or the bot user (historical migrations excepted).

**Touches:**
- `Backend/chatbot/consumers.py` — the `@mathia` wake trigger (line ~819) becomes `@kazi`.
- `KAZI_AGENT_NAME` in `.env` (manual, one line) and any settings default.
- Prompt/template/string sweep (~130 refs): `agent_prompts.py`, templates
  (`Automations - Mathia`, etc.), docstrings, help texts.
- Bot user rename `mathia` → `kazi` via a one-off data migration (users app —
  **protected**); a collision with an existing `kazi` account merges room
  memberships into it before retiring the old row.
- Static asset: `mathia-avatar.svg` → `kazi-avatar.svg` + references in
  `chatbot/views.py`.

**Verification:** grep for `Mathia` returns zero in `Backend/` and templates;
chat wake works with `@kazi`; existing rooms still resolve their bot member.

**Open questions:** none — hard cut, no backwards alias.

---

## B. Persona program (dashboard UX, identity, avatars)

**Goal:** users manage *their* personas from the dashboard (name,
description, tool scope, risk ceiling, approval boundary, avatar, skills);
persona creation stays admin-side; new rooms pick an existing persona; the
persona knows its own name in chat and shows its avatar in the room list.

**Model (migration — protected):**
- `Persona.avatar` — `ImageField` upload (null), served via MEDIA.
- `Persona.skills` — JSON list of skill names from `skill_registry` (filesystem
  source of truth; the list is an assignment, not a copy).
- `PersonaRequest` — `(user, room, name, description, status)` for the
  `@admin` persona-request flow (§C); admin approve → creates a draft Persona
  owned by the requesting user.

**Identity injection (no protected paths):**
- `orchestration/coordinator.py` (not protected): when a room resolves to a
  persona, append to the loop's context prompt:
  *"You are Kazi, operating as {persona.name}. {persona.description}. Tool
  scope: ... Risk ceiling: ... Always ask before: ..."* — plus the bodies of
  the persona's assigned skills (bounded by `SKILL_MAX_CHARS`).

**Avatars in the chat list:**
- `chatbot/views.py` room serialization (already has the avatar branch): if
  `room.persona` exists use the persona avatar; else the default Kazi avatar.
- Dashboard avatar upload for the persona owner.

**Dashboard pages (new, users app or new `personas` surface):**
- List my personas; edit form (name/description/scope/ceiling/boundary/skills
  chips from `list_skills()`); avatar upload. No create — create stays admin.
- New-room flow: persona picker bound to the creating user's active personas.

**Verification:** room with persona → chat opens with the identity line;
avatar appears in room list; out-of-scope tool still denied; another user's
persona not selectable.

---

## C. `@admin` chat escalation

**Goal:** users can message the super admin from any room via `@admin`, and
request a persona the same way.

**Feasibility (asked): yes — Django is irrelevant here, this is pure routing.**
Mentions are already parsed (the `@mathia` trigger). Plan:

- In `chatbot/consumers.py` (not protected): before bot dispatch, detect
  `@admin` (configurable handle via settings). The message persists in the
  room, the bot replies with a short ack only, and a `Notification` goes to
  superusers (`event_type="message.mention"` — matrix already exists;
  metadata carries room + message).
- `@admin request persona "<name>" — "<description>"` creates a
  `PersonaRequest` row (no self-authorization: the request is a proposal;
  admin confirms in the admin UI → draft Persona owned by the requester).

**Verification:** `@admin` message in a room → notification row for the
superuser; persona request → admin UI action creates a draft persona; bot
does not attempt to fulfill persona requests itself.

---

## D. Skills: dashboard management + chat creation

**Answer to "where do I add skills":** skills are filesystem folders
`skills/<name>/SKILL.md` at the repo root (`SKILLS_DIR` setting) with
frontmatter (`stage`, `pinned`, six-part body). The chat agent yesterday
couldn't find them because its sandboxed shell cwd is the persistent
workspace, not the repo root — and it has no tool that writes skills. Two
gaps, two fixes:

1. **Dashboard skills page** (not protected): list skills with stage + pinned
   badges; actions = promote/demote (staging→review→active→stale, per
   `_TRANSITIONS`), pin/unpin, and a create form that writes a `staging`
   SKILL.md through `write_staged_skill` (six-part contract template).
2. **Chat skill creation** — a `save_skill` meta-tool in `agent_loop.py`
   (**protected** — needs this plan's OK): takes name/description/tools/body,
   calls `workflows.promotion.save_session_as_skill` (already ships with v0.7
   #156 — staged skill folder + reviewable `WorkflowDraft`, never active).
   The chat agent can then say "save this as a skill" and get a real staged
   skill, promoted later in the dashboard.

**Verification:** dashboard create → `skills/foo/SKILL.md` appears with
`stage: staging`; promote → active; `load_skill` returns it; chat
"save this as a skill" → staged folder + draft, no active skill without
promotion.

---

## E. Schedule silent-skip gap (health watch)

**Goal:** skipped schedule fires become a visible, notified health signal
instead of silence.

**Touches (not protected):**
- `workflows/temporal_integration.py`: `fetch_schedule_health(trigger)` →
  `get_schedule_handle(id).describe()`; read `info.num_actions`,
  `num_actions_skipped_overlap`, `num_actions_missed_catchup_window`.
- `workflows/health.py`: new beat-style task `check_schedule_health` — for
  each active schedule trigger, compare skipped counts against the previous
  snapshot (cache); growth → `degraded` state + `workflow.health` notification
  ("Night Hawk skipped N fires since last check — worker likely down"), and
  the weekly digest lists it. Pausing is NOT the response (pausing doesn't fix
  skips); alerting is.

**Verification:** unit test with a mocked `describe()` showing skip growth →
  notification; zero growth → quiet.

---

## Order of work

A (rename) → B (persona program) → D (skills) → C (mentions) → E (schedule
health). A is independent and small; B's migration also carries
`PersonaRequest` for C.

## Protected paths needing human OK

- `Backend/users/migrations/*` (bot rename data migration)
- `Backend/workflows/migrations/*` (Persona avatar/skills, PersonaRequest)
- `Backend/orchestration/agent_loop.py` (save_skill meta-tool)

## Open questions for the human

1. Bot username after rename: `kazi`? (Display name "Kazi".)
2. `@admin` handle: literal `@admin`, or the superuser's first name?
3. Avatar: uploaded image per persona, or generated (initials/color) like the
   current ui-avatars fallback? Upload + fallback is the proposal.
