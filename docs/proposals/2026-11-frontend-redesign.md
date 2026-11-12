# Proposed release: one shell, every device (frontend redesign)

**Date:** 2026-11-12
**Status:** draft — not filed. Proposed as its own release after v0.8; the version number is the maintainer's call.
**Basis labels:** `[Evidence]` = counted or observed in the 2026-11-11 audit (code audit + a local run screenshotted at 390, 820 and 1440 px); `[Analysis]` = design judgment; `[Known]` = general platform knowledge not re-checked for this document.

The design philosophy, colour and type values, component specifications, icon paths, mock-up
sources and measurement scripts are in
[`2026-11-frontend-redesign/DESIGN-SPEC.md`](2026-11-frontend-redesign/DESIGN-SPEC.md).

This document is issue-ready. Section 5 is the issue set; each has an executable acceptance
check. Section 7 lists what we refuse. No code is written until an issue is picked up.

---

## 0. Why

The chat room has a clear identity (paper, ink and clay; brick texture; tailed bubbles; pill
composer). The rest of the platform does not share it, and none of it was engineered for
phones.

1. **Three visual systems.** Chat and app pages use the warm theme; landing, login, register
   and pricing are a separate black-and-white design written inline (landing: 706 lines of
   inline CSS); onboarding borrows a third, blue-grey token set from `context-panel.css`.
   `[Evidence]`
2. **The theme is a patch layer.** `kazi-theme.css` loads last and overrides an older blue
   `chatbase.css`. 140 selectors are defined in more than one file; the shadcn-style colour
   tokens are defined three times with different values; 139 `!important`. `[Evidence]`
3. **Responsive behaviour is accidental.** 12 distinct breakpoint widths; the mobile chat
   drawer is defined four times with conflicting rules and two open mechanisms; about 20
   `100vh` sites; no container queries, no `clamp()`. `[Evidence]`
4. **Touch is broken in measurable ways** (390 px): chat has 25 of 40 tap targets under
   40 px; settings 75 of 104, plus 26 inputs under 16 px (iOS zooms on focus); tables in
   settings and transactions run off-screen; message actions are hover-only. `[Evidence]`
5. **Two navigation systems.** App pages have a sidebar; in chat the site navigation is
   inside a "More" dropdown. Marketing has a third. `[Evidence]`
6. **Dead weight and risk.** `app.css` is linked by no template; `chatbot.html` is rendered
   by no view; jQuery is loaded and unused; `emoji-mart@latest` and `marked` are unpinned
   CDN scripts. `[Evidence]` The audit also raised security items; they are tracked
   privately per `SECURITY.md` and are not described here.
7. **The chat client does not know what a turn is.** v0.8 persisted turns
   (`orchestration/turns.py`) and their endings; no JS file reads a turn id, a turn notice or
   a partial flag. The client still appends chunks, which is the "narration glued to the
   answer" failure the v0.8 proposal names. `[Evidence]`

8. **Pages are heavy and depend on the internet.** Measured 2026-11-12 on the development
   server at 390 px, cold cache (local files uncompressed, so local sizes are upper bounds):

   | Page | Requests | To outside servers | Transfer | Scripts | Fonts |
   |---|---|---|---|---|---|
   | Chat | 49 | 20 (5 hosts) | 993 KB | 397 KB | 279 KB |
   | Dashboard | 10 | 4 (2 hosts) | 303 KB | 37 KB | 148 KB |
   | Settings | 19 | 13 (3 hosts) | 483 KB | 49 KB | 254 KB |

   Outside hosts: cdnjs, jsdelivr, code.jquery.com, unpkg, assets.calendly.com,
   ui-avatars.com. Most font weight is the icon font. A home install with no internet, or a
   slow link, stalls on these. `[Evidence]`

## 0.1 Who this is for

Kazi is a harness people run at home, not a hosted commercial service. The marketing pages
are removed: the published docs now do that job. Pricing, plan tiers and the trial flow are
removed too: a self-hoster owns the install and sets its limits. Invites stay, so an owner
can add family or teammates. Social sign-in and payments stay in the codebase for anyone who
wants to commercialise their own install, or for a later decision; they are not the priority. The priority is a UI that is light on memory, quick to respond and fast on
modest hardware.

## 1. Decisions already made (2026-11-12, maintainer)

- Keep the chat room's look and theme; redesign everything else to match it.
- No new frameworks: Django templates, CSS and vanilla JS only.
- Bootstrap and Font Awesome are removed, last (Phase 7).
- Auth, onboarding and the remaining standalone pages move to the warm theme.
- Phones get a bottom tab bar; tablets an icon rail; desktops the full sidebar.
- In scope beyond the original brief: command palette, installable app (no push), composer
  upgrades, collapsible tool timeline with receipt links, page transitions.
- The chat renderer waits for, or moves with, the v0.8 stream contract. No rush.
- The Django templates in this repo are the product UI; there is no separate frontend to
  keep in step.
- One release: all eight phases ship together. Phases still merge one at a time (§7).
- Supported browsers: the last two versions of Chrome, Safari, Firefox and Edge.
- Home use first: performance and low memory are requirements, not polish (§4.5). The
  remaining commercial-era pages are kept, restyled last, and never slow down the core pages.
- The marketing pages and the pricing page are deleted (F-05); the docs replace them.
- No plan tiers. Rate limits and quotas become one set of defaults the owner can change
  through configuration (F-06). The trial flow is removed; plain invites are kept.
- Onboarding becomes a guided first task in chat, not a form wizard (F-61).
- The chat list sorts by last activity, shows a description, persona, model and state on
  every row, and supports pins and named, coloured, collapsible groups in the manner of
  browser tab groups (F-46, F-47). Groups are visual organisation only in this release.
