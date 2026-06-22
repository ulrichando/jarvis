# Computer Use — "Mission Control" redesign

**Date:** 2026-06-22
**Status:** Design approved (visual direction); pending spec review → implementation plan
**Surface:** `src/web/` (web app, `/computer-use`)
**Author:** brainstormed with Ulrich via the visual companion

## 1. Goal

The current `/computer-use` page works but reads like a developer tool:
mono-uppercase labels, a row of six tiny bordered buttons, a cramped 380px
chat. The ask: make it **enterprise-grade** — trustworthy, observable, and
visually on par with the rest of JARVIS.

The approved direction is **Mission Control**: the live desktop becomes the
hero, the right panel becomes an **auditable activity timeline** (every agent
step is a row with status + timestamp + a screenshot thumbnail), and the
command input docks full-width along the bottom. Same capability — Jarvis
driving the user's live X11 desktop via the `:8771` sidecar — re-presented for
trust and clarity.

## 2. Scope

**In scope (all within `src/web/`):**

- Full visual + structural rebuild of `src/app/(app)/computer-use/page.tsx`
  into the four-region Mission Control layout.
- Decompose the monolithic page into focused components (see §5).
- Extend `components/computer-use/novnc-view.tsx` with an imperative
  `snapshot()` handle so the page can grab per-step thumbnails from the canvas.
- Client-side enrichment of the existing event stream: arrival timestamps,
  derived step status, canvas-snapshot thumbnails.

**Out of scope (explicitly NOT touched):**

- The `:8771` computer-use sidecar and its SSE event shapes. No new event
  types, no structured action metadata, no server-emitted screenshots. The
  redesign consumes the **existing** stream as-is.
- `/api/computer-use` + `/api/computer-use/approve` route logic (status probe,
  SSE proxy, approve POST) — unchanged.
- The voice-agent `tools/computer_use.py` path (separate feature).
- Auth/proxy (`proxy.ts` same-origin carve-out) — unchanged.

**Why out:** the sidecar lives outside `src/web` and is shared with the
multi-provider loop; changing its contract is a separate, higher-risk effort.
Keeping the revamp frontend-only means zero cross-tree regression surface and a
self-contained, shippable change.

## 3. Current state (reference)

- `page.tsx` (621 lines): two-pane flex — desktop (`flex-1`, noVNC) + 380px
  conversation aside; a 48px mono-uppercase header with `HeaderBtn` cluster;
  `ChatBubble` / `PermissionCard` / `ModelPicker` / `ThinkingDots` helpers.
- `novnc-view.tsx` (104 lines): dynamic-imports `@novnc/novnc`, renders the
  RFB canvas into a container div, exposes `onState`, live `viewOnly` toggle.
- Existing SSE events (from sidecar, proxied verbatim): `start`, `text`,
  `action` (`{summary}`), `permission_request` (`{id,label,summary}`),
  `blocked`, `denied`, `ping`, `done`, `error`.

## 4. Target design — four regions

### 4.1 App bar (~52px)
- **Left:** cyan desktop glyph + "Computer Use" (proper case, ~14.5px/560).
- **Status cluster:** `● Connected` chip (semantic dot: green connected /
  amber connecting / neutral offline) + a mono `session · 9f3a…c12` chip
  (short `sessionId`) for auditability.
- **Right controls:** a **segmented Supervised / Auto** control (replaces the
  toggle button); a single **Take control** button; an overflow `⋯` menu
  (Connect/Disconnect, New session, Refresh). While the agent is running, a
  danger-tinted **Stop** is shown in the cluster (today's Refresh slot becomes
  Stop, matching current behavior); **Take control** stays available and, as
  today, pauses the agent before handing over.
- Rationale: collapses six equal-weight bordered buttons into a clear primary
  → secondary → overflow hierarchy.

### 4.2 Desktop stage (hero, `flex-1`)
- The noVNC stream framed like a real window: a 34px chrome strip
  (traffic-light dots, "Live desktop", mono `1920 × 1080`), rounded border,
  soft drop shadow.
- **Floating glass control overlay** (bottom-center): when the agent runs,
  shows `● Jarvis is working` + a **Take control** pill. When the user has
  taken over, the overlay/frame border turns cyan and reads
  `You're in control — Give control`.
- **States** (carried over, restyled into the frame):
  - ready + connected → live canvas.
  - disconnected → centered "Reconnect" card.
  - not-ready → services checklist (VNC :6080, sidecar :8771) + `hint` block.

### 4.3 Activity timeline (right, ~400px) — the centerpiece
- Header: "Activity" + a `● Working` chip while running + a `N steps · m:ss`
  counter.
- Entries render from the conversation model:
  - **Task block** (user message): small "TASK" eyebrow + the instruction, in a
    card.
  - **Reasoning** (`text` part): muted paragraph with a left hairline rule.
  - **Step** (`action` part): a status icon on a connecting vertical rail
    (done ✓ / running ◌), the action text (the event `summary`), an optional
    mono param line (rendered only if present — see §6), a timestamp, and an
    optional **screenshot thumbnail** (64×40) captured at arrival.
  - **Permission** (`permission_request`): prominent cyan-bordered inline card,
    Approve / For session / Deny (wired to `/api/computer-use/approve`,
    unchanged); resolves in place.
  - **Done** (`done`): green check summary row.
  - **Blocked / Error**: danger-tinted rows.
