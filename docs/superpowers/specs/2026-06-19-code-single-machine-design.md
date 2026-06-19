# /code — single canonical machine + ephemeral cloud sandboxes

**Date:** 2026-06-19
**Status:** Approved design (brainstorming) → ready for implementation plan
**Scope:** `src/web` only (bridge store + `GET /environments` route + `/code` UI). No CLI, no schema migration, no voice-agent.

## Problem

`/code` lists every `environments` row as a "connected machine." Today that is 1 local
(`Moon`, `worker_type=claude_code_repl`) + 2 `container` rows that are **101 h and 170 h
stale** — finished cloud sessions whose rows were never deleted. Root causes:

1. **No automatic GC.** `deleteEnvironment` runs only on an explicit CLI disconnect; nothing
   reaps by `last_seen_at`, so stale cloud sandboxes accumulate forever.
2. **Cloud sandboxes are listed as "machines"** alongside the local box, undifferentiated.
3. **Machine identity keys on `directory`** — `findEnvironmentByIdentity` matches
   `machine_name AND directory`, so the same physical box used in two folders shows as two
   machines.

(The per-connect duplicate-`Moon` bug was already fixed via identity reuse — see
`src/web/src/lib/bridge/store.ts:335`. The remaining clutter is GC + categorization + the
`directory` key.)

## How claude.ai/code models it (research)

claude.ai/code is **session-centric**, not machine-centric: one session list,
**icon-differentiated** — a computer icon + green dot for the connected local machine, a
cloud/diff icon for each sandbox. There is no "Machines" inventory page.

- **Local machine** = one registered local process; outbound-only poll; green-dot online;
  **~10-min network TTL**, then the process exits. claude.ai keys identity **per session**
  (hostname is only a display prefix) and avoids duplicates by reconnect-to-same-session +
  a "continue or new" prompt — it does **not** GC duplicate machines.
- **Cloud** = ephemeral per-task VMs that go `expired` after inactivity and are reclaimed;
  the **transcript outlives the VM**. An *environment* is a durable, user-edited template
  (name, network policy, env vars, setup script, ~7-day cached snapshot); a *sandbox* is the
  throwaway instance.

**Our deliberate divergence (researcher-recommended):** to deliver the "exactly one machine"
the user wants, we key the local machine on a **stable identity** and UPSERT + GC, rather than
claude.ai's per-session identity (which is why it shows duplicate-ish sessions). We keep
claude.ai's **UX** (one icon-differentiated list) and **lifecycle** (online dot, ephemeral
auto-reclaimed sandboxes, transcript kept).

## Design (Approach A — server + web only)

### 1. Machine identity — one row per machine
`findEnvironmentByIdentity(user_id, machine_name)` keys on **`(user_id, machine_name)` only**
(drop `directory`), scoped to **non-`container`** rows. The local box is one row regardless of
attach directory; `directory` becomes a mutable facet refreshed on each attach (alongside
`branch`/`git_repo_url`, which already work this way). Container sandboxes are unaffected —
they keep their existing per-`(user, repo)` dedup in the `environments/cloud` path, so
sandboxes never collapse into each other.

### 2. Lazy GC reaper (no background job)
A best-effort sweep at the top of `GET /api/bridge/v1/environments`:

- **Sandboxes** (`worker_type = 'container'`) with `last_seen_at` older than `SANDBOX_TTL`
  **and no active session** → `deleteEnvironment` (the container itself was already reaped on
  archive; this clears the dangling row). Session transcripts in the `sessions` table are
  untouched.
- **The machine** is never deleted; it is annotated `online = (now - last_seen_at) <
  ONLINE_TTL`, else `offline`. Liveness keys on the heartbeat and explicitly excludes zombies
  (the lesson from the existing `/code` worker-liveness work).
- TTLs (tunable constants): `ONLINE_TTL ≈ 2 min` (responsive dot); `SANDBOX_TTL ≈ 24 h`.
  (claude.ai's ~10-min network TTL is the reference for a future "local agent self-exits when
  unreachable" parity — see Future.) The two 101 h / 170 h container rows reap on the first
  fetch.

Lazy on-read GC is chosen over a background timer because `GET /environments` is the only
consumer that cares, and it keeps the change server+web-local with no new process.

### 3. `/code` UI split
`page.tsx` replaces the flat `setMachines(j.environments)` with two groups: **one local
machine** (computer icon + green/grey `online`/`offline` dot) and **cloud sandboxes** (a
separate group; each shows an `expired` badge when reclaimed). Auto-select the machine when
present. This is claude.ai's icon-differentiated single list and fits the existing left-nav /
3-column layout.

### 4. Migration
Purely the reaper — the two stale `container` rows vanish on the next `/environments` load;
`Moon` stays as the one machine. No manual DB surgery, no schema change.

## Scope

- **IN:** `src/web/src/lib/bridge/store.ts` (`findEnvironmentByIdentity` + a reaper helper),
  `src/web/src/app/api/bridge/v1/environments/route.ts` (GET reaps + annotates online/offline),
  `src/web/src/app/(app)/code/[[...session]]/page.tsx` + a small machine/sandbox list
  component.
- **OUT:** the CLI (off-limits and no change needed — `machine_name` is already sent on
  register), the `environments` schema (no migration), the voice-agent, and the full
  "environment template" system (network policy / env vars / setup script / cached snapshot).

## Testing

Unit (vitest, `src/web/tests/bridge/`):
- **Identity:** same `machine_name` + two directories → one row; two containers stay separate;
  a genuinely different machine (`machine_name` differs) is its own row.
- **Reaper:** stale container deleted; machine kept; a fresh sandbox kept; a sandbox with an
  active session spared.
- **Online calc:** `last_seen` within/over `ONLINE_TTL` → online/offline; a zombie row is not
  reported "online."

Existing bridge tests must stay green.

## Future (deferred — not required for "one machine")

- **Environment templates** — claude.ai's durable env (name, network policy, env vars, setup
  script, cached snapshot). Larger feature; not needed to stop the machine clutter.
- **Local-agent self-exit at ~10 min unreachable** — full claude.ai network-TTL parity; needs
  a CLI change, so deferred.