- The details panel becomes tabs: Activity, Notes and Memory (plus Search, and Who's here
  in team rooms), taking its principles
  (one line per action, detail on demand, monospace only for machine text) from terminal
  coding tools without copying any one of them (F-48).
- Search covers all history, not only what is on screen, and covers media: voice by
  transcript, files by name and extracted text, images by name and a generated description.
  In-chat results open in a Search tab of the details panel; one palette searches across
  rooms with `in:`, `from:`, `with:` and `has:` scopes (F-81, F-82).
- Search runs on a keyed word index written when a message is saved, not on decrypting
  history at query time (F-80). Built-in full-text search over readable words was rejected
  because it would undo the at-rest encryption.
- Team rooms show who is speaking and for whom: each assistant carries its owner, only the
  owner can approve its actions, long agent runs fold into threads, and handoffs, catch-up
  and queue state are visible (F-83). Each person may bring their own assistant into a
  shared room (F-84).
- Voice is rebuilt around talking to the assistant: speech becomes editable text in the
  composer and is sent as a message. Sending the audio itself as a voice note is the
  secondary path. Recording replaces the composer in place; failures keep the audio and
  offer retry; spoken replies are a per-room switch and stop when the user starts talking;
  phones get a conversation mode with captions. Approvals always need a tap, never voice
  alone. Transcription and speech both go through the provider-agnostic route (F-85–F-89).
- Dark mode is a first-class theme on every page, including sign-in, onboarding and the
  remaining standalone pages: designed, not inverted, and held to the same contrast and
  screenshot checks as light (F-14; values in the design spec §3.3).
- At desktop width the full navigation sidebar stays while a chat is open; the details panel
  is collapsible, and its open or closed state is remembered per user (F-40, F-48).
- Settings gains a read-only system status section (F-62); keys stay in the environment file.
- Performance target: a Raspberry-Pi-class server opened from a mid-range phone a few years
  old. Nothing is fetched from outside servers at page load; an outside service loads only
  when its feature is opened.
- Workspaces on a former paid or trial tier move to the same defaults as everyone else.
- The installable app (F-72) stays as the last, optional item and is documented as needing
  HTTPS or localhost.
- `/` opens the dashboard (sign-in first if signed out).
- The existing JS is type-checked in CI through JSDoc types; no TypeScript rewrite, no build
  step.
- Approved: the CI screenshot harness (F-01). Not yet approved: the payments restyle (F-55)
  and the CSP/inline-script change (F-71); each still needs a plan and a human OK.

Reference designs: twenty-three mock-up screens, inventoried in the design spec §9, with their
sources under `2026-11-frontend-redesign/mockups/`. They are fixed-size pictures, not the
responsive page.

## 2. What we keep

- Palette and dark palette as defined in `kazi-theme.css:15-115`; `brick-wall.svg`; the K mark.
- Bubble shapes (16 px, 5 px tail corner), the olive gradient on own messages, the
  clay-outlined Kazi bubble, the pill composer and round clay send button, pill toolbars,
  ink-tinted shadows, existing motion and the global reduced-motion rule.
- Every id, class and data attribute the JS and tests depend on, until the module that reads
  it is itself replaced (to be inventoried in F-01 from the JS that queries them; see also `chatbot/test_branding.py`, `users/tests.py`).
- Server-rendered pages. No client-side router, no build step.

## 3. Coupling with v0.8 (turns that own the truth)

| v0.8 item | Frontend consequence | Our issue |
|---|---|---|
| T-A1 turn states (shipped) | A reply is one unit with a visible state: queued, working, waiting on you, done, stopped | F-41 |
| T-A2 typed stream, `seq`, one terminal frame | Renderer consumes typed events; narration and answer are separate channels | F-41 |
| T-A3 client contract doc + adversarial tests | The contract is our interface; our renderer tests reuse its fixtures | F-41 |
| Turn queue cap (`TURN_QUEUE_MAX`, default 3) | Composer shows queued count and a full-queue state | F-41 |
| T-B1/T-B2 verification and write-set audit | "Done" shows what was verified, linked to its receipt; failed/partial endings look different from success | F-44 |
| T-B4 missed reminders | "Missed" is a digest entry in Inbox, not a late alert | F-53 |
| T-C1 commitments | Inbox is the one surface for open commitments, with done/drop | F-52 |
| T-C2 proactive policy | Quiet hours, daily cap and "stop suggesting this" in settings | F-51 |

Phases 0–3 do not depend on v0.8. F-40 (chat layout) does not either. F-41 is blocked on the
T-A3 contract; F-44's verified-effects slot on T-B2; F-51 and F-52's new controls on Phase C, F-53's on T-B4.
Each blocked issue ships its layout first and its v0.8 behaviour when the backend lands.

## 4. Target architecture

### 4.1 CSS

- **One token file**: colour (light/dark), spacing scale, fluid type scale, radius, shadow,
  motion, stacking order, layout sizes. Everything else references tokens.
- **Cascade layers** in a declared order (reset, tokens, base, layout, components, pages,
  utilities) so precedence never depends on file order or `!important`.
- **Three layout ranges**, named once: compact (phone), medium (tablet), expanded (desktop).
  Shell layout switches on these; components adapt with container queries to the space they
  are given, not to the screen.
- **Viewport correctness**: dynamic viewport units, safe-area insets, `viewport-fit=cover`,
  inputs at 16 px or more, tap targets at 44 px or more.
- **One dark-mode mechanism** (`data-theme` on the root), applied before first paint,
  defaulting to the system preference.

### 4.2 Templates

- One base template for chat and app pages; one for auth and the other standalone pages. Shared partials for
  head, navigation, and each kit component.
