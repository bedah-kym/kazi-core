# Kazi brand kit

Status: adopted.

## Where it's used

- App favicons and web manifest: `Backend/chatbot/static/` (`favicon.*`, `icon-*.png`, `site.webmanifest`), linked from `Backend/users/templates/brand/head_icons.html`.
- Inline mark in templates: `Backend/users/templates/brand/kazi_mark.html` (body is `currentColor`, gate is `--kazi-gate` or the `gate` argument).
- Kazi's chat avatar: `Backend/users/static/img/kazi-avatar.svg` (a copy of `kazi-avatar.svg`).
- App colours: `Backend/chatbot/static/css/kazi-theme.css` (`--clay` is Murram; `--clay-fg` is the dark-mode-safe foreground).
- Docs site: `docs/assets/brand/` and `docs/stylesheets/kazi.css`.
- GitHub README header: `kazi-lockup.svg` and `kazi-lockup-reversed.svg`.

When an asset changes here, update its copy in those places too.

## The mark: Lango

*Lango* is Swahili for "gate". The K's upper arm is cut free and lifted, like a barrier arm that a person raises. Kazi does the work, and a human opens the gate before anything risky goes through.

Two other directions were explored and are kept in `concepts/` for reference:

- `b-mikono.svg`: *mikono* means "hands". A steady hand (the stem) next to an open, working hand (the chevron).
- `c-pindo.svg`: *pindo* is the border of a kanga. The K is built from border triangles that tile into a band.

## Files

| Use | File |
|---|---|
| Mark, light backgrounds | `kazi-mark.svg` |
| Mark, dark backgrounds | `kazi-mark-reversed.svg` |
| One colour | `kazi-mark-mono-ink.svg`, `kazi-mark-mono-white.svg` |
| Wordmark | `kazi-wordmark.svg` (ink), `kazi-wordmark-reversed.svg`, `kazi-wordmark-accent.svg` (Murram gate) |
| Horizontal lockup | `kazi-lockup.svg`, `-reversed`, `-mono-ink`, `-mono-white` |
| Chat avatar (circle) | `kazi-avatar.svg` (Murram), `kazi-avatar-ink.svg` |
| Favicons | `favicon/favicon.svg` (switches with `prefers-color-scheme`), `favicon/favicon-16.svg` (redrawn for the 16 px grid), `favicon/favicon.ico` (16/32/48) |
| App icons | `favicon/icon-192.png`, `icon-512.png`, `icon-512-maskable.png`, `apple-touch-icon.png` |
| GitHub social preview | `social-preview.png` (1280×640) |

The wordmark is drawn as shapes, so it needs no font.

## Palette

| Token | Hex | Role | Contrast |
|---|---|---|---|
| Murram 600 | `#A83C1E` | Primary on light | 5.6:1 on Stone, 6.3:1 on white |
| Murram 300 | `#EF8460` | Primary on dark | 7.0:1 on Ink |
| Sisal | `#C7871C` | Secondary, for graphics and highlights only | 5.9:1 on Ink, 2.7:1 on Stone (not for text on light) |
| Ink | `#1B1613` | Text and dark surfaces | 16.1:1 on Stone |
| Stone | `#F5F2ED` | Light surfaces and text on dark | |
| Dust | `#6B6159` / `#A69C92` | Muted text, light / dark | 5.4:1 on Stone / 6.7:1 on Ink |

Ratios are WCAG 2.x relative-luminance contrast. Murram is deliberately deeper and redder than the current app accent (`#D97757` in `claude-theme.css`), which sits in the same orange family Anthropic uses for Claude.

## Type

- Wordmark: custom shapes (in the SVGs).
- Docs headings: **Bricolage Grotesque**. Body: **IBM Plex Sans**. Code: **IBM Plex Mono**. All three are on Google Fonts under the SIL Open Font License.

## Merchandise

- Print in flat spot colours (screen print, vinyl, embroidery), never gradients. Match Murram to a physical swatch from the printer.
- Smallest safe sizes: 20 mm printed, 45 mm embroidered, 25 mm for an enamel pin. Below that the gate gap fills in.
- Dark garments take the reversed mark. Murram or busy fabric takes a one-colour version.
- Keep the copy short: "You approve. Kazi does the work." or "Karibu". Avoid "Hapa kazi tu" and "Kazi iendelee", which are tied to Tanzanian political campaigns.

## Usage

- **Clear space:** keep a margin of at least the stem width on every side of the mark. For the lockup, keep at least the height of the wordmark's "i" dot ×2.
- **Minimum size:** mark 16 px (use `favicon-16.svg` at 16 px), lockup 80 px wide, avatar 24 px.
- **Two colours, two roles:** the body is Ink (or Stone on dark). Only the gate takes Murram.
- **Don't:**
  - close the gap between the gate and the rest of the K;
  - recolour the body Murram and the gate Ink;
  - add outlines, gradients, shadows or a robot;
  - set "kazi" in a font instead of using the wordmark file;
  - put the full-colour mark on Murram or on a busy photo (use a one-colour version).
