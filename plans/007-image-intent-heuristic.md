# Plan 007: Tighten `hasImageIntent()` so text asks don't force image generation

> **Executor instructions**: This is a heuristic-tuning plan — the **test table in
> Step 1 is the spec**. Make the table pass with the smallest change; don't
> over-fit. Run each verify command, honor STOP conditions, update the
> `plans/README.md` row when done.
>
> **Drift check (run first)**:
> `git diff --stat f6efd301..HEAD -- src/web/src/lib/chat/image-markdown.ts`
> If it changed, re-read the function before editing.

## Status

- **Priority**: P3
- **Effort**: S
- **Risk**: LOW (heuristic; the explicit Image toggle remains the authoritative path)
- **Depends on**: none
- **Category**: bug (false positive)
- **Planned at**: commit `f6efd301`, 2026-06-22

## Why this matters

`hasImageIntent()` auto-forces the image-generation tool on a user message. Its
third branch fires on **"generate/create/make/draw/design [me] a/an/the/some
\<anything\>"** unless the object word is in a hardcoded `TEXT_NOUN` list. That
list is incomplete, so common textual asks wrongly trigger image generation —
e.g. "generate a design document" and "design a database schema" (`document`,
`schema` aren't in `TEXT_NOUN`), wasting an image call and surprising the user.
The explicit Image toggle is the reliable signal; the auto-detect should err
toward NOT hijacking text requests. This plan tightens the heuristic against a
concrete table of expected results.

## Current state

`src/web/src/lib/chat/image-markdown.ts:64-95` — the relevant code:

```ts
const IMAGE_NOUN =
  "images?|pictures?|pic|photos?|drawings?|illustrations?|logos?|icons?|artwork|painting|portrait|wallpaper|posters?|banners?|graphics?|visuals?|renders?|sketches?|scene|mockups?|avatars?|stickers?|memes?|comics?|diagrams?";
const TEXT_NOUN =
  "poem|story|essay|code|script|lists?|summary|plan|email|letter|song|recipe|article|paragraph|outline|jokes?|reports?|names?|ideas?|tables?|message|caption|description|paragraphs?";

export function hasImageIntent(text: string): boolean {
  const t = text.toLowerCase();
  const textNoun = new RegExp(`\\b(${TEXT_NOUN})\\b`).test(t);
  // Branch 1 — inherently visual verbs (unless the ask is for a textual artifact).
  if (/\b(draw|sketch|paint|illustrate|render)\b/.test(t) && !textNoun) {
    return true;
  }
  // Branch 2 — "generate/create/make/... an IMAGE-noun ..."
  if (
    new RegExp(
      `\\b(generate|create|make|design|produce|show me|give me|imagine|visuali[sz]e)\\b[\\s\\S]{0,30}\\b(${IMAGE_NOUN})\\b`,
    ).test(t)
  ) {
    return true;
  }
  // Branch 3 — "generate/create/make/draw/design [me] a/an/the/some <thing>"
  if (
    /\b(generate|create|make|draw|design|produce)\s+(me\s+)?(a|an|the|some)\b/.test(t) &&
    !textNoun
  ) {
    return true;
  }
  return false;
}
```

The function is **pure and dependency-free** (used identically on client and
server — see the file header). There is no existing test for it.

The over-breadth is **Branch 3**: it returns true for any "generate a X" whose X
isn't in `TEXT_NOUN`. Branches 1 and 2 are well-targeted (visual verbs; explicit
image nouns) and should keep matching.

## Commands you will need

| Purpose | Command | Expected |
|---|---|---|
| Run the new test | `cd src/web && npx vitest run tests/image-intent.test.ts` | all pass |
| Full suite | `cd src/web && npx vitest run` | no new failures |
| Typecheck | `cd src/web && bunx tsc --noEmit` | exit 0 |

## Scope

**In scope**:
- `src/web/src/lib/chat/image-markdown.ts` — only `hasImageIntent` and its
  `IMAGE_NOUN`/`TEXT_NOUN`/branch logic. Do NOT change `appendImageMarkdown`,
  `stripGeneratedImagesForModel`, or `GENERATED_IMG_MD`.
- `src/web/tests/image-intent.test.ts` (create)

**Out of scope** (do NOT touch):
- The Image toggle / `toolChoice` wiring that consumes `hasImageIntent` — the
  authoritative manual path stays as-is.
- Adding NLP/model-based intent detection — keep it a regex heuristic.