- The kit: button, icon button, card, pill, field, tabs, menu, dialog, sheet, toast, avatar,
  empty state, skeleton, responsive table, approval card. Each is one include plus one CSS
  block, replacing 7 button, 8+ badge and 3 toast implementations.

### 4.3 JavaScript

- Small classic-script modules behind one namespace; no bundler.
- One overlay module (dialog, sheet, drawer, menu): focus trap, Escape, scroll lock,
  `aria-expanded`, return focus.
- Chat: transport (socket + reconnect) → frame adapter → turn store → renderer. The adapter
  is the only code that knows the wire format, so v1 frames today and v2 frames later are a
  one-file change.
- All message text enters the DOM through one sanitising path.

### 4.4 Shell

| Range | Navigation | Chat room | App pages |
|---|---|---|---|
| Compact | Bottom tab bar (Home, Chats, Automations, Inbox, More) | One pane: chats list → conversation; details as a bottom sheet | Single column; tables become stacked cards |
| Medium | Icon rail | Room list + conversation; details as a side sheet | Content with rail |
| Expanded | Full sidebar | Room list + conversation + docked details column | Content with sidebar |

### 4.5 Performance and memory

- **Nothing from outside at page load.** Every script, style, font and icon is served by the
  install itself. A feature that needs an outside service (Calendly) loads it only when the
  user opens that feature. `[Analysis]`
- **Pay for a feature when it is used.** Voice waveform, emoji picker, code highlighter,
  product tour, image cropper and maps load on first use, not on every page view.
- **Icons as inline SVG**, not a 150–280 KB icon font.
- **Long conversations stay cheap.** Only messages near the viewport keep their full DOM;
  older ones are released and restored on scroll. One socket for the open room, not one per
  room visited.
- **Cheap paint.** Blur and large shadows only on small, static surfaces; no always-running
  background animation; animate transform and opacity only.
- **Static files cached hard**: hashed filenames (already in place via the manifest storage)
  with long cache lifetimes, so a second visit transfers almost nothing.
- **Budgets enforced by the harness (F-04)**, set against a Raspberry-Pi-class server
  and a mid-range phone a few years old:

  | Budget | Chat | Other app pages |
  |---|---|---|
  | Requests to outside servers at load | 0 | 0 |
  | Requests at load (cold) | ≤ 15 | ≤ 8 |
  | Transfer at load (cold, compressed) | ≤ 250 KB | ≤ 120 KB |
  | Script at load | ≤ 90 KB | ≤ 30 KB |
  | JS heap after 500 messages | within 2× of heap after 50 | — |
  | Long tasks while streaming a reply | none over 50 ms | — |

  The numbers are starting proposals; F-04 measures what the first phases achieve and fixes
  them as a shrink-only baseline.

## 5. Issues

### Phase 0 — Safety net (`F-0x`)

**F-01 — Real-template visual and metric harness** `[Analysis]` *(touches CI: ask first)*
- Context: the Playwright suite loads a fixture, not real templates, at one desktop size, and
  is not run in CI.
- Build: seed a demo user and room; drive the real pages at 390, 820, 1280 and 1440; store
  baseline screenshots; record per-page metrics (elements past the right edge, tap targets
  under 44 px, inputs under 16 px, text under 12 px).
- Acceptance: the harness fails on today's code for the known defects (settings table
  overflow at 390; chat tap targets); budgets are recorded as a shrink-only baseline.

**F-02 — Remove dead weight** `[Evidence]`
- Build: delete `app.css`, `chatbot.html`, the jQuery tag, the duplicate
  `reconnecting-websocket` load, the duplicated search block (`chatbase.css:1149-1325`), the
  mis-pasted `#searchToggle.active` rule and classes used by no template or JS.
- Acceptance: F-01 screenshots unchanged; a test asserts no template references the removed
  files; `diagnose_features.py` updated or removed with reason.

**F-03 — Third-party scripts and message rendering** `[Evidence]` *(security note in PR)*
- Build: pin and self-host marked, DOMPurify, highlight.js, emoji picker, wavesurfer,
  driver.js; route all message text through the one sanitising path of §4.3; narrow the
  DOMPurify allow-list (drop `style`). Related security fixes are tracked privately.
- Acceptance: a test sends a message and a voice transcript containing markup and asserts it
  renders as text (shown failing first); no script tag points at an unpinned URL.

**F-04 — Performance budgets in the harness** `[Evidence]`
- Context: §0 item 8; no performance measurement exists today.
- Build: per page, record request count, outside-server requests, transfer, script weight
  and, for chat, heap growth over a seeded long conversation and long tasks during a
  replayed stream; compare against §4.5.
- Acceptance: the check fails on today's chat page (20 outside requests); baseline is
  shrink-only; a PR that adds a CDN tag fails.

**F-05 — Remove the marketing pages** `[Evidence]`
- Context: the docs site replaces them. Eight pages: landing, why Kazi, how it works,
  playbooks, workflows library, updates, trust, pricing. Footprint: their templates, their
  routes in `users/urls.py`, the root route in `Backend/urls.py`, their views in
  `users/views.py`, and the links between them. No test references them. Pricing can only go
  once F-06 removes the trial-expiry redirect that points at it.
- Build: delete the templates, views and routes; repoint any remaining link (sign-in,
  register, trial apply) at the docs or remove it; ship with F-33.
- Acceptance: each removed path returns 404 (test shown failing first); no template
  contains a link to a removed route; the full suite passes.

