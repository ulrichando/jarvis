# JARVIS Between-Turn Scheduler — Design Spec

**Date:** 2026-05-20
**Status:** Implemented (phase 1), shipped to `master` 2026-05-20. **Amended 2026-05-20 to match as-built** — see the Amendment section below. Where the original body (especially §2.1, §2.3, §2.4) or the "Locked decisions" disagree with it, **the Amendment is authoritative.**
**Origin:** Comparative review of NousResearch `hermes-agent` (cloned at `./hermes`) vs JARVIS. The review identified that JARVIS has solid persistent memory but **no between-turn / autonomous execution** — it only thinks when spoken to. This spec closes that gap by porting and adapting Hermes's cron subsystem (`hermes/cron/jobs.py`, `hermes/cron/scheduler.py`).

---

## Amendment — as-built (2026-05-20)

Implementation diverged from the original design in five places. The as-built behavior is authoritative:

1. **Hosting is a standalone systemd timer, NOT an in-daemon entrypoint tick (supersedes §2.1 + decision 4).** LiveKit's `entrypoint()` is *per-session*, not always-on — only the worker *process* is always-on, and it exposes no clean singleton hook (`prewarm` runs in each of N job subprocesses). So the tick runs in **`jarvis-cron.timer`** (every minute) → `jarvis-cron.service` → `src/voice-agent/cron_worker.py` → `cron_scheduler.tick()`, fully independent of voice sessions. This is what makes jobs fire *between sessions* (truly unattended) — the per-session entrypoint design could not. `JARVIS_CRON_DISABLED=1` is honored by `cron_worker`.
2. **`prompt` jobs are text-only in phase 1 (supersedes §2.3 + decision 5).** No tool loop, no `allow_shell` — a prompt job is a single LLM completion that speaks/queues its text. The read-only **tool loop** + `allow_shell` move to **phase 2** (a safe off-band tool-execution loop is its own build). The "morning briefing" use case is served by a `script` job in phase 1.
3. **No live mid-session speak (supersedes §2.4 "Live speak" + decision 2).** The ticker is a separate process with no `AgentSession`, so it cannot `session.say()`. Delivery is `notify-send` (always) + the `pending.jsonl` queue, drained + voiced by the voice agent on the user's next connect. Live mid-session voice → phase 2.
4. **The store lock is a real cross-process `fcntl` lock (clarifies §2.2).** Because the timer process and the voice daemon's schedule tools both write `jobs.json`, the read-modify-write mutators (`add_job`/`remove_job`/`_mutate`/`get_due_jobs`/`advance_next_run`/`mark_job_run`) hold an exclusive `fcntl` lock on `~/.jarvis/cron/.store.lock`.
5. **Files vs §3:** added `src/voice-agent/cron_worker.py` + `setup/systemd/jarvis-cron.{service,timer}`. `jarvis_agent.py`'s change is "drain `pending.jsonl` on connect + register the 4 schedule tools" — the per-session `run_forever` start was **removed**. `jarvis-cron.service` mirrors the voice-agent's *light* sandbox (deliberately NOT `ProtectHome`/`ProtectSystem`/`RestrictAddressFamilies`: script jobs write `$HOME`, prompt jobs need network).

Everything else shipped as written: job schema, schedule kinds, `[SILENT]`, `cron_runs` audit, failure policy + auto-disable, caps, the `pending_confirm` confirm-gate, the content security scan, and the voice creation tools. Commits: `e6e0da81`…`e93f4820` (build) + `c439d8df` (unattended timer).

---

## 1. Problem & goal

JARVIS is reactive-only: the supervisor LLM engages exclusively during a live voice turn. It cannot run scheduled work, monitor anything, or follow up on its own. The goal is a **general-purpose scheduler** that runs prompts or scripts on a schedule and delivers results to the user even when no voice session is active.

### Goals (phase 1)
- Persist user-defined jobs and run them on time, unattended, while the daemon is up.
- Two job types: a shell/Python **`script`** job and an LLM **`prompt`** job.
- Deliver results via desktop notification immediately **and** voice them on the next session.
- Create/list/cancel jobs by voice, with the same store hand-editable as config.