## Git workflow

- Branch: `advisor/007-image-intent-heuristic`
- One commit, e.g. `fix(web): stop hasImageIntent false-firing on text asks`.
- Do NOT push / open a PR unless instructed.

## Steps

### Step 1: Write the expected-behavior table as a test FIRST

Create `src/web/tests/image-intent.test.ts` encoding the intended behavior. These
assertions are the spec for Step 2:

```ts
import { describe, expect, test } from 'vitest'
import { hasImageIntent } from '@/lib/chat/image-markdown'

const SHOULD_BE_IMAGE = [
  'draw a cat',
  'sketch a robot',
  'make me a logo',
  'generate an image of a sunset',
  'create a poster for my event',
  'design an icon for the app',
  'generate a diagram of the architecture',
]
const SHOULD_NOT_BE_IMAGE = [
  'generate a design document',
  'design a database schema',
  'make a plan for the sprint',
  'create a summary of this thread',
  'write a poem about the sea',
  'generate a report',
  'create a list of names',
  'make me a sandwich recipe',
]

describe('hasImageIntent', () => {
  for (const s of SHOULD_BE_IMAGE) {
    test(`image: "${s}"`, () => expect(hasImageIntent(s)).toBe(true))
  }
  for (const s of SHOULD_NOT_BE_IMAGE) {
    test(`text: "${s}"`, () => expect(hasImageIntent(s)).toBe(false))
  }
})
```

**Verify**: `cd src/web && npx vitest run tests/image-intent.test.ts`
→ runs; the `SHOULD_NOT_BE_IMAGE` cases for "design document"/"database schema"
will FAIL against current code (that's the bug — proceed to Step 2).

### Step 2: Tighten the heuristic until the table passes

Make the **smallest** change that turns the table green. The recommended fix is
to **remove Branch 3** (the bare "generate a/an/the \<thing\>" matcher): visual
verbs (Branch 1) and explicit image nouns (Branch 2) keep all the
`SHOULD_BE_IMAGE` cases matching, while the `SHOULD_NOT_BE_IMAGE` cases stop
matching. Confirm "generate a diagram…" stays true (Branch 2 — `diagram` is an
IMAGE_NOUN) and "generate an image of a sunset" stays true (Branch 2).

If removing Branch 3 breaks a `SHOULD_BE_IMAGE` case, prefer adding the missing
image noun to `IMAGE_NOUN` over reinstating Branch 3. Do not simply pad
`TEXT_NOUN` to chase individual words — that's the whack-a-mole the current code
already loses at.

**Verify**: `cd src/web && npx vitest run tests/image-intent.test.ts` → ALL pass.

### Step 3: Confirm nothing else regressed

The same module is imported server- and client-side and may have an existing test
(`src/web/tests/image-generation.test.ts` exists). Run the whole suite.

**Verify**: `cd src/web && npx vitest run` → no new failures; `bunx tsc --noEmit` → exit 0.

## Test plan

- New `tests/image-intent.test.ts` with the two labeled tables (Step 1).
- Verification: targeted run green after Step 2; full suite + tsc clean (Step 3).

## Done criteria

- [ ] `tests/image-intent.test.ts` exists and ALL its cases pass.
- [ ] "generate a design document" and "design a database schema" → `false`.
- [ ] "draw a cat", "make me a logo", "generate a diagram of the architecture" → `true`.
- [ ] `cd src/web && npx vitest run` → no new failures; `bunx tsc --noEmit` exit 0.
- [ ] Only `image-markdown.ts` + the new test changed (`git status`).
- [ ] `plans/README.md` row for 007 updated.

## STOP conditions

- Making the SHOULD_NOT cases pass forces a SHOULD_BE case to fail and no
  IMAGE_NOUN addition fixes it → STOP and report the conflict (the table may need
  a maintainer judgment call on that example).
- You find `hasImageIntent` is also relied on somewhere that expects the OLD
  broad behavior (grep its importers) → STOP and report before narrowing it.

## Maintenance notes

- The Image toggle remains the reliable, explicit path; this heuristic is a
  best-effort convenience, deliberately biased toward NOT hijacking text asks.
- If users report missed image intents after this, prefer extending `IMAGE_NOUN`
  with the specific noun over re-adding a broad "generate a \<anything\>" branch.
- Reviewer: read the two test tables — they ARE the behavior contract; scrutinize
  any example you'd classify differently.