**F-06 — Remove plan tiers and the trial flow** `[Evidence]` *(backend-owned; model fields need a migration: protected, plan in `docs/plans/` and human OK first)*
- Context: `Workspace.plan` (free, trial, pro, agency) defaults to `free` and drives real
  limits for a self-hoster: API rate limits (`Api/throttling.py:29-60`), upload quotas
  (`chatbot/message_actions.py:242-252`, `:360-369`) and moderation gating
  (`users/models.py` `should_moderate`). `TrialExpiryMiddleware` locks out an expired
  trial and redirects to pricing.
- Build, UI side (this release): drop the plan step from onboarding, the plan label in the
  sidebar, upgrade wording in settings and messages, the trial application page and the
  trial applications admin page.
- Build, backend side (with the backend owner): one set of default limits read from
  configuration, owner-adjustable, replacing the per-plan tables; moderation, nudges and
  suggestions governed by their existing per-workspace switches alone; remove the trial
  routes, views and expiry middleware; keep `send_platform_invite` and plain invites. Leave
  the `plan` and `trial_*` columns in place until a planned migration removes them.
- Acceptance: a fresh install's first user gets the configured default limits, not the
  former free-tier ones (test shown failing first); changing the configured limit changes
  the enforced limit; onboarding no longer reads a plan from the request; no template mentions a
  plan, price, trial or upgrade; invite tests pass unchanged.

### Phase 1 — Foundations (`F-1x`)

**F-10 — Tokens, layers and one theme mechanism** `[Analysis]`
- Build: the token file and layer order of §4.1; remove the duplicate token sets in
  `context-panel.css` and the legacy blue set in `chatbase.css`; define the two used-but-undefined
  tokens (`--border-color`, `--input-bg`) or remove their uses; theme applied before paint.
- Acceptance: F-01 screenshots within tolerance in light and dark; no flash of light theme
  for a dark-mode user (asserted by a first-paint screenshot).

**F-11 — Type and spacing scale; self-hosted Inter** `[Evidence]`
- Context: the theme names Inter but chat and app pages never load it.
- Acceptance: Inter is actually loaded on chat, dashboard and login (`document.fonts.check`
  and a same-origin font request; the computed family name alone already reads "Inter" today
  and proves nothing); distinct font-size values drop from 60 to the scale.

**F-12 — CSS ratchet** `[Analysis]`
- Build: a check script in the style of `check_boundaries.py`: counts raw colours outside
  the token file, `!important`, `100vh`, and media queries using widths outside the three
  ranges; baseline may only shrink.
- Acceptance: adding a raw hex to a component file fails the check.

**F-13 — Type-check the JS in place** `[Analysis]` *(adds a CI step: covered by the F-01 approval only if run in the same job; otherwise ask)*
- Build: the TypeScript compiler as a development-only dependency in `e2e/`, run in
  check-only mode over the static JS; JSDoc types on module boundaries; one declaration file
  for the stream frames and turn states, kept in step with the v0.8 contract doc. Start with
  new and rewritten modules; legacy files join as they are touched.
- Acceptance: a deliberate type error in a checked module fails the check; nothing is
  emitted and the shipped files are byte-identical to the sources.

**F-14 — Dark mode as a first-class theme** `[Evidence]`
- Context: a dark token set exists (`kazi-theme.css:74-115`) and is toggled in chat and the
  app shell, but selectors mix `[data-theme="dark"]`, `body.dark-mode` and `.dark-mode`;
  several files hard-code light colours (`ai-assistant.css` and `message-actions.css` use no
  tokens at all); sign-in, register and onboarding have no dark styles; the theme is applied
  after load, so a dark-mode user sees a light flash; the system preference is ignored.
- Build: the dark values of the design spec §3.3 for every token, including the ones the
  redesign adds; one selector (`data-theme` on the root); elevation by surface step with
  shadows only on floating layers; a separate, lighter clay for text and focus; visible
  control borders; `color-scheme` and `theme-color` set per theme; the theme applied before
  first paint and defaulting to the system preference; the F-12 ratchet extended to fail on
  a colour that has a light value and no dark one.
- Acceptance: every page in the F-01 harness is captured in both themes; an automated
  contrast check passes in dark on every page (text 4.5:1, large text and control
  boundaries 3:1); no element renders with a light-theme hard-coded colour in dark
  (asserted by scanning computed backgrounds for the light palette); a first-paint
  screenshot for a stored dark preference shows no light frame; with no stored preference
  the page follows `prefers-color-scheme`.

### Phase 2 — Component kit (`F-2x`)

**F-20 — Kit includes and reference page** `[Analysis]`
- Build: the components of §4.2 as includes; a development-only page that renders every
  component in every state, used by F-01.
- Acceptance: reference page screenshots at all four widths, light and dark; each component
  meets 4.5:1 text contrast and 44 px targets (measured by the harness).

**F-21 — Overlay module** `[Evidence]`
- Context: only Bootstrap modals manage focus; the context panel, both drawers, the upload
  modal and the action modal do not.
- Acceptance: Playwright tests for open, Tab cycle, Escape, backdrop click and focus return,
  for dialog, sheet, drawer and menu.

### Phase 3 — Shell (`F-3x`)

**F-30 — One base template and navigation** `[Analysis]`
- Build: §4.4 for chat and app pages from the existing `nav_sections`; Inbox as one item for
  approvals and notifications; fix the off-canvas sidebar shadow visible on phones.
- Acceptance: the same navigation markup on chat and dashboard; at 390 the tab bar is
  present and no drawer shadow is visible; branding and dashboard tests still pass.

**F-33 — Dashboard as the home page** `[Analysis]`
- Build: `/` redirects to the dashboard, which sends a signed-out visitor to sign-in. Ships
  with F-05, since removing the landing page leaves `/` with no view.
- Acceptance: tests for signed-in `/` → dashboard and signed-out `/` → sign-in.

