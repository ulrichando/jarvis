# Design Tab Overhaul — v1 Spec

Date: 2026-04-29
Status: approved (brainstorming gate)
Owner: Ulrich
Source: brainstorming session 2026-04-29

## Problem

The `/design` route in the JARVIS web UI exists and renders (chat left, files panel center, iframe preview right) but the chat behind it is bound to the generic coding workbench prompt — which scaffolds Vite apps when asked for a slideshow. Output quality is generic. The page has no concept of a brand, no per-format specialization, and no way to export anything other than "open the file in a new tab."

The user is a non-designer (founder/PM persona) who wants to go from idea → polished visual through conversation, then hand the result to investors/team/customers without dropping into Figma or Canva first.

## Goals

- Conversation-first design generation that produces output that looks like a thoughtful designer made it, not generic AI output.
- Persisted brand system (logo, colors, fonts, reference screenshots) auto-applied to every generation in the workspace.
- Per-format specialization: slides ≠ prototype ≠ landing ≠ one-pager ≠ infographic — each with its own playbook.
- Concrete exports: HTML download (already free) + PDF (new).
- Zero new infrastructure: reuse the existing Docker workbench, workspace storage, boltArtifact parser, iframe preview, and multi-LLM chat.

## Non-goals (explicit v2)

- Click-to-edit canvas / inline comments anchored to elements
- AI-generated tweak sliders for spacing/color
- Variant-grid canvas (3-4 designs side-by-side)
- PPTX export, Canva handoff, MP4/animation export
- Org-scoped sharing / multi-user collaboration
- Handoff bundle to a downstream coding agent
- Editing of brand on a per-design basis (one brand per workspace in v1)

## v1 cut decisions

User confirmed in brainstorming:
- v1 = canvas + multi-format generator + brand system + format playbooks + HTML/PDF export.
- v2 = iteration toolkit (sliders, inline comments, direct manipulation) + Canva handoff + animation export.
- Quality bar: "as Claude Design" — meaning the output looks finished, not the UI is identical.

## Architecture

```
[chat composer]
    ↓ POST /api/chat { mode: "design", workspaceId, format, messages }
[/api/chat] composes:
    base system prompt
  + buildDesignPrompt({ format, brand })
  + history
    ↓
[LLM] streams <boltArtifact><boltAction type="file" filePath="<format>.html">…</boltAction></boltArtifact>
    ↓
[message-parser.ts] (existing) → writes file into workspace
    ↓
[DesignFilesPanel] picks up new file → user clicks → [DesignPreview] renders in iframe (existing)

[Brand panel]
    ↓ /api/design/brand?workspaceId=… (GET / PUT)
[brand.json] in `<workspace>/.jarvis/brand.json`

[Export]
    ↓ /api/design/export?workspaceId=…&path=…&format=pdf
[Playwright headless] → navigates to served file URL → page.pdf() → response stream
```

No new database tables. No new services. The brand and export concerns become a small slice of `lib/design/`.

### Files added

- `src/web/src/lib/design/format.ts` — Format type + curated font pairings + utility helpers
- `src/web/src/lib/design/brand.ts` — Brand storage (read/write `brand.json`)
- `src/web/src/lib/design/playbooks.ts` — Per-format prompt builders
- `src/web/src/app/api/design/brand/route.ts` — GET/PUT brand for a workspace
- `src/web/src/app/api/design/export/route.ts` — PDF export via Playwright
- `src/web/src/components/design/format-selector.tsx` — Chip selector above chat
- `src/web/src/components/design/brand-panel.tsx` — Brand upload/edit UI

### Files modified

- `src/web/src/lib/actions/jarvis-prompt.ts` — `buildDesignPrompt()` now accepts `{ format, brand }` and delegates to `playbooks.ts`
- `src/web/src/app/api/chat/route.ts` — accepts `format` in the body, loads brand, passes both to `buildDesignPrompt`
- `src/web/src/components/chat/chat.tsx` — accepts `format` prop, sends it in body
- `src/web/src/components/design/design-view.tsx` — wires format selector + brand panel + export menu

## Format playbooks

A `Format` is one of: `slides | prototype | landing | onepager | infographic`. Each has:

1. **Default file path** — `slides.html`, `prototype.html`, etc.
2. **Canvas dimensions / aspect** — slides `1920×1080`, prototype iPhone `390×844`, landing fluid, one-pager A4 portrait, infographic 1080×1920 (vertical) or print.
3. **Anatomy guidance** — for slides: cover, agenda, content slides, ending slide; for prototype: minimum 3 screens with `data-route` navigation; for landing: hero, sections, footer.
4. **Specific anti-slop rules** — slides forbid the "8-tile feature grid"; landing forbids centered hero with "Welcome to X"; infographic forbids decorative emoji.

