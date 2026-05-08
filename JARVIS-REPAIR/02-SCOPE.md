# 02 — Scope Declaration

> The user (Ulrich) explicitly delegated scope-setting to `[ORCH]` in Session 1 (2026-05-05). Scope below is treated as binding by every future session unless an entry appears in the Amendment log.

---

## In scope

The following subsystems are in scope for audit, repair, and verification.

- [x] **Voice channel** — `src/voice-agent/` (Python, LiveKit). Single largest active failure surface. STT (Groq Whisper), VAD (Silero), supervisor LLM (Groq llama-3.3 default; FallbackAdapter to DeepSeek; tray-pickable per `~/.jarvis/voice-model`), specialists registry, supervisor-graph v2 (gated `JARVIS_LANGGRAPH_SUPERVISOR=1`, default off), TTS (Edge / Groq). Includes `jarvis_voice_client.py` (LiveKit peer) and the systemd units `jarvis-voice-agent.service` / `jarvis-voice-client.service`.
- [x] **Hub + state.db** — `src/hub/` (Python). Redis Streams consumer (`events:conversation`, `events:settings`, `events:memory`) → SQLite `~/.jarvis/hub/state.db` → `broadcasts:*` re-publish. The integration backbone: voice/web/CLI/extension all read and write through it. Includes `migrate_*` scripts.
- [x] **Web workbench** — `src/web/` (Next.js). 40+ API routes under `app/api/`, primary user surface for typed chat, voice-session inspection, settings, workbench (deploy / domains / heal / dev-log). Recent Convex retirement migration; uncommitted changes in working tree.
- [x] **Observability layer** — structured JSON logs (`/tmp/jarvis-voice-agent.log`, `~/.jarvis/proxy.log`), `~/.local/share/jarvis/turn_telemetry.db`, the voice-intelligence rubric (`docs/superpowers/specs/2026-04-30-voice-intelligence-rubric.md`). Charter §7 SLOs cannot be verified without it.
- [x] **Service supervision** — systemd user units in `~/.config/systemd/user/jarvis-*.service`. `jarvis-voice-agent` is the active failure point (watchdog stalls observed 2026-05-04/05).

## Out of scope (the Scope Guard)

The team **will not** propose changes to these. Out-of-scope findings go in `03-STATE.md` "Out-of-scope observations" and are surfaced to the user, never silently acted on.

- **CLI (`src/cli/`).** In good shape per memory `feedback_cli_boundary.md` and verified by proxy log (95%+ cache hit, healthy DeepSeek round-trips). A P0 with concrete evidence would require explicit scope expansion.
- **Tauri desktop (`src/desktop-tauri/`).** Separate effort. Per memory `project_tauri_release_rebuild.md`, has its own build discipline. Not actively bug-prone; defer.
- **Misty Scone OS rice (`src/os/desktop/`).** Empty per memory `project_actual_stack.md`; nothing to repair.
- **Android client (`src/android/`).** Semi-active, not in current pain path.
- **Browser extensions (`src/extensions/`).** Stable; new feature work belongs in a separate effort.
- **`.worktrees/` (kimi-supreme / news-widget / screen-watching / voice-quality).** Side branches not on `main`. Repair operates on the current branch only.
- **`src/cli/src/utils/claudeInChrome/`.** Reserved for future Chrome/Firefox extension work per memory `project_claudeinchrome_kept.md`. Don't touch.

## Goals (measurable success criteria)

What "fixed" looks like, in numbers. Each becomes an Acceptance Gate row.

1. **Zero LLM circuit-breaker false-trips over a 24h voice soak.** Concretely: zero `[breaker:llm] OPEN` log lines that resolve to validation errors (the cause-walking fix should make this impossible). Real transport outages may still trip it; those are correct.
2. **Voice end-to-end p95 < 3.0s, p50 < 1.5s** measured over a 100-turn dev set on the canonical Groq llama-3.3-70b path (Charter §7 voice latency budget). Baseline currently unmeasured.
3. **Zero unplanned `jarvis-voice-agent.service` restarts over 24h.** systemd watchdog stalls were the dominant failure 2026-05-04/05; goal is no `Watchdog timeout (limit 2min)!` events.
4. **Test suite green on the in-scope packages.** Currently 1 real failure (`test_supervisor_has_persona_register_block`) + ~6 order-dependent flakes in `tests/test_track_guard.py` and `tests/test_specialists_health.py::test_browser_v2_*`. Target: 0 failures, 0 flakes when run in any order.
5. **Voice-intelligence rubric ≥ 95/100, target 100/100.** Per `docs/superpowers/specs/2026-04-30-voice-intelligence-rubric.md`. Current claim per memory is 95/100 — verify with one fresh measurement before treating as baseline.
6. **Zero `recall search failed: no such table: turns` log lines** post-restart (the missed-migration fix should eliminate them).
7. **Hub schema migration completeness.** Verified: every voice/web/CLI write path lands in `~/.jarvis/hub/state.db` with no silent drops. Today there are six entries of "no such table: turns" in 24h pre-fix; should drop to zero.

## Non-goals

- **No new channels, models, or capabilities.** Repair only. New Kimi mode work in `src/web` may continue (it's not in scope as new capability — it's already shipped) but the team will not propose feature additions.
- **No re-architecting the hub Redis pipeline.** Convex retirement is recently complete; building on top, not over.
- **No replacement of LLM providers.** Multi-provider stays multi-provider per memory `project_multi_llm.md`.
- **No UI polish beyond what fixes a P0/P1 functional bug.**
- **No CLI changes.**

## Constraints

- **Time:** no hard deadline; quality over speed. 200-LOC / 3-file patch cap is enforced (Charter §3 Phase 5).
- **Hardware:** must run on Ulrich's existing Linux workstation (Kali rolling). No cloud burst, no hardware assumptions beyond what `~/.config/systemd/user/jarvis-*.service` already implies.
- **Must-not-break:** voice (just-fixed), CLI (out of scope and healthy), the hub Redis bus (everything depends on it).
- **Privacy:** voice transcripts and conversation memory stay on-device. The web workbench is a personal local instance; do not introduce auth surfaces that imply public deployment.
- **Reversibility:** every patch ships with a rollback path (Charter Principle 8). For voice, "switch ~/.jarvis/voice-model back" must always work.
- **Auto-restart-on-fix:** voice-agent and hub services run under systemd; any code change requires `systemctl --user restart <unit>` to take effect. This is not optional and the team verifies post-restart, not just compile-clean.

## Stakeholders

- **Product / final approval:** Ulrich (the user).
- **Technical authority:** `[ARCH]`, with `[SEC]` veto on security surfaces (key handling, prompt construction, root-via-sudoers calls per memory `project_jarvis_root.md`).
- **Override authority:** Ulrich, recorded in an ADR.

## Amendment log

| Date | Change | Reason |
|---|---|---|
| 2026-05-05 | Initial scope declared by `[ORCH]` under user delegation | User instruction "review all my project and answer those questions yourself and start" — Session 1 |
| 2026-05-05 | Charter §1 mission marked partially obsolete (no Brain Server / Weaviate / PostgreSQL / local model serving in actual repo) | See `decisions/ADR-002-charter-architecture-amendment.md` |