**F-31 — Command palette** `[Analysis]`
- Build: Cmd/Ctrl+K opens a palette over rooms, pages and actions, built on F-21.
- Acceptance: keyboard-only test: open, type, arrow, Enter navigates; Escape restores focus.

**F-32 — Page transitions** `[Known]`
- Build: cross-document view transitions between app pages; no effect where unsupported or
  under reduced motion.
- Acceptance: navigation works with the feature disabled; reduced-motion run shows no animation.

### Phase 4 — Chat room (`F-4x`) — moves with v0.8

**F-40 — Layout** `[Evidence]`
- Build: grid shell for room list, conversation and details; details docked at expanded,
  sheet below; composer tracks the on-screen keyboard and safe areas; bubbles sized by
  readable line length; one drawer definition.
- Acceptance: at 390 with the keyboard open the composer and last message are visible; no
  `100vh` remains in chat CSS; existing chat hooks unchanged.

**F-41 — Turn renderer behind a frame adapter** `[Analysis]` *(blocked on T-A3 contract)*
- Build: §4.3 chat pipeline; turn states visible; narration in a collapsible timeline,
  answer in the bubble; failed, cancelled, superseded and expired endings with a reason and
  marked partial text; queued count in the composer; reconnect resumes by turn and sequence.
- Acceptance: the T-A3 adversarial fixtures (duplicate, reordered, dropped, disconnect,
  cancel race) replayed against the renderer yield exactly one bubble and one terminal state
  per turn.

**F-42 — Reading and touch** `[Evidence]`
- Build: message actions reachable by touch and keyboard; "jump to latest" with unread
  count; position held when history loads; per-room drafts.
- Acceptance: Playwright touch test opens actions without hover; loading history does not
  move the first visible message.

**F-43 — Composer** `[Analysis]`
- Build: drag-and-drop and paste for files; a `/` menu for skills and routines.
- Acceptance: paste and drop tests reach the existing upload endpoint; `/` menu is keyboard-operable.

**F-44 — Approval card and verified effects** `[Analysis]` *(effects slot blocked on T-B2)*
- Build: one approval card used in chat, dashboard and Inbox; resolving in one place updates
  the others; a slot under a completed turn listing verified effects with receipt links.
- Acceptance: approving in chat removes the item from an open Inbox page without reload.

**F-45 — Accessibility** `[Evidence]`
- Build: landmarks and a heading in chat; the message list as a log with polite
  announcements; labels on every control; `aria-current` updated on room switch.
- Acceptance: an automated accessibility scan of chat reports no critical issues; a
  screen-reader-oriented test asserts new messages are announced once.

**F-46 — Chat list rows and ordering** `[Evidence]`
- Context: the chat view lists rooms in no useful order and each row shows only a name. The
  dashboard's `list_rooms` already orders by `last_message_at`; the chat view does not.
  `Chatroom` has no description field; the room summary shown in the details panel exists.
- Build: sections in order Needs you, Pinned, groups, Recent; within each, most recent
  activity first. Each row: name, one-line description (the room summary unless the user has
  written their own), persona, model, time, and a state badge from the turn states (working,
  needs you, stopped). Search covers names, descriptions and group names.
- Acceptance: sending a message in a room moves it to the top of its section without a
  reload; a room with a pending approval appears under Needs you; rows meet the F-01 budgets
  at 390.

**F-47 — Groups, pins and manual order** `[Analysis]` *(stored data is backend-owned and needs a migration: protected, plan and human OK first)*
- Build, data (with the backend owner): per-user groups (name, colour, position, collapsed),
  a room's group and position within it, pinned flag, and a user-written description.
  Per user, so two members of a room can organise it differently.
- Build, UI: drag a chat onto another to create a group, into or out of a group, and to
  reorder; drag groups to reorder. A collapsed group shows name, count and one badge if
  anything inside needs attention. Group actions: new chat in group (choose persona and
  model), rename, recolour, archive all. Every drag action is also available from a menu
  (long-press, right-click or keyboard): move to group, new group, pin, edit, archive.
  Colours come from a fixed set in the token file and never carry meaning alone.
- Acceptance: order, group membership and collapsed state survive a reload and appear on a
  second device; a keyboard-only test moves a chat into a group and reorders it; a touch
  test does the same through the sheet; dragging never fires a navigation.
- Not in this release: grouped rooms sharing notes or memory. Revisit after groups are in use.

**F-48 — Details panel: Activity, Notes, Memory** `[Analysis]`
- Context: the panel is a stack of cards that floats over the conversation.
- Build: a header with the room's persona, model and approval mode; three tabs.
  *Activity*: pending approvals pinned at the top; the current plan as a checklist; a ledger
  with one line per action (verb, target, duration, status) that expands to show input,
  output and a receipt link; commands, paths and timings in monospace.
  *Notes*: the existing notes, on kit components.
  *Memory*: what the assistant holds for this room, grouped as facts and preferences, each
  with edit and forget; stale items in their own section, marked as not used in replies,
  with still-open, done and drop.
- Depends on: plan steps and action events from the stream (F-41); memory read, edit and
  forget endpoints, which do not exist in `chatbot/urls.py` today (backend); stale handling
  from T-C3 and done/drop from T-C1. Each tab ships with what the backend offers at the time.
- Acceptance: an action in the ledger opens the same receipt as the bubble's receipt link;
  forgetting a memory item removes it from the next prompt (asserted on the built context,
  shown failing first); the panel is a docked column at expanded width and a sheet below it,
  and is fully keyboard-operable.

### Phase 4b — Search and team rooms (`F-8x`)

