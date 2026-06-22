# Web `/artifacts` gallery — design

**Date:** 2026-06-22
**Scope:** `src/web/` only
**Status:** approved-in-brainstorm, spec for review

## Problem

The web app's `/artifacts` route is a dead `FeatureLanding` "coming soon" stub.
Meanwhile two pieces of skeleton already exist but are disconnected:

1. The claude.ai-style DB tables `artifacts` + `artifactVersions`
   (`src/web/src/lib/db/schema.ts`) — `kind ∈ code|markdown|html|react|svg|mermaid`,
   per-version content — are **completely unwired**: zero inserts, zero selects,
   no API route.
2. The only artifacts that actually get produced are **bolt.diy-style multi-file
   build artifacts** (`<boltArtifact>`/`<boltAction>` XML, parsed by
   `StreamingMessageParser`, rendered inline by `components/chat/artifact-panel.tsx`).
   These are embedded in persisted assistant message content in Postgres
   `web.messages`. They are NOT saved to the `artifacts` table.

Goal: turn `/artifacts` into a real, claude.ai-style **gallery/library** of
artifacts, populated both retroactively (backfill from chat history) and going
forward (a real save path on artifact generation).

Out of scope for this spec: changing the in-chat artifact *display*
(`artifact-panel.tsx` stays as-is) and the bolt build → `/workbench` flow.

## Decisions (locked in brainstorm)

- **Target:** the `/artifacts` gallery route (not the in-chat side-panel rebuild).
- **Data source:** BOTH — backfill existing chat history AND add a real save path.
- **Multi-file storage:** small additive DB migration — nullable `files jsonb`
  column on `artifactVersions`.
- **Also:** remove the `customize` feature from the provider feature list / nav
  (separate, unrelated follow-up bundled at the end).

## Data model

Reuse existing tables; one additive migration.

```
artifacts
  id              uuid pk
  conversationId  uuid -> conversations (cascade)
  slug            text          -- stable key within a conversation (from title)
  title           text
  kind            enum(code|markdown|html|react|svg|mermaid)
  createdAt       timestamptz

artifactVersions
  id          uuid pk
  artifactId  uuid -> artifacts (cascade)
  version     int           -- 1-based, increments per re-emit
  content     text          -- the previewable entry payload (e.g. index.html,
                            --   or the single-file payload for single-file kinds)
  language    text          -- mirrors kind for the code viewer
  files       jsonb NULL    -- NEW. [{ path, content }, …] full bundle for
                            --   multi-file builds; NULL for single-file artifacts
  messageId   uuid -> messages (set null)
  createdAt   timestamptz
```

**Identity / versioning:** an artifact is keyed by `(conversationId, slug)` where
`slug = slugify(title)`. Re-emitting the same title in the same conversation
appends a new `artifactVersions` row (`version = max+1`) instead of creating a new
artifact. This gives claude.ai-style version history.

**Kind inference** (from the action set of one `<boltArtifact>`):
- exactly one `file` action, `.html`/`.htm` → `html`
- exactly one `file`, `.svg` → `svg`
- exactly one `file`, `.md`/`.markdown` → `markdown`
- single file whose body is a React component (`.jsx`/`.tsx` exporting a
  component) → `react`