Each playbook shares a base block:
- Required `<style>` containing 8pt grid CSS variables (`--space-1: 0.5rem` … `--space-12: 6rem`)
- Required font import from Google Fonts (display + body), pulled from a curated pairing
- Required color variables (`--bg`, `--fg`, `--accent`, `--muted`, `--supporting`)
- "Real content" rule: no lorem ipsum, no "Company X", concrete numbers/names

### Curated font pairings (default catalog)

When no brand is set, the playbook chooses one pairing semi-randomly per generation, biased by format:

| pairing | display | body | bias |
|---|---|---|---|
| editorial | Playfair Display | Inter | infographic, one-pager |
| modern-sans | Bricolage Grotesque | IBM Plex Sans | landing, prototype |
| technical | Space Grotesk | JetBrains Mono | slides (technical), prototype |
| serif-warm | Fraunces | Inter | one-pager, landing |
| editorial-modern | Newsreader | Manrope | slides (corporate), one-pager |

All entries above are available on Google Fonts (no licensing cost, no self-hosting required).

Explicitly excluded as defaults: Roboto, Open Sans, Lato, Montserrat (overused → AI-slop signal). Inter appears only as a body font, never display.

## Brand system

### Storage

Per-workspace file at `<workspace>/.jarvis/brand.json`:

```json
{
  "version": 1,
  "name": "Pretva",
  "logoPath": ".jarvis/brand/logo.svg",
  "colors": {
    "bg": "#0B0B0F",
    "fg": "#F4F4F5",
    "accent": "#FF6A00",
    "muted": "#71717A",
    "supporting": "#27272A"
  },
  "fonts": {
    "display": { "family": "Bricolage Grotesque", "googleFontsUrl": "…" },
    "body":    { "family": "IBM Plex Sans", "googleFontsUrl": "…" }
  },
  "voice": "Confident, concise, founder-direct. Avoid jargon.",
  "references": [
    { "path": ".jarvis/brand/ref-1.png", "note": "App home screen" }
  ]
}
```

Logo and references are stored as files under `<workspace>/.jarvis/brand/`. The path is relative to the workspace root so the existing file-serving route can return them.

### Injection into the prompt

When `buildDesignPrompt({ brand })` runs and `brand` is non-null, it appends a `<brand_system>` block:

```
<brand_system>
  Brand: Pretva. Voice: Confident, concise, founder-direct.
  Use ONLY these tokens:
    --bg: #0B0B0F
    --fg: #F4F4F5
    --accent: #FF6A00
    --muted: #71717A
    --supporting: #27272A
  Fonts (from Google Fonts):
    display: Bricolage Grotesque
    body: IBM Plex Sans
  Logo: <img src="/api/workspace/<id>/file?path=.jarvis/brand/logo.svg&raw=1">
  Place the logo on cover/hero areas only, never as decorative repeat.
</brand_system>
```

When brand is null, the playbook picks a curated pairing + neutral palette (per format) and proceeds. The brand block is the highest-priority guidance — playbook-level color/font defaults are explicitly overridden.

### UI

A "Brand" tab toggle in the design view header swaps the center panel between the file list and a brand editor:
- Logo upload (drag-drop or file picker, stored under `.jarvis/brand/`)
- Color palette (5 hex inputs with live swatches)
- Font picker (Google Fonts autocomplete) — display + body
- Voice textarea (free text, ~200 chars)
- Reference screenshots (drag-drop, multiple)

Save writes `brand.json`. Subsequent chat messages include the brand automatically.

## Canvas + iteration loop

The current layout (chat left, files+preview right) stays. Two additions above the chat composer:

1. **Format chip selector** — `slides | prototype | landing | one-pager | infographic` (default: slides). Sets `format` in the next chat POST.
2. **"Make variants" button** — sends the same user message 3× in parallel with different seeds, dropping `<format>-v1.html`, `<format>-v2.html`, `<format>-v3.html`. The user opens each from the file list in turn. This is the lightweight v1 affordance for design exploration; the full side-by-side variant-grid canvas (Approach C) remains v2. v1 acceptance does NOT require this button — it can be deferred without blocking the rest of v1.

Iteration is conversational: "make slide 3 less wordy", "swap to a warmer palette". Each turn the model rewrites the whole HTML file; existing parser handles it.

## Export pipeline

### HTML

Already free — `/api/workspace/<id>/file?path=<p>&raw=1` streams the file. Add a "Download HTML" item to the export menu that points at this URL.

### PDF

New route `GET /api/design/export?workspaceId=<id>&path=<p>&format=pdf`:

1. Resolve workspace + validate path is under workspace root.
2. Boot a Playwright `chromium` browser (chromium-headless-shell — already used elsewhere).
3. Navigate to `http://localhost:<port>/api/workspace/<id>/file?path=<p>&raw=1` (server-side fetch, internal port).
4. Wait for `networkidle`.
5. `page.pdf({ format: 'Letter' or 'A4' based on format hint, printBackground: true, margin: 0 })`.
6. Stream response with `Content-Type: application/pdf` and `Content-Disposition: attachment; filename="<basename>.pdf"`.
7. Close browser. (No long-lived browser — keep stateless to avoid concurrency issues in v1.)