**F-80 — Keyed word index for search** `[Evidence]` *(backend-owned; new stored data and key handling: security design, plan and human OK first)*
- Context: `search.js` filters only the messages loaded in the page; no server search
  exists. Message bodies are encrypted per room (AES-GCM), so the database cannot text-search
  them. Benchmark, 2026-11-12, 50,000 synthetic messages of about 400 bytes on a 4-core Xeon
  with hardware AES: decrypting and scanning every message took 90 ms with the messages
  already in memory (a best case; it grows with history, repeats per search and per room,
  and will be slower on a home server); a keyed-hash word index answered one- and two-word
  queries in about 0.3 ms including decrypting the 20 newest hits, at about 0.15 ms of
  hashing per message on save and 48 MB of index for 22 MB of ciphertext before any trimming.
- Build: on save, derive a per-room search key from the room key; tokenise the plain text
  the server already holds; store (room, keyed hash of word, message id, source). Sources:
  message text, voice transcript, extracted document text, image description, action and
  receipt summaries. Skip very common and very short words; store hashed prefixes of three
  to six letters for search-as-you-type. Query in three stages: plain-column filters (room,
  group, sender, date, type, has-file); index lookup, newest first; decrypt only the page of
  results being shown. Cross-room search hashes the query once per room the user belongs
  to. A low-priority background job indexes existing history in small batches.
- Acceptance: a word that appears only in a message older than the loaded page is found
  (shown failing on today's search); a user who has left a room gets no results from it; a
  database dump contains no readable message words (asserted on the index table); search
  latency and index size are recorded on the reference hardware and added to §4.5; the
  leak the index does have (which messages share a word, and word frequency) is written
  down in the security docs.

**F-81 — Search inside a chat, with a media view** `[Evidence]`
- Build: a Search tab in the details panel (F-48). Results list sender or kind, date and a
  highlighted snippet, newest first, across messages, files, voice notes and actions.
  Choosing a result loads the conversation around that message, marks it, and shows a
  "Back to latest" strip. Filter chips: all, messages, media, actions, from, date. Media
  view with Images, Files, Voice and Links; a voice result plays from the matching moment.
  Previous and next step through results; the old in-page filter is removed.
- Depends on: F-80; a way to load history around a given message id.
- Acceptance: Playwright: search a term from old history, open the result, the message is
  in view and marked, "Back to latest" returns to the newest message; keyboard-only run
  covers the same path; at 390 the tab works as a sheet.

**F-82 — Search across rooms** `[Analysis]`
- Build: the command palette (F-31) and the chat-list search box become one search over
  room names, descriptions, groups, people, personas, messages, files and memory, grouped by
  kind. Scopes typed or picked as chips: `in:<group or room>`, `from:<person>`,
  `with:<persona>`, `has:<file|image|voice|link|approval>`. Before typing: recent searches
  and groups as one-tap scopes.
- Depends on: F-80, F-46, F-47; memory endpoints (F-48) for memory results.
- Acceptance: results never include a room the user is not in; each scope narrows results
  as specified (one test per scope); arrow keys and Enter open a result; Escape restores
  focus to where it was.

**F-83 — Team rooms** `[Evidence]`
- Context: with more than one human in a room the assistant answers only when addressed by
  its wake word; personas belong to a human and cannot exceed that person's access; handoffs
  are recorded as visible. The interface shows almost none of this.
- Build: a name and a stable colour per person; assistants visually distinct from people by
  shape as well as colour, each tagged with the owner it acts for. `@` opens a picker of
  assistants and people, and the composer shows who the message is addressed to. A "Who's
  here" tab in the details panel: people with presence; assistants with owner, model, state
  and what each is waiting on. An approval card shows its button only to the owner; everyone
  else sees who it is waiting for. A long agent run folds into a thread with a summary in
  the room. Handoffs between assistants appear as one line. A marker at the first unread
  message with a count and a Summarise action. A line above the composer when the addressed
  assistant is busy with someone else's request.
- Depends on: F-41 for turn state; F-84 for more than one assistant per room.
- Acceptance: a second user's browser shows no approve control for an action owned by the
  first (and the server refuses the attempt: test on the endpoint, not only the page);
  people and assistants are distinguishable in a greyscale screenshot; the picker is fully
  keyboard-operable; the catch-up marker appears only when there are unread messages.

**F-84 — More than one assistant in a room** `[Analysis]` *(backend-owned; touches turn routing and approvals: plan first)*
- Context: a room has one bot identity today. F-83's mock-up assumes each member can bring
  a persona they own into a shared room.
- Build (with the backend owner): persona membership per room; addressing by persona name;
  turns, budgets, approvals and receipts attributed to the persona's owner; queueing rules
  when several people address the same assistant.
- Acceptance: a persona invoked in a shared room can use only its owner's access (existing
  persona bound tests extended to a shared room); an approval can be resolved only by the
  owner; receipts name the owner and the persona.
- Until this lands, F-83 ships against the single assistant and shows its owner as the
  person who addressed it.

### Phase 4c — Voice (`F-85`–`F-89`)

Measured 2026-11-12 in Chromium with a fake microphone, real templates, development server:

| Step | Phone 390 | Desktop 1440 |
|---|---|---|
| Tap mic → recording bar visible | 75 ms | 72 ms |
| Stop button reachable while recording | no (covered by the bar) | yes |
| Cancel button reachable while recording | yes | no |
| Stop → voice bubble appears | 34 ms | 27 ms |
| Transcript 8 s later | "Transcribing…" | "Transcribing…" |
| Mic button size | 44 px | 38 px |
| Recording state announced to assistive tech | no | no |
| Microphone permission denied | browser `alert()` | same |

Latency is fine; the flow is broken. `[Evidence]`

