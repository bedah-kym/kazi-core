# Frontend redesign: design specification and handoff

**Date:** 2026-11-12
**Companion to:** [`../2026-11-frontend-redesign.md`](../2026-11-frontend-redesign.md) (the plan: evidence, decisions, issues, open questions).
**Purpose:** everything a new session, or a different model, needs to reproduce the mock-ups and carry the work on without the original conversation.

Read in this order: this file §1 (state of the work), the plan's §1 (decisions), then the
mock-up sources for the screen you are building.

---

## 1. State of the work

- **Nothing is implemented.** The branch holds documents only: the plan, this spec, the
  mock-up sources and the measurement scripts. No template, stylesheet, script, model or
  test has been changed.
- **Approved by the maintainer:** the direction and every decision in the plan's §1; the CI
  screenshot harness (F-01).
- **Not approved yet (ask before touching):** the payments restyle (F-55, protected path);
  moving inline scripts out and any CSP change (F-71).
- **Waiting on the backend owner:** main versus a release branch; the split and default
  limits for removing tiers (F-06); stored data for groups (F-47); memory endpoints (F-48);
  the search index design (F-80); more than one assistant per room (F-84); the voice
  pipeline (F-85); which health checks the web process can expose (F-62).
- **Blocked on v0.8:** the turn renderer (F-41) needs the stream contract document (T-A3).
- **Security items found during the audit** are tracked privately per `SECURITY.md` and are
  not described in these documents. Ask the maintainer for them before starting F-03 or F-85.

### Files in this folder

| Path | What it is |
|---|---|
| `DESIGN-SPEC.md` | This file. |
| `mockups/*.dc.html` | Source of each mock-up screen. Every element carries its exact inline style, so these are the most precise reference for sizes and colours. |
| `mockups/canvas.json` | The canvas index: screen names, sizes and positions. |
| `research/shoot.js` | Playwright: screenshots plus overflow, tap-target and font-size metrics per page. |
| `research/perf.js` | Playwright + CDP: requests, outside requests, transfer, heap per page. |
| `research/voice.js` | Playwright with a fake microphone: timings and reachability for the voice flow. |
| `research/bench.py` | Decrypt-and-scan versus keyed word index on 50,000 synthetic messages. |
| `research/page-metrics-2026-11-11.jsonl` | Output of `shoot.js` for the pages listed in it, at three widths (2026-11-11). |

The mock-up sources are written for a design-canvas runtime (`<x-dc>`, `<sc-for>`,
`{{holes}}`, `./support.js`) and do not render in a plain browser. Read them as annotated
markup: the structure and the inline styles are plain HTML and CSS. Two things in them are
not part of the product: the runtime tags, and the texture URL, which points at the repository file
`Backend/chatbot/static/img/brick-wall.svg` rather than a served address.

### Running the app locally for screenshots (no Docker, no Redis, no Postgres)

Environment variables: `DJANGO_DEBUG=true`, `KAZI_DEV_INMEMORY=1`, `KAZI_DEMO_MODE=true`,
any `DJANGO_SECRET_KEY`, and `DATABASE_URL` pointing at a scratch SQLite file. Then, with
Python 3.12 and `requirements.lock` installed: `manage.py migrate`, create a user, create a
`users.Workspace` for that user with `onboarding_completed=True` (otherwise most pages
redirect to onboarding), and `manage.py runserver 127.0.0.1:8765 --noreload`. The research
scripts assume that address and a user `demo`. Set `localStorage.kazi_tour_seen` before
driving the chat page, or the product tour covers the composer.

## 2. Design philosophy

1. **The chat room is the brand.** Paper, ink and one clay accent; brick texture; tailed
   bubbles; a floating pill composer. Every other page is redesigned to match the chat
   room, never the reverse.
2. **One accent, used sparingly.** Clay marks the primary action, the current location, the
   assistant, and "needs you". If two clay things compete on a screen, one is wrong.
3. **Quiet surfaces, hairline borders, soft warm shadows.** Depth comes from a one-pixel
   line and an ink-tinted shadow, not from colour blocks or gradients. The only gradient is
   the user's own bubble.
4. **One line first, detail on demand.** An action, a step, a result or a memory item is
   one line; expanding shows the rest. Long agent work folds into a single summary line.
5. **Monospace means machine.** Commands, file names, durations, counters, model names and
   keyboard keys use the monospace stack. Everything a person wrote or reads as prose uses
   Inter.
6. **State is always visible and always honest.** Working, waiting on you, done, stopped,
   failed. Nothing spins without a deadline. A success shows what was verified. A failure
   says what happened, what was kept, and what to do next.