Page size is inferred from the `Format`:
- `slides` → custom `width: 1920px, height: 1080px` (landscape, single page per slide section)
- `one-pager` → A4 portrait
- `infographic` → custom `width: 1080px, height: 1920px` (matches the format's declared canvas)
- `landing` → Letter portrait, single tall page (`page.pdf({ format: 'Letter', printBackground: true })`)
- `prototype` → custom `width: 390px, height: 844px` (single iPhone-aspect page; v1 PDF only captures the current screen, not the full flow — multi-screen prototype export is v2)

### Export menu

Top bar already has a "Present" button. Replace with a dropdown:
- Open in new tab (current behavior)
- Download HTML
- Download PDF

## Anti-slop guardrails (baked into every playbook)

1. **Typography:** never default to Inter/Roboto/Open Sans/Lato/Montserrat as display. Display must be from the curated catalog or the brand. Body is Inter only when paired with a strong display.
2. **Layout:** asymmetry preferred. Forbid the "8-feature 4×2 grid with emoji icons" pattern. Cover slide and hero must not both be centered text + single CTA.
3. **Color:** use exactly 5 named variables. Forbid the lavender→teal gradient hero. Backgrounds may use one solid + one accent gradient at most.
4. **Content:** no lorem ipsum. No "Company X / Lorem Solutions". If the brief lacks specifics, the model invents specifics that fit the brief and notes them in chat.
5. **Motion:** at most one orchestrated entrance animation per file (page-load stagger). Default easing `cubic-bezier(0.22, 1, 0.36, 1)`. Never `linear` unless intentional.
6. **Imagery:** use `images.unsplash.com` URLs the model knows exist, or skip imagery. Never `placeholder.jpg`.

These rules live in the shared base block of `playbooks.ts` so changing them propagates to every format.

## Testing

### Integration (vitest + Next.js test route)

- `mode: "design", format: "slides"` with a mocked LLM that emits a known boltArtifact → assert `slides.html` lands in the workspace.
- `brand.json` present with `accent: #FF6A00` → assert generated HTML contains `--accent: #FF6A00`.
- `mode: "design"` without `format` → defaults to `slides`.
- Brand PUT → next chat call sees the new brand in the prompt (via inspectable middleware).

### E2E (Playwright)

- Generate a slides file with the deterministic mock LLM, hit `/api/design/export?...&format=pdf`, assert response is `application/pdf`, magic bytes `%PDF`, size > 1KB.
- Brand panel: upload a logo, save, reload page → logo persists.

### Manual visual QA

- A `docs/design-rubric.md` (separate doc, not in this spec): 5 reference prompts × 5 formats = 25 generations. Each scored 1-5 on typography / layout / color / specificity / no-slop. Target: average ≥ 4 across all five axes before declaring v1 ready.

## Risks & open questions

- **Playwright in the Next dev/build environment.** The repo already uses Playwright via the QA tooling; need to confirm `playwright-chromium` is installable in the prod runtime (Docker workbench is separate from the Next.js process). Mitigation: use `playwright-chromium-headless-shell` which is small, or fall back to `puppeteer-core` with a system Chromium binary.
- **Cross-origin font loading in PDF render.** Google Fonts CSS occasionally fails on networkidle wait. Mitigation: explicit `page.evaluateHandle('document.fonts.ready')` before `page.pdf()`.
- **Brand asset path safety.** All brand files must be under `<workspace>/.jarvis/brand/`. The brand API route must validate paths against traversal, same way the existing workspace file route does.
- **LLM compliance with brand tokens.** Models may ignore `--accent` and use a different hex. v1 mitigation: prompt is explicit; v2 could post-process the file and replace any non-token color with the closest token.

## What this is NOT (clarifications)

- Not a fork or adaptation of `huashu-design` (which is a SKILL.md for AI agents — different shape, custom personal-use license, declined to copy in brainstorming).
- Not a clone of Claude Design's UI — same overall feel (chat-left/canvas-right, brand auto-apply, exports) but the architecture, prompts, and components are JARVIS-native.
- Not adding a new top-level dependency on Figma or Canva — those are v2 handoff targets, not v1 platforms.

## Acceptance criteria

v1 ships when:
- [ ] All five formats produce a working file via the chat (mocked + real LLM)
- [ ] Brand panel saves and reloads; brand colors appear in generated HTML
- [ ] PDF export returns a valid PDF for at least slides + one-pager
- [ ] Manual rubric average ≥ 4 across all five axes for at least one format end-to-end
- [ ] No regression in the non-design `/chat` route (existing workbench prompt unchanged)