**F-85 — Voice pipeline that works, on any provider** `[Evidence]` *(backend-owned)*
- Context: the client sends audio as base64 over the socket (`voice_message`); that handler
  saves and broadcasts the message but never starts transcription, which only the unused
  REST endpoint does. So no voice note is ever transcribed and none reaches the assistant,
  while the client shows "Transcribing…" and "Kazi is thinking" indefinitely. History does
  not carry the voice fields, so a voice note renders as "[Voice Message]" text after a
  reload. Transcription is hard-coded to OpenAI `whisper-1` and the automatic spoken reply
  to OpenAI `tts-1`, although `tts_connector.py` is provider-agnostic. The blob is always
  labelled `audio/webm`.
- Build: one upload path (HTTP, with progress, real content type and extension from the
  recorder); transcription started on that path, through a provider setting that mirrors
  the speech connector (any OpenAI-compatible endpoint, including a local one); explicit
  states on the message (uploading, transcribing, ready, failed) with a deadline, never an
  open-ended wait; voice fields in history; spoken replies through the speech connector
  only, the hard-coded task removed.
- Acceptance: a voice message sent from the page is transcribed with the provider mocked
  (shown failing first on the socket path); a provider outage ends in `failed` within the
  deadline and the audio is kept; after reload the voice message still has its player; no
  code path names a speech vendor outside the provider table.

**F-86 — Recording bar** `[Evidence]`
- Build: recording replaces the composer in place: Cancel, live level, timer, Done, all
  always reachable. Phone: hold to talk, release to finish, slide left to cancel, slide up
  to lock; tap also works. Desktop: click, or a shortcut to start, Enter to finish, Escape
  to cancel. The text field is not disabled by a failed start. Microphone blocked is
  explained in place with a link to instructions, no `alert()`. The product tour never
  covers the composer. State changes are announced.
- Acceptance: at 390 and 1440, Cancel and Done are both hit-testable while recording (the
  check that fails today); keyboard-only start, finish and cancel; with permission denied
  no dialog opens and the inline message is shown; targets are 44 px or more.

**F-87 — Review, send and failure** `[Analysis]`
- Build: live words while speaking where the provider streams them, otherwise on finish.
  After finishing, the transcript sits in the composer as editable text with the clip
  attached: Send to the assistant (text is the message, audio kept as an attachment), Send
  as voice note, or discard. A hands-free setting sends after a short silence. Failure
  keeps the clip and offers Try again, Type it instead, Send as voice note.
- Depends on: F-85; F-41 for the resulting turn.
- Acceptance: an edited transcript is what the assistant receives (asserted on the queued
  turn); with hands-free off nothing is sent without an explicit action; the failure state
  appears within the deadline and Try again reuses the same clip without re-recording.

**F-88 — Spoken replies** `[Analysis]`
- Build: a per-room "Speak replies" switch, remembered per user. A spoken reply always
  shows its text, with a playback strip: stop, position, speed. Speech starts only after a
  user gesture in that tab and stops when the user starts recording, taps stop, or leaves
  the room. Sentences are synthesised as they complete so speech starts before the reply
  ends. Voice messages in history get the same themed player.
- Depends on: F-85; sentence boundaries from the stream (F-41).
- Acceptance: with the switch off no audio is requested; starting to record stops playback
  within 200 ms; time from first reply text to first audio is recorded and added to §4.5;
  the player is operable by keyboard and has a text alternative.

**F-89 — Conversation mode on phones** `[Analysis]`
- Build: a full-height view for back-and-forth talking: captions for both sides, a level
  indicator that says who is speaking, mute, stop, end, and a way back to the chat.
  Everything said is saved to the room as ordinary messages. Action cards appear inline;
  an action that needs approval shows a button and says that speech will not approve it.
- Acceptance: a spoken "yes" while an approval is pending does not resolve it (asserted on
  the approval record); ending the mode leaves a normal transcript in the room; the view
  works with reduced motion and keeps the screen awake only while active.

### Phase 5 — App pages (`F-5x`), one family per PR

- **F-50 Dashboard**: needs-you strip, even tile grid, empty and loading states.
- **F-51 Settings**: kit fields, sections as tabs that fit a phone, save feedback; nudge
  controls when T-C2 lands.
- **F-52 Workflows and Inbox**: tables to cards at compact; commitments view when T-C1 lands.
- **F-53 Notifications and reminders**: one list pattern; "missed" digest when T-B4 lands.
- **F-54 Travel**.
- **F-55 Payments** *(protected path: plan in `docs/plans/` and human OK first)*: kit
  components only, no behaviour change; fix the Wallet page titled "Dashboard".
- Acceptance for each: F-01 budgets at zero for that family at all four widths; the family's
  existing Django tests pass unchanged; no Bootstrap component classes remain in its templates.

### Phase 6 — Auth, onboarding and remaining standalone pages (`F-6x`)

**F-60** — Move login, register, logout and chat export onto the standalone base, tokens
and kit; sign-in links to the docs, not to "request an invite". Acceptance: no inline
`<style>` blocks remain; each passes the harness at 390.

**F-61 — Onboarding as a guided first task** `[Analysis]`
- Context: today's onboarding is a five-step form (account type, profile, workspace name,
  plan, integrations) written for a SaaS signup.
- Build: a new user lands in a chat room with a workspace already created under a default
  name (editable in settings). A short, skippable walkthrough teaches the harness by using
  it: ask Kazi for something, watch the steps, approve an action, open its receipt. It ends
  with links to the docs for self-hosting setup, channels and skills. Replaces the current
  wizard and the separate product tour; loads only for users who have not finished it.
- Acceptance: a new user reaches a working chat without filling any form; skipping marks
  onboarding complete; the walkthrough is fully keyboard-operable and works at 390; a user
  who finished it never loads its script again.