7. **The human approves.** Approval is a labelled button, shown only to the person who owns
   the action. Speech never approves. A waiting approval is the most prominent thing on any
   screen it appears on.
8. **Shape carries meaning, colour confirms it.** People are circles; assistants are
   rounded squares. Groups have a name as well as a colour. Status has a word as well as a
   tint. Nothing is distinguishable by colour alone.
9. **Adaptive, not shrunk.** Three layouts (phone, tablet, desktop) with different
   navigation, not one layout scaled. Components size to the space they are given.
10. **Touch-first sizes everywhere.** Controls are at least 44 px; text fields are 16 px so
    phones do not zoom; nothing depends on hover or drag alone.
11. **Light on the machine.** Self-hosted at home: nothing fetched from outside at page
    load, features loaded when used, cheap paint, no always-running animation.
12. **Plain words.** Sentence case. Buttons say what they do ("Approve and send", "Try
    again"). No emoji. Uppercase only for 11 px eyebrow labels.
13. **Inspired, not copied.** The details panel takes its principles (4 and 5) from terminal
    coding tools; its look is Kazi's own palette and components.

## 3. Colour

### 3.1 Core palette (existing, from `kazi-theme.css:15-115`)

| Token | Light | Dark | Use |
|---|---|---|---|
| `--paper` | `#f6f3ec` | `#262624` | Page background |
| `--surface` | `#fcfbf8` | `#30302d` | Sidebars, panels, assistant and others' bubbles |
| `--raised` | `#ffffff` | `#383733` | Cards, inputs, list rows that are selected |
| `--ink` | `#3d3929` | `#edeae2` | Primary text |
| `--ink-soft` | `#6e6a5e` | `#a5a098` | Meta text (times, counts, captions) |
| `--line` | `#e5e0d5` | `#3e3d38` | Hairline borders, dividers |
| `--clay` | `#a83c1e` | `#a83c1e` | Accent fills: primary buttons, active nav, assistant avatar |
| `--clay-strong` | `#8a3016` | n/a | Hover on clay; clay-coloured text on light |
| `--clay-fg` | `#a83c1e` | `#ef8460` | Clay as text or icon on dark |
| `--clay-fg-strong` | `#8a3016` | `#f6a685` | Links on dark |
| `--clay-tint` | `rgba(168,60,30,.12)` | `rgba(168,60,30,.18)` | Tinted buttons, icon squares, "working" pill; lighter washes (`.08`–`.10`) for mentions and running rows |
| `--success-hex` | `#2f855a` | `#63b581` | Success dot and icon |
| `--success-soft` | `rgba(72,150,101,.14)` | `rgba(72,150,101,.22)` | "Verified" strip, "Done" pill |
| `--warning-hex` | `#b57a1f` | `#d9a63c` | Warning |
| `--danger-hex` | `#c85c3f` | `#e5664f` | Danger |

### 3.2 Values the mock-ups add (proposed new tokens)

| Proposed token | Value | Use |
|---|---|---|
| `--ink-2` | `#57513f` | Secondary text and icons, one step stronger than `--ink-soft`; inactive nav and tab text |
| `--line-strong` | `#cfc8b8` | Input borders, dashed outlines, sheet grabber, inactive waveform |
| `--fill-1` | `#efeae0` | Segmented-control track, neutral status pill, neutral icon square |
| `--fill-2` | `#e9e4d8` | Neutral avatar background |
| `--clay-line` | `rgba(168,60,30,.25)` | Border of the assistant bubble and of "needs you" cards |
| `--clay-on-clay` | `#f6c9b6` | The gate stroke of the K mark on a clay background |
| `--bubble-own` | `linear-gradient(135deg, #4a463c, #57513f)` with text `#f6f3ec` | The user's own messages |
| `--success-text` | `#2f6b4a` on light surfaces, `#24573b` on the `--success-soft` | Success text (the dot stays `#2f855a`) |
| `--warning-strong` | `#8a5e10` | Border and icon of the failure card |
| `--mark` | `#f3d9a4` | Search-match highlight behind text |
| `--code-bg` / `--code-fg` / `--code-dim` | `#2f2d28` / `#edeae2` / `#b9b3a6` | Command and output block |
| `--scrim` | `rgba(38,38,36,.40)` to `.45` | Backdrop behind sheets and the palette |

Group colours (a fixed set; each has a fill for the header pill and rail, a tint for
avatars and drop targets, and a text colour for use on the tint):

| Name | Fill | Tint | Text on tint |
|---|---|---|---|
| Olive | `#5f6b3a` | `#e7ead9` (drop target `rgba(95,107,58,.12)`) | `#4a5630` |
| Ochre | `#8a5e10` | `#efe4d0` | `#6b4a0c` (proposed) |
| Blue | `#3f5f7a` | `#dfe7ee` | `#2f4a61` |
| Plum | `#6f4a6a` | `#ecdfe9` | `#5a3a56` |

The same tint and text pairs colour people in team rooms (Amina blue, Brian plum in the
mock-up); a person's colour is stable across the app. White text on every fill above and on
clay passes 4.5:1; `--ink-soft` on `--paper` passes; do not use `--ink-soft` on tints.

### 3.3 Dark mode

Dark mode is a first-class theme on every page, not an inversion of the light one. The
mock-ups draw light mode only; this section is the specification for dark.

**Principles**

1. **Warm charcoal, never black.** The page is `#262624`. Pure black and pure white are not
   used for surfaces or text.
2. **Depth comes from lighter surfaces, not shadows.** Three steps: paper `#262624`, surface
   `#30302d`, raised `#383733`. Anything that sits "above" uses the next step and a hairline.
   Shadows are kept only for things that float over content (menus, sheets, dialogs, the
   dragged row), and are black, not ink-tinted.
3. **One accent, two forms.** Clay as a fill stays `#a83c1e` with white text. Clay as text,
   icon, underline or focus ring becomes the lighter `#ef8460` so it stays readable.
4. **Softer text, same hierarchy.** Primary `#edeae2`, secondary `#c9c4b8`, meta `#a5a098`.
   The three levels match the light theme's three levels one for one.
5. **Tints get stronger, not brighter.** Washes go from 12–14 % to 18–22 % opacity on the
   surface beneath; status and group tints become deep, low-chroma versions of their hue
   with light text.
6. **Borders that controls need are visible.** Decorative hairlines stay subtle (`#3e3d38`);
   the outline of an input, checkbox or dashed target uses `#8a877c`, which holds 3:1
   against all three surfaces.
7. **No glow.** The clay shadow under buttons, the brand mark and the recording bar is
   removed in dark; the fill alone carries them.
8. **Texture stays, quieter.** `brick-wall-dark.svg`, same placement rules as light.
9. **Media is not left blazing.** Images and file previews get a 1 px hairline; the code
   block goes one step darker than the page so it still reads as inset.
10. **Follows the system, remembers the user.** First visit follows the operating-system
    preference; an explicit choice is stored and applied before first paint on every page,
    including sign-in and onboarding. Browser chrome follows (`color-scheme`, `theme-color`).

**Dark values for the tokens in §3.2**

| Token | Dark value | Notes |
|---|---|---|
| `--ink-2` | `#c9c4b8` | 6.9:1 on raised, 8.7:1 on paper |
| `--line-strong` | `#8a877c` | 3.3:1 on raised, 3.7:1 on surface, 4.2:1 on paper |
| `--fill-1` | `#3a3935` | Segmented-control track, neutral pill |
| `--fill-2` | `#44423d` | Neutral avatar |
| `--clay-line` | `rgba(239,132,96,.35)` | Assistant bubble and "needs you" border |
| `--clay-on-clay` | `#f6c9b6` | Unchanged |
| `--bubble-own` | `linear-gradient(135deg, #57513f, #665f4a)`, text `#f6f3ec`, plus a 1 px `rgba(255,255,255,.08)` border | 5.7–7.1:1; lighter than the page so the user's messages still stand forward |
| `--success-text` | `#63b581` on surfaces (5.3:1); `#8fd0a6` on the success wash (5.6:1) | |
| `--danger-text` | `#f0907c` | 5.7:1 on surface; `--danger-hex` `#e5664f` is for icons and fills only (4.0:1 as text) |
| `--warning-strong` | `#d9a63c` | 5.4:1 on raised |
| `--mark` | `#6b5420` behind `--ink` text | 6.0:1 |
| `--code-bg` / `--code-fg` / `--code-dim` | `#1e1e1c` / `#edeae2` / `#a5a098` | 13.9:1 and 6.4:1 |
| `--scrim` | `rgba(0,0,0,.55)` | |
| Floating shadow | `0 12px 32px rgba(0,0,0,.5)`; dialogs `0 24px 64px rgba(0,0,0,.6)` | Replaces every ink-tinted shadow |
| Focus ring | 2 px `#ef8460` | 5.9:1 on paper |

Group and person colours in dark: the fill is unchanged (white text passes on all four);
the tint and its text become:

| Name | Fill | Tint | Text on tint |
|---|---|---|---|
| Olive | `#5f6b3a` | `#3a4226` | `#c5d19a` (6.5:1) |
| Ochre | `#8a5e10` | `#4a3a14` | `#e6c377` (6.5:1) |
| Blue | `#3f5f7a` | `#26384a` | `#a9c6e0` (6.8:1) |
| Plum | `#6f4a6a` | `#3f2c3c` | `#dcb6d6` (7.2:1) |

Measured against the existing dark palette: `--ink` on paper 12.6:1; `--ink-soft` 5.8:1 on
paper, 5.1:1 on surface, 4.6:1 on raised (do not use it on tints); `--clay-fg` 5.9:1 on
paper, 4.6:1 on raised and on the clay wash; links `#f6a685` 6.8:1; white on clay 6.3:1.
Ratios were computed with the WCAG formula, not measured on a rendered page.

**Component notes for dark**

- Assistant bubble: surface fill, `--clay-line` border, no shadow.
- Others' bubbles: surface fill, hairline border.
- Cards, list rows and inputs: raised fill, hairline (inputs: `--line-strong`).
- Selected chip: `--ink` fill with paper-coloured text (it inverts with the theme).
- Translucent bars: `rgba(48,48,45,.90–.96)`.
- Recording bar and current search result: 2 px `#ef8460` border.
- Skeletons: fill-1 to fill-2, no shimmer under reduced motion.

Conversation mode (voice) uses the dark palette regardless of theme: background `#262624`,
card `#30302d`, line `#3e3d38`, text `#edeae2`, meta `#a5a098`, accent text and level bars
`#ef8460`, primary button `#a83c1e`.

## 4. Typography

- **Sans:** Inter (400, 500, 600, 700), self-hosted, then
  `-apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif`.
- **Mono:** `ui-monospace, SFMono-Regular, "Cascadia Code", Menlo, Consolas, monospace`.
- **Base:** 15 px, line-height 1.5, colour `--ink`.

| Role | Size / weight | Notes |
|---|---|---|
| Eyebrow / section label | 11 / 600 | Uppercase, letter-spacing `.1em` (`.12em` on the hero), colour `--ink-soft` or clay |
| Meta | 12 / 400–600 | Times, counts, hints, pills |
| Secondary | 13 / 400–600 | Descriptions, timeline rows, captions |
| Control | 14 / 500–600 | Buttons, tabs, chips |
| Body | 15 / 400 | Messages, paragraphs |
| Input, row title, screen title in header | 16 / 400–600 | Inputs are never below 16 |
| List heading | 20 / 700, tracking `-0.02em` | "Chats" |
| Phone page title | 24 / 700, tracking `-0.02em` | |
| Stat value | 28 / 700, tracking `-0.02em` | |
| Hero title | 30 / 700, tracking `-0.02em`, line-height 1.2 | Dashboard (24 on phone) |
| Conversation-mode captions | 17 (you), 20 (assistant) | |
| Mono | 11–14 | Model names 11; file names, timings 12; code 12 / 1.6; timer 14 / 600 |

## 5. Space, shape, depth, motion

- **Spacing scale (px):** 2, 4, 6, 8, 10, 12, 14, 16, 20, 24, 28, 32. Gaps between sibling
  groups come from `gap`, not margins.
- **Radii:** 999 (pills: buttons, chips, search, group headers, composer hints) · 28–32
  (composer and recording bar) · 20–24 (sheets, dialogs, large cards) · 16 (bubbles, hero)
  · 14 (cards, phone list rows) · 12 (list rows, inner cards, icon squares) · 10 (nav items,
  ledger rows, assistant avatars, small buttons) · 8 (file-type squares) · 5 (the tail
  corner of a bubble) · 11 (the brand squircle) · 50% (people).
- **Bubble corners:** own `16px 16px 5px 16px`; others and assistant `16px 16px 16px 5px`.
- **Shadows:**
  - Card: `0 2px 8px rgba(61,57,41,.05–.08)`
  - Composer: `0 6px 20px rgba(61,57,41,.10)`
  - Own bubble: `0 4px 14px rgba(61,57,41,.22)`
  - Assistant bubble: `0 4px 16px rgba(168,60,30,.10)`
  - Clay button or brand mark: `0 4px 12px rgba(168,60,30,.30–.35)`
  - Recording bar: `0 6px 20px rgba(168,60,30,.20–.22)`
  - Popover: `0 12px 32px rgba(61,57,41,.22)`; dragged card: `0 14px 32px rgba(61,57,41,.28)`
  - Dialog: `0 24px 64px rgba(38,38,36,.35)`; sheet: `0 -8px 32px rgba(38,38,36,.30)`
- **Borders:** 1 px `--line` by default; 2 px clay for focus, the active search field, the
  current search result and the recording bar; dashed `--line-strong` for placeholders,
  unset filters, the "hearing" bubble and waiting states.
- **Translucent bars:** headers `rgba(252,251,248,.90–.92)`, composer `.96`; blur only on
  these small fixed bars.
- **Texture:** `brick-wall.svg`, tiled 96 × 48 px, behind the chat list, conversation,
  hero and standalone pages; never behind text without a surface under it.
- **Motion (existing, keep):** 140–220 ms ease for UI; message entry 300 ms
  `cubic-bezier(.16,1,.3,1)`; hover lift 1–2 px; all motion off under
  `prefers-reduced-motion`.

## 6. Layout

Three ranges, named once and used everywhere: **compact** (phone), **medium** (tablet),
**expanded** (desktop). The mock-ups were drawn at 390, 820 and 1440 px.

| Region | Compact | Medium | Expanded |
|---|---|---|---|
| Navigation | Bottom tab bar: Home, Chats, Automations, Inbox, More; item 52 px high, icon in a 56 × 30 pill, 11 px label | Icon rail 72 px, items 48 × 48, radius 12 | Sidebar 232 px (216 when four columns are open), items 44 px high, radius 10 |
| Chat list | Its own screen; rows 68 px min, avatar 44 | 264 px column | 300–336 px column, rows with 36–40 px avatars |
| Conversation | Full screen, header 60 px, padding 12–16 | Flexible column | Flexible column, header 64 px, padding 20–24 |
| Details panel | Bottom sheet | Side sheet on demand | Docked column 320–348 px (380 for Search), collapsible, state remembered |
| Composer | Min height 56, radius 28, inset 10–14 from edges | Same | Same, inset 24 |

- **Bubble width:** own messages up to about 440 px, assistant up to about 480 px on
  desktop; 86 % and 92 % of the column on a phone.
- **Dashboard:** hero, then a "needs you" strip, then module tiles (3 across on desktop, 2
  on phone), then four stat cards. Page padding 28 × 32 on desktop, 16 on phone.
- **Sidebar anatomy:** brand (36 px squircle + name + workspace), nav list, footer with the
  user and the theme button. Active item: clay fill, white text, 600.

## 7. Components

Sizes are from the mock-ups; states not drawn (hover, focus, disabled) follow §5 and the
focus rule below.

- **Focus:** a 2 px clay ring, offset 2 px, on every interactive element (`:focus-visible`).
- **Buttons** (height 44; 48–52 for the main action on a phone; 36–40 inside dense panels):
  primary: clay fill, white text, 600, pill, clay shadow · secondary: white fill, 1 px
  `--line`, `--ink` text, 500 · ghost: transparent, `--ink-2` text · tinted: `--clay-tint`
  fill, `--clay-strong` text.
- **Icon buttons:** 44 × 44 circle (36–40 inside toolbars and panels); transparent,
  `--ink-2` icon; tinted variant for "new" and pressed states. Always an `aria-label`.
- **Send button:** 44 px clay circle, white filled paper-plane, clay shadow.
- **Fields:** height 44–50, radius 12 (pill for search), 1 px `--line-strong`, white fill,
  16 px text; label above at 14 / 600; search fields have a 16 px magnifier at the left.
- **Chips and filters:** height 30–32, pill. Selected: `--ink` fill with `--paper` text,
  600. Unselected: white with 1 px `--line`. Unset scope: transparent with a dashed border.
  Active scope: the group's fill with white text and a small remove button.
- **Badges and pills:** count badge: clay fill, white 12 / 600, min 20 × 20 · "Needs you":
  clay fill, white 11 / 600 · "Working" / "Waiting" / "Pending": `--clay-tint` with
  `--clay-strong` text · "Done": `--success-soft` with `--success-text` · "Idle": `--fill-1`
  with `--ink-2`.
- **Avatars:** people are circles (34–44 px) in their tint with their text colour and
  initials; the user's own is `#4a463c` with `#f6f3ec`; neutral rooms use `--fill-2`.
  Assistants are rounded squares (radius 10) in clay with white initials or the K mark.
  Stacked avatars overlap by 10 px with a 2 px surface-coloured ring.
- **Brand mark:** SVG `viewBox="5 3 23 26"`, three polygons: `5,3 11,3 11,29 5,29`,
  `11,16 18,16 27,29 20,29` and the gate `15,12 22,12 28,3 21,3`. On clay: white, white,
  `--clay-on-clay`. Sits in a clay squircle (36–40 px, radius 11) or a clay circle.
- **Message bubbles:** padding 12 × 16–18. Own: `--bubble-own`. Others: `--surface`, 1 px
  `--line`. Assistant: `--surface`, 1 px `--clay-line`, clay-tinted shadow, a header row
  with a 22 px clay avatar and the name in `--clay-strong` 13 / 600. Time below at 12 px.
  In team rooms the name sits above the bubble with the time, and an assistant's name is
  followed by a "for <owner>" pill in the owner's tint and text colour.
- **Date separator:** centred pill, 11 / 600 uppercase, `rgba(252,251,248,.9)` with a line
  border.
- **Folded steps:** a 36 px row inside the bubble: chevron, summary text, duration in mono.
  Expanded steps list one per line with a 16 px status icon (green check, clay clock).
- **Verified strip:** `--success-soft`, check icon, "Verified: …" with the target in mono, and
  a "Receipt" link.
- **Approval card:** inside the assistant bubble: white card, radius 12, an eyebrow
  ("SEND EMAIL · NEEDS APPROVAL"), recipient and subject, then Approve and send (primary),
  Edit draft (secondary), Reject (ghost). On a phone the primary is full width, 48 px, with
  the other two beneath. For someone who cannot approve: a dashed `--paper` box with a
  lock icon and "Waiting for <owner> to approve".
- **Chat list row:** avatar; line 1 name (600) and time; line 2 one-line description
  (13 px, `--ink-2`, ellipsis); line 3 persona · model in mono 11, and a state badge at the
  right. Current row: white fill, soft shadow. Grouped rows are indented 8 px with a 3 px
  left rail in the group colour and a `0 12px 12px 0` radius.
- **Section label in lists:** eyebrow style: NEEDS YOU, PINNED, RECENT.
- **Group header:** pill 34 px (40 on phone) in the group fill, white 13 / 600: chevron,
  name, count, optional count badge. Collapsed shows the same with a right chevron.
- **Drag states:** target group gets its tint and a 2 px dashed outline in its colour; an
  insertion line (2 px, with a small ring at the left) shows the landing position; the
  dragged row is white, lifted (dragged-card shadow), rotated −2°, with a dark pill label
  ("Add to School project"); its origin is a dashed empty slot.
- **Tabs (details panel):** text tabs 40 px high, 14 px; selected has a 2 px clay
  underline and 600 weight; a hairline under the row. Tabs: Activity, Notes, Memory,
  Search; team rooms add "Who's here" first.
- **Segmented control:** `--fill-1` track, 4 px padding, pill; selected segment white with
  a small shadow, 600.
- **Plan checklist:** done items struck through in `--ink-soft` with a green check; the
  current item 600 with a 12 px clay ring.
- **Action ledger row:** 40 px: verb (44 px wide, 600), target in mono (ellipsis),
  duration in mono, status icon. Running row: `--clay-tint` background, verb and status in
  clay. Expanded: a white bordered card holding the row, a dark code block (`$ command`
  dimmed, output below) and a footer line with the sandbox note and a Receipt link.
- **Memory item:** white card, radius 10: text, then 36 px edit and forget icon buttons.
  Stale items: dashed border on `--paper`, a mono age line, and Still open / Done / Drop.
- **Search result:** card with a meta line (kind or sender at the left, date at the right)
  and a 14 px snippet with matches in `<mark>`. Current result: 2 px clay border on a faint
  clay wash. In the conversation, the jumped-to message gets a 2 px clay border and a 6 px
  clay halo; a dark strip under the header says where you are and offers "Back to latest".
- **Media grid:** three columns, 6 px gap, square tiles radius 10. Files: a 36 px type
  square, mono name, meta line. Voice: play button, level bars, mono duration, transcript
  line.
- **Command palette:** 720 px wide dialog, radius 20, over a scrim. Input row 18 px text
  with scope chips before the text and an `esc` key hint; a row of dashed scope chips;
  results grouped under eyebrow labels; the selected result has a 2 px clay outline on a
  faint clay wash; a footer of key hints in bordered mono chips and a result count.
- **Bottom sheet:** surface fill, radius `20px 20px 0 0`, a 40 × 4 grabber, a header row
  identifying the item, then 52 px rows; destructive or separate actions under a divider.
- **Roster card (team rooms):** white card with avatar, name, "Acts for <owner> · model",
  and a state pill; the one that is waiting gets a clay-tinted border and a line saying
  what it waits for.
- **Handoff line:** centred pill between bubbles: from-name, arrow, to-name, what was
  handed over.
- **Catch-up marker:** a clay hairline across the conversation with "6 new since you left"
  and a small Summarise button.
- **Recording bar:** takes the composer's place; 2 px clay border, clay shadow, radius
  30–32, min height 60–64. Phone: 48 px Cancel circle, level bars, mono timer in clay, 48 px
  clay Done circle with a check. Desktop: labelled Cancel and Done buttons and a row of key
  hints beneath. Level bars: 3 px wide, 3 px gap, radius 2, clay for recorded, `--line-strong`
  for remaining.
- **"Hearing" bubble:** right-aligned, dashed `--ink-2` border on a faint ink wash, an
  eyebrow "HEARING", the last word dimmed.
- **Review card:** radius 24 surface card in the composer's place: play button and
  duration, an editable 16 px text area labelled "YOUR MESSAGE · TAP TO EDIT", "Send as
  voice note" (secondary) and "Send to Kazi" (primary), and a hands-free checkbox.
- **Failure card:** same shape with a 2 px `--warning-strong` border, a warning icon, a
  one-line reason and "Your recording is safe", the kept clip, then Try again (primary),
  Type it instead and Send as voice note (secondary).
- **Playback strip (spoken reply):** a pill on a faint clay wash at the top of the bubble:
  36 px clay stop or play button, a 4 px progress bar, mono position, a speed chip. The
  words being spoken are highlighted in the text beneath.
- **Conversation mode:** dark; a header with a "Chat" button; captions bottom-aligned
  (yours dim, the assistant's larger); action cards inline; a row of 5 px level bars; a
  status line; three round controls: mute (56), stop (72, clay), end (56).

## 8. Icons

Inline SVG, `viewBox="0 0 24 24"`, `fill="none"`, `stroke="currentColor"`, stroke width 1.8
(2–2.6 for small or emphatic marks), round caps and joins. Decorative icons are
`aria-hidden`; meaningful ones have `role="img"` and a label. No icon font. Paths used:

| Icon | Path data |
|---|---|
| Home | `M3 11l9-8 9 8M5 10v10h14V10` |
| Chat | `M4 5h16v11H9l-5 4z` |
| Automations | `M5 4h5v5H5zM14 15h5v5h-5zM7.5 9v4.5a2 2 0 002 2H14` |
| Inbox | `M4 13l2-8h12l2 8v6H4zM4 13h5a3 3 0 006 0h5` |
| Wallet | `M3 7h16a2 2 0 012 2v9a2 2 0 01-2 2H3zM3 7V5a1 1 0 011-1h13M16 13.5h2` |
| Reminders / waiting | `M12 3a9 9 0 100 18 9 9 0 000-18zM12 7v5l3 2` |
| Travel | `M3 11l18-7-7 18-2.5-7.5z` |
| Settings | `M4 7h10M18 7h2M4 17h2M10 17h10M16 5v4M8 15v4` |
| More (menu) | `M4 7h16M4 12h16M4 17h16` |
| Search | `M11 4a7 7 0 100 14 7 7 0 000-14zM16 16l4 4` |
| Plus | `M12 5v14M5 12h14` |
| Close | `M6 6l12 12M18 6L6 18` |
| Check | `M5 12l5 5 9-10` |
| Back | `M15 5l-7 7 7 7` |
| Chevron down / right / up | `M6 9l6 6 6-6` / `M9 6l6 6-6 6` / `M6 15l6-6 6 6` |
| Arrow right | `M5 12h14M13 6l6 6-6 6` |
| Send (filled) | `M3 11.5l18-8-7 18-3-7z` |
| Attach | `M8 12.5l6-6a3 3 0 014 4l-8 8a5 5 0 01-7-7l7-7` |
| Microphone | `M12 3a3 3 0 00-3 3v5a3 3 0 006 0V6a3 3 0 00-3-3zM6 11a6 6 0 0012 0M12 17v4` |
| Speaker | `M4 9v6h4l5 4V5L8 9zM16 9a4 4 0 010 6M18.5 6.5a8 8 0 010 11` |
| Play (filled) | `M8 5l12 7-12 7z` |
| Stop (filled) | `rect x=6 y=6 width=12 height=12 rx=2` |
| Bell | `M6 16V11a6 6 0 0112 0v5l2 2H4zM10 20a2 2 0 004 0` |
| Moon | `M20 14.5A8 8 0 019.5 4a8 8 0 1010.5 10.5z` |
| Details panel | `M3 5h18v14H3zM15 5v14` (sheet on phone: `M3 5h18v14H3zM3 14h18`) |
| New group (folder) | `M3 7h7l2 2h9v10H3zM12 12v4M10 14h4` |
| Edit | `M4 20h4L19 9l-4-4L4 16zM13 7l4 4` |
| Pin | `M12 17v4M8 4h8l-1 6 3 4H6l3-4z` |
| Archive | `M3 5h18v4H3zM5 9v10h14V9M10 13h4` |
| Lock | `M7 11V8a5 5 0 0110 0v3M5 11h14v9H5z` |
| Warning | `M12 4l9 16H3zM12 10v4M12 17v.01` |
| Bookmark (memory) | `M6 4h12v16l-6-4-6 4z` |

## 9. Mock-up inventory

Sample content throughout is invented: the people (Amina, Brian), the personas (Writer,
Researcher, Analyst, Designer, Copywriter), the rooms, messages, file names and numbers.

| File | Size | Shows |
|---|---|---|
| `Main.dc.html` | 1440 × 900 | Chat, desktop: sidebar, chat list, conversation with an approval card, docked details (mode switch, summary, notes, receipts). First version of the details panel, superseded by `ChatGroups`. |
| `ChatTablet.dc.html` | 820 × 1180 | Chat, tablet: icon rail, chat list, conversation. |
| `ChatPhone.dc.html` | 390 × 844 | Chat, phone: conversation with stacked approval buttons. |
| `ChatsListPhone.dc.html` | 390 × 844 | Chat list as its own screen with the bottom tab bar. First version, superseded by the grouped list. |
| `DashboardDesktop.dc.html` | 1440 × 900 | Dashboard: hero, needs-you strip, six tiles, four stats. |
| `DashboardPhone.dc.html` | 390 × 844 | Dashboard, phone, with the tab bar. |
| `SignInPhone.dc.html` | 390 × 844 | Sign-in on the warm theme. Drawn before the scope changed: it still shows social sign-in and "request an invite"; the invite link is to be replaced by a docs link (F-60). |
| `ChatGroups.dc.html` | 1440 × 900 | Current reference for desktop chat: full sidebar, grouped chat list (Needs you, Pinned, groups, Recent), conversation with folded steps and a verified strip, details panel with tabs and the Activity tab (plan, action ledger). |
| `ChatListDrag.dc.html` | 340 × 900 | Dragging a chat into a group. |
| `DetailsMemory.dc.html` | 360 × 900 | Details panel, Memory tab. |
| `ChatsGroupsPhone.dc.html` | 390 × 844 | Grouped list on a phone with the move-to-group sheet open. |
| `SearchInChat.dc.html` | 1000 × 900 | Search tab with mixed results; conversation jumped to a result. |
| `SearchMedia.dc.html` | 380 × 900 | Search panel filtered to media: images, files, voice. |
| `GlobalSearch.dc.html` | 1000 × 760 | Command palette searching across rooms with scopes. |
| `TeamRoom.dc.html` | 1200 × 900 | Team room: people and owner-tagged assistants, thread summary, handoff line, approval owned by someone else, `@` picker, "Who's here" roster. |
| `VoiceRecordPhone.dc.html` | 390 × 844 | Recording bar and "hearing" bubble. |
| `VoiceReviewPhone.dc.html` | 390 × 844 | Review and send. |
| `VoiceFailedPhone.dc.html` | 390 × 844 | Transcription failure, and the microphone-blocked message. |
| `VoiceConversationPhone.dc.html` | 390 × 844 | Conversation mode. |
| `VoiceDesktop.dc.html` | 1000 × 640 | Desktop recording bar, spoken reply with playback strip, "Speak replies" switch. |

Known gaps in the mock-ups: only light mode is drawn (except conversation mode), so dark
mode is specified in §3.3 but not yet pictured; hover,
focus and disabled states are not drawn; no settings, workflows, notifications, reminders,
travel, onboarding walkthrough or system-status screens exist yet; the screens are fixed
sizes, not responsive pages; none was visually verified after publishing.

## 10. Accessibility rules the designs assume

- Real elements: `button`, `a`, `input` with `label`; never a clickable `div`.
- Text contrast at least 4.5:1, including 11 px labels; 3:1 for text 24 px and larger.
- Targets at least 44 × 44 px; inputs at least 16 px.
- Every drag has a menu equivalent; every hover has a tap and a keyboard equivalent.
- The message list is a log that announces new messages; recording, sending and failure
  are announced.
- Landmarks and one `h1` per page; `aria-current` on the active nav item and room.
- All audio has text; all animation stops under reduced motion.

## 11. Starting a new session

Suggested first message:

> Read `docs/proposals/2026-11-frontend-redesign.md` and
> `docs/proposals/2026-11-frontend-redesign/DESIGN-SPEC.md`, plus `AGENTS.md`. Nothing is implemented yet. Tell me the state of
> the work and what is blocked, then wait for me to choose the next issue.

Rules that carry over from the repository (`AGENTS.md`): protected paths need a written plan
and a human OK; every new test is shown failing first; no new framework or bundler; ask
before touching CI, migrations, CSP or payments.
