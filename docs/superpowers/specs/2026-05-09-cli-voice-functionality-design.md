# CLI voice functionality — design

> **Goal.** Bring the jarvis CLI (`src/cli/`) to parity with the voice-related slash commands and agent definitions that already exist in `.claude/` for Claude Code, so a developer using the jarvis CLI has the same diagnostic surface (`/voice-tests`, `/voice-status`, voice-log-analyzer subagent) without leaving their CLI session.

**Date:** 2026-05-09
**Author:** ulrich (with Claude)
**Status:** draft → for review

---

## Context

The repository has two consumer surfaces that talk to the JARVIS voice agent:

| Surface | Voice diagnostics today |
|---|---|
| **Claude Code** (this CLI) — config in `.claude/` | `/voice-restart`, `/voice-logs`, `/voice-tests` slash commands; `log-analyzer` subagent; SessionStart hook reports voice-agent status. |
| **jarvis CLI** (`src/cli/`) — separate TS/Bun codebase | `/voice` (toggle), `/voice-restart`, `/voice-logs`. **Missing:** `/voice-tests`, runtime status check, log-analyzer subagent. |

Upstream Claude Code (`/home/ulrich/Documents/Projects/claude-code/`) was reviewed; it has fewer voice features than the fork (`src/cli/`) — the fork already adds JARVIS-specific tools (`VoiceAgentStatusTool`, `VoiceSpeakTool`) and multi-backend STT. So the work is purely additive on top of the fork.

## Non-goals

- **Re-architecting voice mode.** The existing `voiceModeEnabled.ts` / `voiceStreamSTT.ts` paths stay as-is.
- **Startup banner.** SessionStart.sh runs at Claude Code session start. Adding an analog to the CLI's startup would touch core boot path; we use an on-demand `/voice-status` command instead.
- **Auto-port of every `.claude/agents/*.md` file.** Only the voice-relevant `log-analyzer` is in scope.
- **Changing how the CLI loads agents.** `.jarvis/agents/<name>.md` is the existing convention; we just add a file there.

---

## Design

### 1. `/voice-tests` slash command

**Purpose.** Run the voice-agent pytest suite from inside the CLI and return a smart summary, mirroring `.claude/commands/voice-tests.md` ("Report: total pass/fail count and the first failing test's traceback").

**Files.**
- New: `src/cli/src/commands/voice/tests.ts`
- Modify: `src/cli/src/commands/voice/index.ts` — add `voiceTests: Command` export
- Modify: `src/cli/src/commands.ts` — add `voiceTestsCommand` declaration alongside the existing `voiceLogsCommand` declaration block, and add the corresponding spread entry into the exported command list (same pattern as `voiceLogsCommand`)

**Behavior.**
1. Accepts a single optional argument string. If non-empty, split on whitespace (respecting `"quoted"` substrings) and forwarded as separate argv entries to pytest. Example: argument `'-k consolidator -x'` becomes argv `['-k', 'consolidator', '-x']`. The string is never run through a shell — it's spawned via `execFile(python, [...])`.
2. Resolves voice-agent path: `process.env.JARVIS_VOICE_AGENT_PATH` if set, else `<homedir>/Documents/Projects/jarvis/src/voice-agent`. If neither exists, return a clear error message ("Voice agent path not found at <path>; set JARVIS_VOICE_AGENT_PATH").
3. Verifies `<va>/.venv/bin/python` exists. If not: clear error ("voice-agent venv missing — run `cd <va> && python -m venv .venv && pip install -r requirements.txt`").
4. Spawns `<va>/.venv/bin/python -m pytest tests/ [filter args...] --tb=short -q` with cwd=`<va>`, timeout 120 s, 10 MB stdout cap.
5. Smart summary parser:
   - Extract pytest summary line via regex: `/(\d+ (?:passed|failed|skipped|errors?|deselected|warnings?))(?:, \d+ (?:passed|failed|skipped|errors?|deselected|warnings?))* in [\d.]+s/`. ANSI-strip first.
   - On all-pass: return that line alone.
   - On failure: return summary line + first `FAILED tests/...` line + the short-traceback block immediately preceding it (lines between the nearest `_____ test_name _____` separators above and below).