**F-62 — System status in settings** `[Analysis]`
- Context: onboarding no longer covers setup, and `kazi doctor` is command-line only.
- Build: a read-only section in settings, visible to the owner, showing whether the
  database, cache, background worker, sandbox and model provider are reachable, each with a
  link to the matching docs fix. It reuses the checks behind `kazi doctor`; it never shows or
  accepts a secret. The first-task walkthrough links to it.
- Acceptance: with the worker stopped, the section reports it as down (test shown failing
  first); a non-owner cannot open it; no key or token value appears in the response.

### Phase 7 — Remove the scaffolding (`F-7x`)

- **F-70 Remove Bootstrap and the icon font**: native dialog/popover via F-21; inline SVG
  icon partial. Acceptance: no request to the Bootstrap or Font Awesome CDNs on any page.
- **F-71 Inline scripts to files** *(touches CSP in settings: ask first)*: no inline
  `<script>` or `on*=` handlers. Acceptance: all pages work under the CSP as written, with a
  test that loads each page with CSP enforced and asserts zero violations.
- **F-72 Installable app**: service worker for the static shell and an offline notice; no
  push. Acceptance: offline reload shows the notice, not a browser error; the worker never
  caches authenticated HTML or API responses.

## 6. Build order

1. F-01 → F-04 → F-02 → F-06 → F-05 (with F-33) → F-03.
2. F-10 → F-11 → F-12 → F-13 → F-14. Every later issue ships both themes.
3. F-20 → F-21.
4. F-30, then F-31 and F-32.
5. F-40, F-42, F-43, F-44 (approval card), F-45, F-46 as the kit and shell allow; F-47 once its stored data
   exists; F-41, F-48 and F-44's effects slot when the v0.8 contract and verifier exist.
   F-80 as soon as its plan is approved, since F-81 and F-82 wait on it; F-83 after F-41,
   with F-84 following the backend's schedule.
   F-85 first among the voice issues, then F-86 → F-87 → F-88 → F-89.
6. F-50 → F-55, one family per PR.
7. F-60 → F-61 → F-62.
8. F-70 → F-71 → F-72.

Every PR: one issue, harness screenshots before and after in the description, tests shown
failing without the change.

## 7. Anti-patterns (what we refuse)

- **No framework, bundler or CSS preprocessor.** Not Tailwind, not a component library.
- **No new override file on top of the old ones.** Each migrated selector is deleted from
  its old home in the same PR.
- **No redesign of the chat identity.** Section 2 is fixed.
- **No big-bang branch.** Each phase merges on its own and leaves every page working.
- **No renderer written against today's chunk frames as if they were the contract.**
- **No hover-only or drag-only interaction** without a touch and keyboard equivalent.
- **No weakening an existing test to fit new markup** without the reason in the PR.

## 8. Risks and open questions

- **Protected paths.** `Backend/payments/*` is protected, and the rule covers the nested
  payments templates (its `*` also matches `/`, and CODEOWNERS covers the directory). F-55 and F-71 are not yet approved.
- **Browser floor.** Layers, container queries, dynamic viewport units, dialog and popover
  are assumed available in the last two versions of the four supported browsers `[Known]`;
  F-01 confirms this against current support data before F-10 starts.
- **Is the CSP enforced in production?** Unknown to the maintainer as of 2026-11-12. As
  written it has no allowance for inline scripts, which the templates use. F-01 adds a step
  that reads the response headers of the deployed app and records the answer; F-71's scope
  depends on it.
- **One release means a long-lived gap between first merge and release.** Phases merge to
  main as they land, so main carries a half-migrated UI for a while; the release is cut when
  Phase 7 closes. If main must stay releasable throughout, this needs a release branch.
- **Installable app on a home network.** Service workers need a secure context `[Known]`;
  an install reached over plain `http://` on a LAN address will not get F-72. Kept as the
  last, optional item; drop it if the HTTPS requirement makes it rare in practice.
- **F-06 crosses into the backend.** Limits, quotas, middleware and model fields are not
  frontend work; the backend owner should confirm the split, the default limit values and
  when the column-removing migration happens.
- **Search index details for the backend owner.** Where the index lives on the lite profile
  (SQLite) versus Postgres; how much trimming common words saves; whether prefixes are worth
  their storage; how room-key rotation re-keys the index; and real numbers on Pi-class
  hardware, which the benchmark does not give.
- **Image descriptions and document text.** Searching images and files needs a description
  or extracted text stored at upload. Generating a description costs a model call per image;
  it should be optional and off where no vision-capable model is configured.
- **"Ask about this chat".** A plain-language question answered with links to messages is
  deliberately left out until exact search is solid.
- **Speech on a home server.** Local transcription and speech cost CPU and memory that a
  Pi-class machine may not have. The owner should be able to point each at a local server,
  a remote one, or turn it off; the interface hides the mic or the switch when its service
  is not configured. Realistic latency targets need measuring on reference hardware.
- **Live words while speaking** depend on a transcription service that streams. Where it
  does not, the transcript appears on finish; the design must feel right in both cases.
- **Phone browsers.** Recording format, autoplay and background behaviour differ on iOS
  Safari; none of that was tested here (the measurements used Chromium only).
- **Wake word and always-listening** are out of scope for this release.
- **Groups and memory need new backend surface.** F-47 needs stored per-user organisation;
  F-48's Memory tab needs read, edit and forget endpoints. Both are for the backend owner to
  scope.
- **F-62 reads backend health.** Which checks `kazi doctor` can expose to the web process,
  and at what cost on a small server, is for the backend owner to confirm.
- **Release naming.** The version number is the maintainer's call.
- **Screenshot baselines in CI** need a pinned browser and fonts to stay stable.
