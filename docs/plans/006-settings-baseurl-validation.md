# Plan 006: Provider/Ollama `baseURL` is validated as a URL before it's stored

> **Executor instructions**: Follow step by step, run each verify command, honor
> STOP conditions. Update this plan's row in `plans/README.md` when done.
>
> **Drift check (run first)**:
> `git diff --stat f6efd301..HEAD -- src/web/src/app/api/settings/route.ts`
> If it changed, re-read the cited lines before editing.

## Status

- **Priority**: P3
- **Effort**: S
- **Risk**: LOW (tightens input validation; empty/null clearing preserved)
- **Depends on**: none (touches a different file than 005, but both are settings —
  if running both, do 005 first to avoid back-to-back review churn)
- **Category**: security (hardening)
- **Planned at**: commit `f6efd301`, 2026-06-22

## Why this matters

`PATCH /api/settings` accepts a provider `baseURL` (and the Ollama `baseURL`) as a
bare `z.string()` with no URL validation. Custom endpoints are a legitimate
feature (Ollama, self-hosted gateways), so the goal is **not** to forbid them —
it's that an unvalidated string is stored and later used as the outbound LLM
endpoint, so a malformed or non-URL value fails confusingly at call time, and a
repointed endpoint would receive the provider API key on the next request. On a
single-user local box this is low-severity (the authenticated user owns the keys
anyway), but parsing the value as a real URL is cheap, correct, and removes a
foot-gun. This plan adds `URL`-shape validation while keeping the
empty-string/null "clear the field" semantics.

## Current state

- `src/web/src/app/api/settings/route.ts:17-22` — provider patch schema:
  ```ts
  const providerPatchSchema = z
    .object({
      apiKey: z.string().or(z.null()).optional(),
      baseURL: z.string().or(z.null()).optional(),
    })
    .optional();
  ```
- `route.ts:66-74` — Ollama connection (same loose `baseURL`):
  ```ts
        ollama: z
          .object({ baseURL: z.string().or(z.null()).optional() })
          .partial()
          .optional(),
  ```
- The PATCH handler treats `null` OR `""` as "clear the field" (lines 115-120 for
  providers, 150-153 for ollama). That behavior MUST be preserved — so the
  validator has to accept `""`, `null`, AND a valid URL, rejecting only non-empty
  non-URL strings.
- Convention: this repo validates request bodies with `zod` (`safeParse`,
  returns `400 {error}` on failure — see route.ts:97-100).

## Commands you will need

| Purpose | Command | Expected |
|---|---|---|
| Typecheck | `cd src/web && bunx tsc --noEmit` | exit 0 |
| Run the new test | `cd src/web && npx vitest run tests/settings-baseurl.test.ts` | passes |
| Full suite | `cd src/web && npx vitest run` | no new failures |

## Scope

**In scope**:
- `src/web/src/app/api/settings/route.ts` (add + use a `baseURL` validator; export it for testing)
- `src/web/tests/settings-baseurl.test.ts` (create)

**Out of scope** (do NOT touch):
- The store (`lib/settings/store.ts`) — plan 005 owns that.
- The provider call path that consumes `baseURL` — no change needed once values
  are validated at the boundary.
- `apiKey`, `token`, `defaultOwner` schemas — leave as-is.

## Git workflow

- Branch: `advisor/006-settings-baseurl-validation`
- One commit, e.g. `fix(web): validate provider/ollama baseURL as a URL`.
- Do NOT push / open a PR unless instructed.

## Steps

### Step 1: Add an exported `baseURL` validator

In `src/web/src/app/api/settings/route.ts`, add near the top (after the imports),
a reusable schema that accepts a valid URL, an empty string, or null:

```ts
// A baseURL may be a valid URL, "" or null (both clear the stored value).
export const baseURLSchema = z
  .union([z.string().url(), z.literal(""), z.null()])
  .optional();
```

**Verify**: `grep -n 'baseURLSchema' src/web/src/app/api/settings/route.ts` → present.

### Step 2: Use it in both places

- In `providerPatchSchema` (line ~20) replace
  `baseURL: z.string().or(z.null()).optional(),` with `baseURL: baseURLSchema,`.
- In the Ollama object (line ~69) replace
  `z.object({ baseURL: z.string().or(z.null()).optional() })` with
  `z.object({ baseURL: baseURLSchema })`.

Leave the handler's null/empty-clearing logic (lines 115-120, 150-153) unchanged
— it already handles `null` and `""`.

**Verify**: `cd src/web && bunx tsc --noEmit` → exit 0.

### Step 3: Test the validator

Create `src/web/tests/settings-baseurl.test.ts`:

```ts
import { describe, expect, test } from 'vitest'
import { baseURLSchema } from '@/app/api/settings/route'

describe('baseURLSchema', () => {
  test('accepts a valid URL', () => {
    expect(baseURLSchema.safeParse('http://127.0.0.1:11434').success).toBe(true)
    expect(baseURLSchema.safeParse('https://api.example.com/v1').success).toBe(true)
  })
  test('accepts "" and null (clear) and undefined (omit)', () => {
    expect(baseURLSchema.safeParse('').success).toBe(true)
    expect(baseURLSchema.safeParse(null).success).toBe(true)
    expect(baseURLSchema.safeParse(undefined).success).toBe(true)
  })
  test('rejects a non-URL string', () => {
    expect(baseURLSchema.safeParse('not a url').success).toBe(false)
    expect(baseURLSchema.safeParse('javascript:alert(1)').success).toBe(false)
  })
})
```

If importing from a route module triggers Next route side effects under vitest,
move `baseURLSchema` into a tiny `src/web/src/lib/settings/base-url.ts`, export it
there, and import it in both the route and the test (update Step 1/2 accordingly).

**Verify**: `cd src/web && npx vitest run tests/settings-baseurl.test.ts` → all pass.

## Test plan

- New `tests/settings-baseurl.test.ts`: valid URLs accepted; `""`/null/undefined
  accepted (clear/omit); non-URL and `javascript:` rejected.
- Verification: the targeted vitest above; then `npx vitest run` (no new
  failures) and `bunx tsc --noEmit` (exit 0).

## Done criteria

- [ ] `baseURLSchema` exists and is used for both provider and ollama `baseURL`.
- [ ] `cd src/web && bunx tsc --noEmit` exits 0.
- [ ] `tests/settings-baseurl.test.ts` passes, including the reject cases.
- [ ] A `PATCH` with `baseURL: "not a url"` would return 400 (the schema rejects it).
- [ ] `git status` shows only the in-scope files.
- [ ] `plans/README.md` row for 006 updated.

## STOP conditions

- The schema excerpts don't match the live route file (drift) → STOP.
- `z.string().url()` rejects a baseURL form the app legitimately needs (e.g. a
  bare host with no scheme that the provider client tolerates) → STOP and report;
  the maintainer may want a custom refine (require scheme) rather than `.url()`.

## Maintenance notes

- This validates SHAPE, not destination. If you later want to restrict providers
  to loopback/known hosts (defense beyond shape), add a `.refine()` on top — but
  that's a product decision (it breaks remote gateways), out of scope here.
- Reviewer: confirm `""`/null still clear the stored value (the handler logic
  was intentionally left untouched).