- any file containing a fenced ```mermaid block as its whole payload → `mermaid`
- otherwise → `code` (covers multi-file app builds)

**content vs files:**
- single-file kinds (html/svg/markdown/single code) → `content` = that file's body,
  `files` = NULL.
- multi-file (`code`, multi-file react) → `files` = full bundle; `content` = the
  "primary" file chosen by precedence: `index.html` → `App.tsx`/`App.jsx` →
  `main.*` → first file. Used as the preview/thumbnail source.

## Components

### 1. Shared artifact-from-actions helper — `src/web/src/lib/artifacts/from-actions.ts`
Pure function. Input: `{ title, actions: TrackedAction[] | Action[] }`.
Output: `{ slug, title, kind, content, language, files }` ready to persist.
Single source of truth for kind inference + primary-file selection, used by BOTH
the save path and the backfill so they can never diverge. Unit-tested.

### 2. Save path — `POST /api/artifacts`  (`src/web/src/app/api/artifacts/route.ts`)
- `runtime = "nodejs"`. Guards `if (!db) 503`.
- Body: `{ conversationId, title, actions }` (actions = the completed
  file/shell/start actions for one artifact).
- Auth/ownership: `getUserId(req.headers)`; verify the `conversationId` belongs to
  that user (join `conversations.userId`) before writing — else 404.
- Builds the row via `from-actions.ts`, upserts `artifacts` on `(conversationId,
  slug)`, appends the next `artifactVersions` row.
- Idempotency: if the newest existing version's `content`+`files` are byte-identical
  to the incoming one, skip (don't create a duplicate version on re-render/replay).

**Call site:** `components/chat/chat.tsx` already has an `onArtifactClose` /
artifact-completion path (the same place that finalizes the in-chat card). After an
artifact's actions all reach a terminal state, fire a fire-and-forget
`fetch("/api/artifacts", …)`. No UI change in chat; purely a persistence side effect.
Failures are swallowed + logged (never block the chat turn).

### 3. Backfill — `src/web/scripts/backfill-artifacts.ts`
- Standalone node script (run via the tree's runner, documented in the plan).
- Iterates `messages` where `role = 'assistant'`, replays each `content` through
  `StreamingMessageParser`, collecting `onArtifactOpen`/`onActionClose`/
  `onArtifactClose` into `{ title, actions }` groups.
- For each group, calls the same `from-actions.ts` + persist logic as the API
  (factored into a shared `persistArtifact()` in `src/web/src/lib/artifacts/persist.ts`
  so route + script share it).
- Idempotent: safe to re-run (same dedupe rule as the save path). Reports a summary
  (`N conversations scanned, M artifacts, K versions inserted`).

### 4. Gallery read API — `GET /api/artifacts`  (same route file)
- User-scoped: artifacts whose conversation's `userId = getUserId(...)`.
- Returns cards: `{ id, title, kind, conversationId, latestVersion, updatedAt,
  previewSnippet }`. Optional `?q=` substring filter on title.

### 5. Gallery route — `src/web/src/app/(app)/artifacts/page.tsx`
- Replaces the `FeatureLanding` stub. Server component; fetches user artifacts.
- Renders the grid gallery (cards: kind badge, title, source-chat link, relative
  time, a small preview thumbnail). Header with a search box. Empty state with a
  "generate one in chat" CTA.
- Built with the **frontend-design skill**, inside the existing claude.ai-parity
  design system (refined-minimalism, not a bold divergence — per project UI rule).

### 6. Detail route — `src/web/src/app/(app)/artifacts/[id]/page.tsx`
- Version switcher (`◀ v1 v2 v3 ▶`), download.
- **Preview** by kind:
  - `html` → sandboxed `<iframe srcDoc>` (sandbox=`allow-scripts`).
  - `svg` → render inline (sanitized) / iframe.
  - `markdown` → existing markdown renderer.
  - `mermaid` → render via the mermaid lib if already a dep; else code view fallback.
  - `react` / multi-file `code` → code view (reuse `FileContentView` / CodeMirror)
    + an "Open in workbench" link (reuses the existing workbench/container preview;
    we do NOT in-browser-bundle React here — YAGNI, workbench already does it).
- Code view available for every kind via a Preview/Code toggle.

### 7. Remove `customize`
- Delete the `customize` entry from `PROVIDER_FEATURES.anthropic` in
  `src/lib/ai/features.ts` and any sidebar/nav reference; remove the
  `app/(app)/customize` route if it's only the landing stub. Grep the whole web
  tree for `/customize` + `"customize"` first to avoid a dangling link.

## Data flow

```
generation:  chat turn → <boltArtifact> → StreamingMessageParser (existing)
                → in-chat card (existing, unchanged)
                → onArtifactClose → POST /api/artifacts → persistArtifact()
                                                            → artifacts + artifactVersions

backfill:    web.messages (existing) → StreamingMessageParser replay
                → persistArtifact() (same path)  → artifacts + artifactVersions

read:        /artifacts (gallery) → GET /api/artifacts → cards
             /artifacts/[id]      → version + preview by kind
```

## Error handling

- `db` unset → APIs return 503; gallery shows a "persistence disabled" empty state.
- Save path is fire-and-forget from chat: any failure is logged, never surfaced to
  the user, never blocks the turn.
- Ownership enforced on every artifact read/write via the conversation→user join.
- Backfill is idempotent and re-runnable; logs but does not abort on a single bad
  message.
- Unknown/garbled artifact (no file actions) → skipped, not persisted.

## Testing

- **Unit:** `from-actions.ts` kind inference + primary-file selection (vitest, run
  from `src/web`). Cases: single html, single svg, single md, single react
  component, mermaid fence, multi-file app, no-file artifact (skip).
- **Unit:** `persistArtifact()` versioning (new artifact → v1; same title again →
  v2; identical content → no new version).
- **Route:** `GET/POST /api/artifacts` ownership (other user's conversation → 404).
  Use the vitest catch-all-route import convention (variable specifier +
  `@vite-ignore`, run vitest from `src/web`).
- **Build:** `npm run build` (vite) green; `tsc` clean.
- Manual: backfill against the real dev Postgres, then load `/artifacts` and a
  detail page; verify html preview renders sandboxed.

## Non-goals (explicit)

- No rebuild of the in-chat artifact panel / no in-chat live-preview side panel
  (that was the larger option the user deferred).
- No in-browser React bundling in the gallery (workbench owns that).
- No MCP/"live artifacts"/external-data features.
- Nothing outside `src/web/`.