### Non-goals (deferred)
- Full cron expressions, a `jarvis-cron` CLI, broader prompt-job toolsets, per-job workdir → **phase 2**.
- A Tauri jobs-management UI → **phase 3**.
- Messaging-platform delivery (Telegram/Discord/etc.) → out of scope (no persistent channel in JARVIS).

### Locked decisions (from brainstorming 2026-05-20)
1. **Full general scheduler** (phased; phase 1 = MVP below).
2. **Delivery:** desktop `notify-send` immediately **+** queued and voiced on next session connect; also speak live if a room is connected at completion.
3. **Creation:** voice (supervisor tool) **+** hand-editable config, one shared store.
4. **Hosting:** in the voice-agent daemon (not a standalone worker).
5. **`prompt` jobs run a read-only toolset by default**; shell/write requires an explicit per-job flag.

---

## 2. Architecture

### 2.1 Hosting — in-daemon asyncio tick  ⚠️ SUPERSEDED — see Amendment §1 (as-built: a standalone `jarvis-cron.timer`, because LiveKit's `entrypoint()` is per-session)
A periodic asyncio task runs inside `jarvis-voice-agent.service` (already always-on), ticking every **60 s**. It is started from `jarvis_agent.py::entrypoint()` as `asyncio.create_task(scheduler.run_forever())`, guarded by `JARVIS_CRON_DISABLED`.

- **Why in-daemon:** reuses the wired LLM providers + tool implementations, and can `session.say()` results directly when a room is live. Mirrors Hermes (its scheduler is a background thread inside the always-on gateway).
- **Trade-off (accepted):** if the daemon is down, jobs pause and resume on next start — nothing is lost because schedules persist on disk. A down daemon means JARVIS is not functioning anyway.
- **Double-fire guard:** `fcntl.LOCK_EX | LOCK_NB` on `~/.jarvis/cron/.tick.lock` so an overrunning tick never overlaps the next.
- **Off-band execution:** a tick only *selects and dispatches* due jobs; each job runs in its own `asyncio.create_task` (prompt jobs) or thread executor (blocking scripts), so a slow job never stalls the tick or a live voice turn. This is the same off-critical-path pattern as `pipeline/memory_extractor.py`.

### 2.2 Job store
Port `hermes/cron/jobs.py` → `src/voice-agent/pipeline/cron_jobs.py`. Persistence: `~/.jarvis/cron/jobs.json`, written atomically (`tempfile` + `os.replace`) under an `fcntl` lock, hand-editable.

