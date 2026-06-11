# Web react-compiler migration — design spec

**Date:** 2026-06-11
**Status:** in progress
**Owner:** web (`src/web`)

## 1. Requirements (the problem)

The Next 16 / `react-hooks` v6 upgrade promoted the React Compiler diagnostics
to errors. They were downgraded to warnings (`eslint.config.mjs`, 2026-06-09)
to unblock CI, leaving **~79 warnings** as migration debt:

| Rule | Count | Nature |
|---|---|---|
| `@typescript-eslint/no-unused-vars` | 33 | dead imports/vars/params |
| `react-hooks/set-state-in-effect` | 22 | `setState` inside `useEffect` |
| `react/use` | 11 | compiler use-hook diagnostics |
| `react-hooks/refs` | 11 | ref `.current` read during render |
| `react-hooks/exhaustive-deps` | 4 | missing effect deps |
| `react-hooks/immutability` | 2 | mutating tracked value |

Hotspots: `components/design/design-view.tsx` (18), the design/chat/workbench
suites.

### Acceptance criteria
1. Each touched file's warnings cleared (refactored or justifiably suppressed).
2. **Zero functional regressions** — verified live (Playwright: no new
   console/hydration/pageErrors on the affected flow), not just build+lint.
3. Build green, lint 0 errors.
4. A rule is restored to `error` in `eslint.config.mjs` once its **last**
   site is cleared (prevents re-accumulation).

## 2. Design (how)

### 2.1 Two outcomes per site — classify first

Driving the live app showed **no hydration/console errors on any flow** — these
are conservative compiler diagnostics on *working* code, not active bugs. Many
flag **intentional** patterns whose naive "fix" reintroduces documented bugs.
Each site is therefore classified:

- **REFACTOR** — a genuine smell with a safe compiler-approved rewrite.
- **SUPPRESS (justified)** — an intentional pattern; add
  `// eslint-disable-next-line <rule> -- <why>` with the reason. This is a
  legitimate resolution, not a cop-out: a false positive silenced *with a
  documented rationale* is correct engineering.

**Exemplar (why blind refactor is banned):** `design-view.tsx:208` reads
`sessionAssignedIdRef.current` during render. That ref deliberately avoids a
re-render that would unmount `<Chat>` mid-stream (the documented "messages
disappear on refresh" bug). Converting it to state reintroduces that bug →
**SUPPRESS with justification**, never refactor.

### 2.2 Per-rule fix patterns

- **`set-state-in-effect`**
  - *Derive-in-render* when the state is a pure function of props/other state →
    delete the state+effect, compute inline (or `useMemo`).
  - *Lazy init* for one-time setup — **SSR-safe only**: never read `localStorage`
    / `window` in a `useState` initializer (hydration mismatch). Keep the
    `useState(null)` + `useEffect` "mount then upgrade" pattern; SUPPRESS if the
    compiler still flags the intentional mount-sync.
  - *Reset-on-prop-change* (e.g. `setTweakOverrides({})` on `selected.path`) →
    prefer a `key` on the child, else track `prev` and reset during render, else
    SUPPRESS with justification.
- **`refs`** (ref read during render): if the ref is a non-reactive flag by
  design → SUPPRESS; if it's accidental → move the read into an effect/handler.
- **`react/use`**: adopt the compiler's expected hook usage where mechanical.
- **`exhaustive-deps`**: add the missing dep when safe; if listing it loops,
  SUPPRESS with the existing "would loop" justification (already done at
  `design-view.tsx:265`).
- **`immutability`**: clone before mutate (`[...xs]`, `{...o}`).
- **`no-unused-vars`**: remove dead imports/vars; `^_`-prefix intentionally
  unused params (config already honors `^_` as of this migration).

### 2.3 Verification strategy (Testing phase)

Per file: `npm run build` (compile) + `npm run lint` (warning gone) + a
Playwright pass over the affected flow capturing console/hydration/pageErrors
(the `/tmp/web-verify.mjs` harness). A file is "done" only when its flow renders
as clean as before.

### 2.4 Rollout order

1. Leaf hooks + single-warning files (establish the verified pattern cheaply):
   `hooks/use-resizable-column.ts`, `hooks/use-design-comments.ts`,
   `lib/*`, API routes (unused-vars only).
2. The component clusters: `design-view.tsx`, then `chat/`, then
   `workbench/tabs/`.
3. As each rule's final site clears, flip it back to `error` and prune the
   downgrade comment.

## 3. Out of scope

- No behavior changes beyond what a fix strictly requires.
- Files in the user's uncommitted WIP (`eslint.config.mjs`, `api/logs/stream`,
  `settings/*`, `workbench/tabs/*`, `codegen.ts`, `proxy.ts`, `api/health`) —
  improved in place but **not committed** here to avoid entangling WIP.