6. `logEvent('tengu_voice_tests_run', { withFilter: bool, passed: bool, durationMs })`.
7. Return type: `{ type: 'text', value: string }` matching existing `LocalCommandCall` shape.

**Pure parser unit:** `parsePytestSummary(stdout: string): { summary: string | null, firstFailure: string | null }`. Pure function. Tested in isolation.

### 2. `/voice-status` slash command

**Purpose.** On-demand mirror of `.claude/hooks/SessionStart.sh`: report `jarvis-voice-agent.service` + `jarvis-bridge.service` is-active, last-turn timestamp, age, and the <60 s active-session warning. Useful for users to know whether `/voice-restart` is safe.

**Files.**
- New: `src/cli/src/commands/voice/status.ts`
- Modify: `src/cli/src/commands/voice/index.ts` — add `voiceStatus: Command` export
- Modify: `src/cli/src/commands.ts` — add `voiceStatusCommand` declaration + spread entry, same pattern as `voiceLogsCommand`

**Behavior.**
1. `systemctl --user is-active jarvis-voice-agent.service` (timeout 3 s) → "active" / "inactive" / "failed" / "unknown".
2. `systemctl --user is-active jarvis-bridge.service` (timeout 3 s).
3. Read `~/.local/share/jarvis/turn_telemetry.db` via `sqlite3` shell (timeout 3 s): `SELECT ts_utc FROM turns ORDER BY ts_utc DESC LIMIT 1`. Compute age = now - last_turn (epoch seconds).
4. Format multi-line text:
   ```
   voice-agent: active
   bridge:      active
   last turn:   2026-05-09T13:14:20Z (5m 12s ago)
   ```
   If age < 60 s, append: `WARNING: <60s since last turn — voice session may be active. Don't restart without asking.`
5. `logEvent('tengu_voice_status_checked', { voiceActive: bool, bridgeActive: bool, sessionActive: bool })`.

**Pure parser unit:** `formatVoiceStatus({ voice, bridge, lastTurnAt }: VoiceStatusInputs): string`. Pure function.

### 3. `voice-log-analyzer` project agent

**Purpose.** Mirror `.claude/agents/log-analyzer.md` so the jarvis CLI can dispatch the same diagnostic agent when the user reports voice misbehavior ("JARVIS is silent", "TTS leaking protocol shapes", "breaker open"). The CLI's existing agent-loader already auto-discovers project agents from `<project-root>/.jarvis/agents/<name>.md`; nothing else needs wiring.

**File.**
- New: `<repo-root>/.jarvis/agents/voice-log-analyzer.md`

**Frontmatter.**
```yaml
---
name: voice-log-analyzer
description: "Use when JARVIS is misbehaving and the symptom is in logs — 'JARVIS is silent' / 'saying gibberish' / 'wrong specialist' / 'TTS leaking protocol shapes' / 'breaker open.' Parses ~/.local/share/jarvis/logs/voice-agent.log + telemetry DB, finds the failing pattern, names a likely root cause. Phase-1 only — does NOT propose fixes."
tools: Bash, Read, Grep
color: yellow
---
```

**Body.** Same structural content as `.claude/agents/log-analyzer.md`, with two updates relative to that file:
- Log path corrected to `~/.local/share/jarvis/logs/voice-agent.log` (the 2026-05-07 move from `/tmp/`); the `.claude/` version still points to `/tmp/jarvis-voice-agent.log` and is stale.
- Add a one-line note that the CLI's `VoiceAgentStatusTool` provides a structured status view if Bash systemctl access is restricted.

### Data flow