- **Empty state:** calm "Ready" copy + the restyled example prompts.
- Faithfulness note: each `action` event is an already-completed step, so steps
  render as done ✓; the *live* indicator is a trailing "Working…" row while
  `running` and no `done` has arrived. (The mockup's per-step spinner is
  illustrative; we will not fake a started/finished sub-state the stream does
  not emit.)

### 4.4 Command bar (full-width footer)
- Spans the full width under both panes. Model chip (with `NATIVE` badge) +
  large input ("Tell Jarvis what to do on the desktop…") + cyan send.
- Keyboard/mode hints row (`Enter` send · `⇧Enter` newline · current mode).
- Disabled/placeholder states preserved (running, takeover, not-ready).

## 5. Component architecture

Split the 621-line page into focused, independently-readable units under
`src/components/computer-use/`:

| File | Responsibility | Depends on |
|---|---|---|
| `page.tsx` (orchestrator) | state (status, thread, session, model, flags), SSE read loop, handlers, region layout | all below |
| `app-bar.tsx` | app bar: brand, status/session chips, segmented mode, Take control, overflow, Stop | `DropdownMenu` |
| `desktop-stage.tsx` | framed desktop + chrome + overlay + the 3 stage states | `novnc-view` |
| `activity-timeline.tsx` | Activity header + entry list rendering (Task/Reason/Step/Permission/Done/Blocked) | `Markdown` |
| `permission-card.tsx` | inline approve/deny card | — |
| `command-bar.tsx` | full-width footer input + model picker | `DropdownMenu`, `model-picker` |
| `model-picker.tsx` | the scoped CU model dropdown (extracted as-is) | `DropdownMenu` |

(All under `src/components/computer-use/` — the directory namespaces them, so no
`cu-` prefix.)
| `novnc-view.tsx` | existing; **+ `forwardRef` exposing `snapshot(maxW?): string \| null`** | `@novnc/novnc` |

Each component takes plain props and holds no cross-cutting state, so it can be
read and reasoned about on its own. `page.tsx` keeps the data ownership.

## 6. Data model & flow

- Extend the `Part` type with two client-set, optional fields:
  - `ts?: number` — `Date.now()` captured when the event is parsed.
  - `thumb?: string` — a downscaled JPEG dataURL captured from the noVNC canvas
    at the moment an `action` event arrives (best-effort; omitted on failure).
- **Thumbnail capture:** `novnc-view` exposes `snapshot(maxW=128)` via
  `useImperativeHandle`, which finds the RFB `<canvas>` in its container and
  returns `canvas.toDataURL('image/jpeg', 0.5)` scaled to `maxW`. The canvas is
  painted from WebSocket pixel data (not a cross-origin `<img>`), so it is
  **not tainted** and `toDataURL` succeeds. If the canvas is missing or the
  call throws, return `null` → the step renders without a thumbnail.
- **Timestamp / param line:** `ts` formats client-side (`HH:MM:SS`). The mono
  param line is rendered **only if** the event `summary` already carries
  structured detail; we do not invent coordinates. Richer structured action
  metadata is a future enhancement that would require a sidecar contract change
  (see §10), explicitly out of scope here.
- No change to how the SSE stream is read, framed (`\n\n` split,
  `data:` parse), or to the approve POST.

## 7. Theming

Implement with the **existing Tailwind/CSS-variable tokens** (`bg-background`,
`bg-card`, `border-border`, `text-foreground`, `text-muted-foreground`,
`text-primary`, `bg-primary`, `text-destructive`, etc.) — **not** hardcoded
oklch. The mockup's literal colors were preview-only. This keeps the page
correct in both light and dark themes and consistent with the rest of the app
(refined-minimalism within the existing claude.ai-parity system, not a
divergent palette).

## 8. Accessibility & motion

- Real `<button>`s with `aria-label`/`title`; the segmented control is a
  radio-group semantics (`role="radiogroup"` / `aria-checked`).
- Respect `prefers-reduced-motion` (already imported via `useReducedMotion`):
  gate the pulse/spinner/step-reveal animations.
- Step reveals use a short staggered fade (motion), bounded and subtle.
- Focus-visible rings on all controls; the command input keeps its focus ring.

## 9. Testing & verification

- Extract pure helpers into a testable module
  (`lib/computer-use/timeline.ts`): the event→`Part` mapping and `HH:MM:SS`
  formatting. Add **vitest** unit tests for them (run from `src/web`).
- `npx tsc --noEmit` clean for all touched files.
- `npm run build` (Turbopack) compiles the route.
- Manual: dev server at `127.0.0.1:3000/computer-use`, exercise connected /
  running / permission / takeover / disconnected / not-ready states; Ulrich
  verifies against screenshots (blind-edit workflow).

## 10. Risks & future work

- **Thumbnail timing:** the canvas snapshot reflects the screen *at event
  arrival*, which may slightly lead/lag the action it depicts. Acceptable for
  an audit thumbnail; documented, not load-bearing.
- **Param line / true per-step status / step replay:** all require the sidecar
  to emit structured action metadata (kind, coordinates, server-side
  screenshot, started/finished). That is a separate spec touching the `:8771`
  service; intentionally deferred so this change stays frontend-only.
- **noVNC canvas access** is via querying the RFB-injected `<canvas>`; if a
  future `@novnc/novnc` upgrade changes its DOM, `snapshot()` degrades to
  `null` (thumbnails simply disappear) — no functional break.