**At-most-once semantics:** `next_run_at` is advanced *before* the job runs, so a crash mid-run skips that occurrence rather than double-firing (Hermes's rule).

**Job schema:**
```jsonc
{
  "version": 1,
  "jobs": [{
    "id": "uuid4",
    "name": "morning repo brief",
    "schedule": { "kind": "daily-at", "at": "08:00" },   // or {"kind":"once","at":"<iso>"} | {"kind":"interval","every_s":3600}
    "type": "prompt",                 // "prompt" | "script"
    "prompt": "Summarize uncommitted work across my git repos.",  // prompt jobs
    "command": null,                  // script jobs: shell/py command string
    "allow_shell": false,             // prompt jobs only: unlock bash/write/edit
    "toolset": ["read","list_memories","list_skills"],  // illustrative read-only subset; exact allow-list finalized in impl — never bash/write/edit unless allow_shell
    "delivery": "notify+voice",       // "notify" | "voice" | "notify+voice" | "local"
    "enabled": true,
    "pending_confirm": false,         // voice-created jobs start true until confirmed
    "created_by": "voice",            // "voice" | "config"
    "created_ts": 0, "next_run_at": 0, "last_run_at": 0,
    "run_count": 0, "consecutive_failures": 0
  }]
}
```

**Schedule kinds (phase 1):** `once` (one ISO datetime, auto-disables after firing), `interval` (every N seconds, N ≥ 60), `daily-at` (`HH:MM` local). Full `cron` expressions via `croniter` are phase 2.

**API:** `load()`, `save()`, `add(job)`, `remove(id)`, `set_enabled(id,bool)`, `compute_next_run(schedule, now)`, `get_due_jobs(now)`, `mark_run(id, ok)`.

### 2.3 Scheduler & execution
`src/voice-agent/pipeline/cron_scheduler.py` owns `run_forever()` (the tick) and `run_job(job)`.

- **`script` job:** run `command` via `asyncio.create_subprocess_shell` with a hard timeout (default 120 s); capture stdout/stderr; deliver stdout. No LLM. (Hermes `no_agent`.)
- **`prompt` job:** build a **fresh, minimal chat context** (system preamble + the job prompt) and call the supervisor LLM through `providers/llm.py` with the job's restricted `toolset`. **It must never read or mutate the live `AgentSession.chat_ctx`** — it constructs its own throwaway context. Collect the final text and deliver. Reuses existing tool implementations, exposed via a per-job allow-list.
- **`[SILENT]` convention:** if a job's output is exactly/begins-with `[SILENT]`, suppress delivery (still audited). Lets a monitoring job stay quiet when there's nothing to report.
- **Audit:** every run appends to a new `cron_runs` table in `~/.local/share/jarvis/turn_telemetry.db` (`job_id, ts_utc, type, ok, duration_ms, delivered, output_path`) and writes full output to `~/.jarvis/cron/output/<job_id>/<ts>.md`.
- **Failure policy:** exceptions are caught, audited, and (if `delivery` includes notify) surfaced as "Job ⟨name⟩ failed: ⟨err⟩". After `JARVIS_CRON_MAX_FAILURES` (default 3) consecutive failures a job auto-disables and notifies once.

### 2.4 Delivery
`src/voice-agent/pipeline/cron_delivery.py`:
- **Immediate:** `notify-send "JARVIS" "<summary>"` via subprocess (graceful no-op if `notify-send`/`DISPLAY` absent — falls back to queue-only).
- **Queue:** append `{ts, job, text}` to `~/.jarvis/cron/pending.jsonl`.
- **Live speak:** if `delivery` includes voice and a room is connected at completion, `session.say()` immediately.
- **Drain on connect:** `jarvis_agent.py::entrypoint()` reads + clears `pending.jsonl` once a session is ready and voices a single "While you were away: …" digest, then continues normally.

### 2.5 Creation
- **Voice** — supervisor function tools in `src/voice-agent/tools/schedule.py`:
  - `schedule(when: str, what: str, kind: str = "prompt")` → parses `when` (NL → `schedule` object), stages the job with `pending_confirm=true, enabled=false`, and **returns a human-readable summary for the supervisor to read back**.
  - `confirm_schedule(id)` → flips `pending_confirm=false, enabled=true`. Only called after the user verbally agrees.
  - `list_schedules()`, `cancel_schedule(id)`.
  - Ack phrases follow the `subagents/_ack_phrases.py` register (sir-free).
- **Config** — `jobs.json` is hand-editable; malformed entries are skipped with a log line, never crash the tick. (`jarvis-cron` CLI is phase 2.)

### 2.6 Safety (load-bearing — threat model assumes mic prompt-injection)
- Voice-created jobs **never run until verbally confirmed** (`pending_confirm` gate).
- `prompt` jobs default to the read-only `toolset`; `allow_shell=true` (unlocking bash/write/edit) must be set deliberately and triggers a louder spoken confirm ("this job can run shell commands unattended — confirm?").
- Job-store writes pass through a content security scanner ported from `hermes/tools/memory_tool.py::_scan_memory_content` (injection / exfil / invisible-unicode patterns).
- Caps: `JARVIS_CRON_MAX_JOBS` (default 50), min interval 60 s.
- Kill-switch: `JARVIS_CRON_DISABLED=1` (read at runtime).
- Full per-run audit (see §2.3).

---

## 3. Components

| File | New/Mod | Purpose |
|---|---|---|
| `src/voice-agent/pipeline/cron_jobs.py` | new | Job store: schema, atomic+locked load/save, schedule parsing, next-run, due selection, CRUD |
| `src/voice-agent/pipeline/cron_scheduler.py` | new | `run_forever()` tick + `run_job()` (script + prompt execution, `[SILENT]`, audit, failure policy) |
| `src/voice-agent/pipeline/cron_delivery.py` | new | notify-send + pending.jsonl queue + live-speak + drain helper |
| `src/voice-agent/tools/schedule.py` | new | Supervisor tools: `schedule`/`confirm_schedule`/`list_schedules`/`cancel_schedule` + NL→schedule parse |
| `src/voice-agent/jarvis_agent.py` | mod | Start tick task in `entrypoint()` (gated); drain `pending.jsonl` on session ready; register schedule tools |
| `src/voice-agent/tests/test_cron_jobs.py` | new | Schedule parse, next-run (once/interval/daily-at), due selection, atomic save/load, caps |
| `src/voice-agent/tests/test_cron_scheduler.py` | new | Tick fires due jobs, advances next_run before run, script run, prompt run (mocked LLM), `[SILENT]`, failure→auto-disable |
| `src/voice-agent/tests/test_cron_delivery.py` | new | notify-send invoked (mocked), queue append+drain, live-say (mocked session) |

**Scope guard (regression-prevention):**
- **IN:** the files above (voice-agent only).
- **OUT:** `desktop-tauri`, `web`, `src/cli`, `src/hub`. Phase-1 delivery is entirely in-daemon (notify-send + local queue + `session.say`), so **no hub or Tauri changes are needed**.
- **WHY OUT:** keeps the blast radius inside voice-agent; cross-process delivery (hub events, Tauri toast) is a phase-3 concern.

---

## 4. Error handling
- **Malformed `jobs.json`:** log + skip bad entries; tick continues on valid ones.
- **`notify-send` missing / no `DISPLAY`:** queue-only fallback, logged once.
- **Job timeout / exception:** caught, audited, optionally notified; `consecutive_failures++`; auto-disable at the cap.
- **Tick overrun:** `fcntl` non-blocking lock skips the overlapping tick.
- **LLM provider failure in a prompt job:** flows through the existing `FallbackAdapter`; if all fail, treated as a job failure (above).

## 5. Testing
- Unit tests per component (table above), run via `cd src/voice-agent && .venv/bin/python -m pytest tests/`.
- Time-dependent logic (`compute_next_run`, `get_due_jobs`) takes an injected `now` so it's deterministic — no sleeps.
- LLM and `session.say`/`notify-send`/subprocess are mocked.
- TDD: write the failing test for each unit before its implementation (per `superpowers:test-driven-development`).

## 6. Phasing
- **Phase 1 (this spec):** store + tick + `script` & restricted `prompt` jobs + once/interval/daily-at + delivery (notify + queue + drain + live-speak) + voice `schedule` tool + hand-editable config. Acceptance: *"every morning at 08:00 brief me on my repos"* created by voice, runs unattended, notifies + voices on reconnect.
- **Phase 2:** full cron expressions (`croniter`), `jarvis-cron` CLI, opt-in broader prompt-job toolset (`allow_shell`), per-job workdir.
- **Phase 3:** Tauri jobs panel; richer delivery (hub `events:notification` → desktop toast).

## 7. Risks & open questions
- **Autonomous LLM with tools** is the core risk; mitigated by read-only default + explicit `allow_shell` confirm + caps + audit. Revisit if abused.
- **NL→schedule parsing** quality (voice). Phase 1 keeps schedule kinds simple (once/interval/daily-at) to bound ambiguity; the tool reads the parse back for confirmation.
- **`notify-send` availability** on the target Kali desktop is assumed but should be verified during implementation (fallback already specified).
- **Drain UX:** a long pending queue could produce a verbose "while you were away" digest — phase 1 caps it to the N most recent (default 5) with a "+M more" tail.

## 8. References
- Hermes: `hermes/cron/jobs.py`, `hermes/cron/scheduler.py` (`tick()`, `_run_job_impl()`, `_deliver_result()`).
- JARVIS off-band pattern: `src/voice-agent/pipeline/memory_extractor.py`.
- Security scanner to port: `hermes/tools/memory_tool.py::_scan_memory_content`.
- Comparative review that motivated this: the three Hermes→JARVIS agent reports (memory / autonomy / skills), 2026-05-20.