```
user types `/voice-tests` in CLI
   └─> commands/voice/tests.ts call()
         └─> spawn <va>/.venv/bin/python -m pytest …
              └─> capture stdout (10MB cap, 120s timeout)
                   └─> parsePytestSummary(stdout)
                        └─> { summary, firstFailure } → text
                              └─> CLI renders LocalCommandCall result
```

```
user types `/voice-status` in CLI
   └─> commands/voice/status.ts call()
         ├─> systemctl --user is-active jarvis-voice-agent.service
         ├─> systemctl --user is-active jarvis-bridge.service
         └─> sqlite3 turn_telemetry.db "SELECT ts_utc..."
              └─> formatVoiceStatus({...}) → text
```

### Error handling

| Failure | Behavior |
|---|---|
| `JARVIS_VOICE_AGENT_PATH` unset and default doesn't exist | `/voice-tests` returns: "Voice agent path not found at <path>; set `JARVIS_VOICE_AGENT_PATH`." |
| `.venv/bin/python` missing | `/voice-tests` returns: "voice-agent venv missing — run …" |
| pytest spawn timeout (>120 s) | "Pytest exceeded 120 s timeout (still running). Run manually: cd <va> && .venv/bin/python -m pytest tests/" |
| pytest stdout > 10 MB | Truncate; append "[output truncated at 10 MB]". |
| `systemctl` not present (non-systemd host) | `/voice-status` reports "systemctl not available — non-systemd host?" |
| `sqlite3` shell missing | `/voice-status` reports last-turn as "unknown (sqlite3 not in PATH)". Doesn't fail. |
| Telemetry DB missing | `/voice-status` reports last-turn as "no telemetry yet". Doesn't fail. |
| Agent file load fails | The CLI's existing agent loader logs and skips; no special handling needed. |

### Testing

- **`parsePytestSummary` unit tests:** representative pytest -q outputs (all-pass, mixed, all-fail, empty, ANSI-colored). Pure function, easy to lock down.
- **`formatVoiceStatus` unit tests:** every combination of (voice active/inactive/unknown) × (bridge active/unknown) × (last-turn recent/old/missing) × (age <60 s warning trigger).
- **No integration tests.** Spawning systemctl / sqlite3 / pytest in CLI tests is brittle and slow; the seams are isolated as pure parsers.
- **No tests for the agent file** beyond the CLI's existing agent-loader doing its own validation on load.

### Observability

- `tengu_voice_tests_run` analytics event with `{withFilter, passed, durationMs}`
- `tengu_voice_status_checked` event with `{voiceActive, bridgeActive, sessionActive}`

### Security / safety

- `/voice-tests` does not eval user input — argument is passed via spawn argv array, not shell.
- `/voice-status` only invokes whitelisted commands (`systemctl is-active`, `sqlite3 SELECT`). No shell interpolation.
- All paths resolved via `path.join(os.homedir(), ...)`.
- The voice-log-analyzer agent inherits the CLI's agent permission model; tools restricted to `Bash, Read, Grep`.

---

## Out of scope (deferred)

- **Streaming pytest output.** Smart summary covers the common case; a future addition could stream long-running runs.
- **Auto-doctor agent.** A combined "diagnose voice issue + propose fix" agent. The current voice-log-analyzer is intentionally Phase-1 (diagnose only) per `.claude/agents/log-analyzer.md`.
- **Update of the stale `/tmp/jarvis-voice-agent.log` reference in `.claude/agents/log-analyzer.md`.** The Claude Code agent's path is wrong post-2026-05-07. Fixing that is a separate one-line change to `.claude/`.

---

## Verification

1. `cd src/cli && bun ./scripts/run-cli.mjs --help` — boots without import errors.
2. Inside a CLI session: `/voice-tests` returns summary on a passing suite; `/voice-tests -k consolidator` filters; `/voice-status` reports current daemon state.
3. Trigger the voice-log-analyzer subagent (e.g., "Why is JARVIS silent?") in a CLI session and confirm dispatch + diagnostic output.
4. `parsePytestSummary` and `formatVoiceStatus` unit tests pass.
