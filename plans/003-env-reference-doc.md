# Plan 003: README's promised `docs/env-reference.md` exists (+ a root `.env.example`)

> **Executor instructions**: Follow step by step, run each verify command, honor
> STOP conditions. Update this plan's row in `plans/README.md` when done.
>
> **Drift check (run first)**:
> `git diff --stat f6efd301..HEAD -- README.md src/web/.env.example src/voice-agent/.env.example`
> If any changed, re-read them before proceeding.

## Status

- **Priority**: P2
- **Effort**: S
- **Risk**: LOW (docs only)
- **Depends on**: none
- **Category**: docs / dx
- **Planned at**: commit `f6efd301`, 2026-06-22

## Why this matters

`README.md:77` tells new contributors: "Full env-var reference:
[`docs/env-reference.md`](docs/env-reference.md)." **That file does not exist** —
the link is dead. There is also no root `.env.example`, so the cross-tree env
wiring (the proxy auth token, `DATABASE_URL`, ACP) is undocumented and a fresh
setup is trial-and-error. A wrong/dead doc in the onboarding path is worse than
no doc. This plan creates the referenced reference and a root template.

## Current state

- `README.md:77` (verbatim): "… Full env-var reference:
  [`docs/env-reference.md`](docs/env-reference.md)." — target absent
  (`ls docs/env-reference.md` → No such file).
- No `./.env.example` at repo root (`ls .env.example` → No such file).
- Per-tree templates DO exist and are the source of truth to consolidate:
  - `src/web/.env.example` (~11 vars)
  - `src/voice-agent/.env.example` (~7 vars)
- Additional env vars not in those templates, confirmed in code:
  - `src/web/src/proxy.ts`: `JARVIS_REQUIRE_LOCAL_AUTH`, `JARVIS_LOCAL_API_TOKEN`,
    `JARVIS_AUTH_DISABLED`, `JARVIS_CANONICAL_HOST`.
- **HARD CONSTRAINT — the docs-truth CI gate** (`.github/workflows/lint.yml`, the
  `docs-truth` job, **blocking**) greps all of `docs/` and fails the build if it
  finds key-shaped strings. So `docs/env-reference.md` must reference every
  secret **by variable name and placeholder only** — never a real or
  realistic-looking key value. Forbidden patterns include `gsk_…`, `sk-…`,
  `sk-ant-…`, `lsv2_pt_…`, `AIza…`. Use placeholders like `<your-key>` or `xxx`.

## Commands you will need

| Purpose | Command | Expected |
|---|---|---|
| Enumerate web env reads | `grep -rhoE 'process\.env\.[A-Z_][A-Z0-9_]+' src/web/src \| sort -u` | list of vars |
| Enumerate voice-agent env reads | `grep -rhoE 'os\.environ(?:\.get\()?\[?["'"'"'][A-Z_][A-Z0-9_]+' src/voice-agent --include='*.py' \| grep -oE '[A-Z_][A-Z0-9_]+' \| sort -u` | list of vars |
| Read the two .env.example files | `cat src/web/.env.example src/voice-agent/.env.example` | the templated vars |
| Docs-truth gate (must stay green) | `! grep -rnE 'gsk_[A-Za-z0-9]{16}\|sk-[A-Za-z0-9]{20,}\|sk-ant-[A-Za-z0-9]{10,}\|lsv2_pt_[a-f0-9]{8,}\|AIza[A-Za-z0-9_-]{20,}' docs/env-reference.md` | no output (exit 0) |

## Scope

**In scope** (create):
- `docs/env-reference.md`
- `.env.example` (repo root)

**Out of scope** (do NOT touch):
- `src/web/.env.example`, `src/voice-agent/.env.example` — leave them; the new
  files reference/consolidate, they don't replace.
- Any real `.env` / `~/.jarvis/keys.env` — never read or copy real secret values.
- `README.md` — the link already points at the file you're creating; no edit
  needed unless the path differs (it doesn't).

## Git workflow

- Branch: `advisor/003-env-reference-doc`
- One commit, e.g. `docs: add env-reference.md + root .env.example`.
- Do NOT push / open a PR unless instructed.

## Steps

### Step 1: Enumerate the real env vars

Run the four "Commands you will need" greps and the two `.env.example` reads.
Build a deduped list. Group vars by subsystem:
- **Voice-agent** (LLM provider keys, LiveKit URL/key/secret, STT/TTS toggles,
  memory provider, feature flags `JARVIS_*`).
- **Web app** (`DATABASE_URL`, `BETTER_AUTH_URL`, provider keys, `JARVIS_*` web flags).
- **Proxy / local auth** (`JARVIS_REQUIRE_LOCAL_AUTH`, `JARVIS_LOCAL_API_TOKEN`,
  `JARVIS_AUTH_DISABLED`, `JARVIS_CANONICAL_HOST`).
- **Secrets store** (note that secrets live in `~/.jarvis/keys.env`; see CLAUDE.md /
  memory — the root `.env` is config-only).

### Step 2: Write `docs/env-reference.md`

One section per subsystem. For each var: a one-line purpose, allowed values /
default, and which subsystem(s) read it. **Placeholders only for any secret.**
Open with a short note: "Secrets (provider API keys, `JARVIS_PG_DSN`)
live in `~/.jarvis/keys.env`, not in a committed `.env`. This
file documents what each variable does — never put real values here."

**Verify (docs-truth gate)**:
`! grep -rnE 'gsk_[A-Za-z0-9]{16}|sk-[A-Za-z0-9]{20,}|sk-ant-[A-Za-z0-9]{10,}|lsv2_pt_[a-f0-9]{8,}|AIza[A-Za-z0-9_-]{20,}' docs/env-reference.md`
→ no output, exit 0.

### Step 3: Write the root `.env.example`

A copy-to-`.env` template with grouped, commented placeholders (no real values),
matching the vars documented in Step 2. Keep it consistent with the per-tree
`.env.example` files (don't contradict them).

**Verify**: `test -f .env.example && echo ok` → `ok`.

### Step 4: Confirm the README link now resolves

**Verify**: `test -f docs/env-reference.md && grep -q 'docs/env-reference.md' README.md && echo ok` → `ok`.

## Test plan

No code tests. Verification gates: the docs-truth grep (Step 2) and the
file-existence checks (Steps 3–4). Optionally run the full docs-truth job locally:
`! grep -nE 'src/hub|memory_extractor|memory_consolidator' docs/env-reference.md`
→ no output.

## Done criteria

- [ ] `docs/env-reference.md` exists, groups vars by subsystem, one line each.
- [ ] No key-shaped string in it (the Step 2 grep is empty).
- [ ] `.env.example` exists at repo root with grouped placeholder vars.
- [ ] `README.md`'s `docs/env-reference.md` link resolves to a real file.
- [ ] `git status` shows only the two new files.
- [ ] `plans/README.md` row for 003 updated.

## STOP conditions

- A `.env.example` source file contains what looks like a REAL secret (not a
  placeholder) → STOP and report (it's a leak to fix first, separately); do not
  copy it forward.
- The grep enumeration surfaces 100+ vars → STOP and ask whether to document all
  or only the commonly-set ones (don't silently write a 500-line doc).

## Maintenance notes

- When a new required env var is added in code, add it here — consider a future
  CI drift-check (the 2026-05-29 hardening plan suggested one) that greps for
  `process.env.X` / `os.environ['X']` not present in this doc.
- Keep this list authoritative but pointer-only for secrets; the docs-truth gate
  will reject any accidental key paste.
