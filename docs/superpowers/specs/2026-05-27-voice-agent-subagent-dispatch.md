# Voice-agent subagent dispatch via CLI subprocess

**Date:** 2026-05-27
**Status:** spec, pre-implementation
**Author:** Ulrich + Claude
**Scope:** new tool module `src/voice-agent/tools/dispatch_agent.py`, schema registration via `tools/registry.py`, supervisor system-prompt addition in `prompts/supervisor.md`, telemetry-schema migration in `pipeline/turn_telemetry.py` (3 new columns), unit + integration tests under `src/voice-agent/tests/test_dispatch_agent.py`.

**Out of scope:** in-process AgentTool implementation (rejected); async / background dispatch with callback (rejected — sync only); restoring the pre-2026-05-20 `HandoffSubagent` / `DelegatedSubagent` / `transfer_to_*` patterns (still banned per CLAUDE.md); streaming partial subagent output to TTS (future); MCP server integration (separate spec).

## TL;DR

Voice JARVIS gains a single new tool, `dispatch_agent(subagent_type, task, description)`, that spawns `bin/jarvis` as a subprocess to run one of four CC-style named agents (`explore` / `researcher` / `code_reviewer` / `plan`). The supervisor calls it; a front-loaded ack ("Looking into that — one moment.") plays via the existing `_front_loaded_ack` pipeline so the user isn't stranded in silence; the subprocess result returns as the `tool_result` for normal turn synthesis. Synchronous wait with a per-type timeout (30s for `explore`, 90s for `researcher`, 60s for the rest). This closes item 2 of the 2026-05-27 CLI-parity gap analysis without re-introducing the in-process subagent layer the 2026-05-20 rebuild deliberately removed.

## Why this shape, why now

**Why subagents at all:** The 2026-05-27 CLI-parity scorecard (this session's earlier work) closed 4 of 6 CLI-parity gaps but left "subagent spawning" deferred. Concrete use cases the user wants: fan-out code investigation (Explore), deep web research (researcher), independent code review (code-reviewer), plan/design assistance (Plan). All four are first-class agent types in the leaked Claude Code source at `/home/ulrich/Documents/Projects/claude-code/src/tools/AgentTool/built-in/`.

**Why out-of-process via the CLI:** `bin/jarvis` (the project's Claude-Code-shaped CLI at `src/cli/`) already implements the full AgentTool surface with all four agent types. Voice JARVIS reuses that battle-tested code instead of re-implementing it in `src/voice-agent/`. Trade-off accepted: 1-3 s subprocess cold-start cost vs. orders-of-magnitude less new code to maintain.

**Why sync wait with front-loaded ack (not async callback):** Voice's natural turn shape is one supervisor reply per user input. Async dispatch with a follow-up callback turn requires new plumbing (background-event → new-turn promotion) that the user explicitly rejected at brainstorm time in favor of the simpler sync-with-ack pattern. Trade-off accepted: a 60 s researcher dispatch produces up to ~50 s of dead air after the ack plays. Mitigation: per-type timeout caps the worst case; the user has explicitly opted into this trade-off.

**Why a single tool, not four:** CC also uses a single `Task` tool with a `subagent_type` discriminator. One tool keeps the supervisor's tool surface compact; the per-type policy (timeout, ack phrase, CLI subagent flag) lives inside the dispatcher.

**Why not restore the old subagent layer:** CLAUDE.md is explicit — the 2026-05-20 rebuild removed `HandoffSubagent` / `DelegatedSubagent` / `transfer_to_*` and they are not to be restored without sign-off. This spec is the sign-off: it deliberately uses neither the old terminology nor the old architecture. It is NOT a revival; it is a fresh out-of-process tool that happens to share the "spawn fresh agents for sub-tasks" idea.

## Architecture

```
voice-agent supervisor LLM
        │
        │ tool_use: dispatch_agent(subagent_type='explore', task='find where computer_use is defined')
        ▼
src/voice-agent/tools/dispatch_agent.py::handle_dispatch_agent
        │
        ├─ _emit_front_loaded_ack("Searching the code…")   ──TTS──▶ user hears ack
        │
        ├─ argv = ["bin/jarvis", "--print", "--subagent", "Explore", "<task>"]
        │  asyncio.create_subprocess_exec(*argv, stdout=PIPE, stderr=PIPE)
        │
        ├─ await proc.communicate()   (asyncio.wait_for, timeout=30 s for Explore)
        │
        │  ┌───────────────────────────────────────────┐
        │  │ ON SUCCESS  →  return proc.stdout (text)  │
        │  │ ON TIMEOUT  →  SIGKILL, return error dict │
        │  │ ON NON-ZERO →  return error + stderr tail │
        │  └───────────────────────────────────────────┘
        │
        ▼
tool_result → supervisor LLM → voice reply → TTS
                       │
                       └─ pre-TTS confab gate sees tool_called → bypass retry
                       └─ telemetry row gets subagent_type / subagent_ms / subagent_status
```

## Tool surface

```python
TOOL_NAME = "dispatch_agent"

SCHEMA = {
    "name": "dispatch_agent",
    "description": (
        "Spawn a fresh CLI agent to handle a sub-task with isolated context. "
        "Use when the supervisor's own tool surface would drown in raw output "
        "or when a specialized agent does it better.\n\n"
        "subagent_type:\n"
        "  - 'explore'        : fast file/code search (1-5 s). Returns synthesis, not raw grep.\n"
        "  - 'researcher'     : deep web research (15-60 s). Returns synthesized answer + sources.\n"
        "  - 'code_reviewer'  : review uncommitted diff against project rules (10-30 s).\n"
        "  - 'plan'           : design how to implement a feature (10-30 s).\n\n"
        "DO NOT use for simple lookups the supervisor can handle directly. "
        "DO NOT reply 'I'll look into that' WITHOUT actually calling this tool — "
        "claiming dispatch without dispatching is confab."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "subagent_type": {
                "type": "string",
                "enum": ["explore", "researcher", "code_reviewer", "plan"],
            },
            "task":        {"type": "string", "description": "What the subagent should do, in 1-3 sentences"},
            "description": {"type": "string", "description": "Short 3-5 word label for telemetry"},
        },
        "required": ["subagent_type", "task", "description"],
    },
}
```

## Per-subagent-type policy

| `subagent_type` | Timeout (s) | Ack phrase | CLI flag value |
|---|---|---|---|
| `explore` | 30 | "Searching the code…" | `Explore` |
| `researcher` | 90 | "Looking that up online…" | `researcher` |
| `code_reviewer` | 60 | "Reviewing the diff…" | `code-reviewer` |
| `plan` | 60 | "Thinking through that design…" | `Plan` |

Ack phrases are constants in the dispatcher module, not LLM-generated. Reason: the ack must NOT claim completion ("Done — searching now") — that would trip the confab gate's own claim regex on the wrapping turn. Short factual descriptors of what's about to happen.

The CLI flag value (rightmost column) is the exact string the `bin/jarvis` subprocess expects. Locked at implementation time by reading the CC source at `/home/ulrich/Documents/Projects/claude-code/src/tools/AgentTool/built-in/` and the CLI's matching agent registry. If the CLI uses different identifiers, the policy table is the single place to fix.

## Dispatch contract details

**Subprocess invocation:**
- `argv = [str(BIN_JARVIS), "--print", "--subagent", flag_value, task_text]`
- `BIN_JARVIS` resolves from `Path(__file__).resolve().parents[3] / "bin" / "jarvis"` (no env coupling — predictable across worktrees)
- `asyncio.create_subprocess_exec(*argv, stdin=DEVNULL, stdout=PIPE, stderr=PIPE)` — argv list, no shell, no string interpolation; safe under any task text
- Inherits the voice-agent's env (so the subprocess has API keys + memory dir paths)
- Sets `JARVIS_NO_BANNER=1` and similar low-noise flags to keep the subprocess's stdout to its synthesized answer only

**Result shape:**
- On success → `tool_result` is the subprocess's stdout, stripped of trailing whitespace
- On timeout → `tool_result` is JSON `{"error": "subagent <type> ran too long (><timeout>s); aborted"}` — supervisor handles gracefully
- On non-zero exit → `tool_result` is JSON `{"error": "subagent <type> failed: <stderr tail (last 200 chars)>"}`
- On subprocess-spawn failure → `tool_result` is JSON `{"error": "could not start <bin/jarvis>: <exception>"}`

In all cases the `tool_result` is returned to the supervisor, which formulates a voice-friendly reply. The supervisor MUST NOT pretend success when it sees an error — pre-TTS confab gate is the backstop if it tries.

**Voice barge-in during subagent:**
If the user starts speaking while a subagent is running, the existing barge-in path interrupts in-flight TTS but does NOT kill the subprocess. The dispatcher tags each invocation with a session-id captured at dispatch time; when the subprocess finishes, the dispatcher checks the current session-id. If it differs (turn was abandoned), the result is discarded and a no-op tool_result is returned. The subprocess is allowed to complete because killing it mid-flight can leave orphaned browser sessions / temp files.

**Memory + state isolation:**
- Subprocess does NOT receive the voice supervisor's chat_ctx. It gets a fresh CLI session with just the task description.
- Subprocess writes memory entries to the same shared dir (`~/.claude/projects/.../memory/`). Standard file-based memory layer handles concurrency atomically.
- Subprocess does NOT have access to the voice supervisor's TTS / mic / desktop tools. That's intentional — a sub-task should run isolated.

## Telemetry

Add three columns to `turn_telemetry.turns` via the same gentle-ALTER-TABLE pattern used for past additions (`confab_check_state`, `confab_pattern_matched`, `confab_retry_models`, etc.):

```sql
ALTER TABLE turns ADD COLUMN subagent_type TEXT;
ALTER TABLE turns ADD COLUMN subagent_ms INTEGER;
ALTER TABLE turns ADD COLUMN subagent_status TEXT;
```

All nullable. Existing rows unaffected. Per-turn semantics: if a turn spawns multiple subagent dispatches (rare), the LAST one's values land in the row — sufficient for operator visibility without per-call sub-rows.

`subagent_status` values: `success`, `timeout`, `error`, `aborted` (session-id mismatch on completion).

Migration runs at startup in `pipeline/turn_telemetry.init_db()`, same place every prior column addition lives. Idempotent.

## Confab-gate interaction

The pre-TTS confab gate's `should_gate` checks `tool_calls` non-empty → bypasses with reason `tool_called`. `dispatch_agent` is a registered tool, so the supervisor's turn shows `tool_calls=[dispatch_agent(...)]` and the gate correctly bypasses. The retry chain never fires on a legitimate dispatch. ✅

The supervisor's voiced reply (synthesized from the subagent's text) is itself subject to the gate's claim-pattern checks. If the supervisor says "Done — found 3 places" without any prior tool call in this turn, the gate would catch it. But since `dispatch_agent` IS a tool call in this turn, the gate sees evidence and passes the reply. The act-don't-narrate clause in the tool description warns the supervisor against narrating "I'll look into that" without invoking, which the gate's regex also catches if it slips through.

## Supervisor system-prompt addition

Add a short paragraph to `src/voice-agent/prompts/supervisor.md` under the existing tool-routing guidance:

```
SUBAGENT DISPATCH — dispatch_agent
Use dispatch_agent(subagent_type=...) when:
  - User asks "find / search / where is" anything in the codebase → 'explore'
  - User asks "look up / research / what's the latest on" anything online → 'researcher'
  - User asks "review my diff / check my changes" → 'code_reviewer'
  - User asks "how should I implement / design / approach" anything → 'plan'

Do NOT use for simple lookups you can handle directly with read_file / web_search / etc.
Do NOT chain multiple dispatch_agent calls in one turn — slow.
The ack ("Searching the code…") plays automatically; do not narrate it yourself.
```

## Implementation file map

| File | Action | Lines (est.) |
|---|---|---|
| `src/voice-agent/tools/dispatch_agent.py` | NEW — handler, schema, registry.register call | ~180 |
| `src/voice-agent/tools/_adapter.py` | no change — auto-discovers via `glob("tools/*.py")` | 0 |
| `src/voice-agent/pipeline/turn_telemetry.py` | ADD 3 ALTER TABLE entries in `init_db()` + log_turn writer args | ~15 |
| `src/voice-agent/prompts/supervisor.md` | ADD subagent-dispatch routing paragraph | ~10 |
| `src/voice-agent/tests/test_dispatch_agent.py` | NEW — unit (mock subprocess) + integration (real `bin/jarvis --print "echo"` with 10s timeout) | ~150 |

Total: ~355 lines of net additions. No deletions. No edits to load-bearing files (sanitizers, confab_detector, jarvis_agent.py main body).

## Risk

- **CLI flag-set mismatch.** The exact `bin/jarvis --print --subagent <type>` invocation needs verification at implementation time. If `bin/jarvis` doesn't yet support `--print` non-interactive mode or doesn't accept `--subagent` to pick the agent type, a small CLI-side patch may be needed. Mitigation: read CLI source first, lock the invocation, fail loudly if the CLI doesn't support it. Worst case: this spec doubles to include CLI changes; flag at first implementation step.
- **Subprocess proxy collision.** `bin/jarvis` starts its own proxy on `:4000`. If the voice-agent is mid-conversation and the user is also running `jarvis` in another terminal, ports collide. Mitigation: pass `JARVIS_NO_PROXY=1` or `JARVIS_PROXY_PORT=0` to the subprocess so it reuses or skips proxy bring-up. Detail to confirm at implementation.
- **Dead air on slow subagents.** A 90 s `researcher` dispatch (the timeout from Section 4) + the ~800 ms ack means up to ~89 s of silence after the ack plays. User has accepted this trade-off; the per-type timeout caps the worst case. Future spec could add an async-callback path.
- **Subagent fabrication.** A buggy CLI subagent could return plausible-looking made-up output. Mitigation: the supervisor's reply still passes through the pre-TTS confab gate; if the gate trips, the retry chain catches it. Defense-in-depth.
- **Long subagent output trashing TTS.** A 500-word researcher synthesis is fine for the CLI but too long to voice. Supervisor's job is to summarize before TTS; if it dumps the full output, the gate's pattern regex catches the typical "Done — here's what I found:" claim shape only if the supervisor narrates that way. Future hardening: a `summarize_for_voice=true` hint on the tool call. Out of scope this spec.

## Test plan

- **Unit tests** (`tests/test_dispatch_agent.py`):
  - `test_explore_success`: mock subprocess returns "Found at file.py:42", assert tool_result echoes it cleanly.
  - `test_timeout_kills_subprocess`: mock subprocess sleeps past timeout, assert SIGKILL fired + tool_result is the timeout JSON.
  - `test_non_zero_exit`: mock subprocess exits 1 with stderr "boom", assert tool_result error includes "boom".
  - `test_unknown_subagent_type_rejected`: schema validation rejects `subagent_type="nonsense"` before subprocess runs.
  - `test_session_id_mismatch_aborts`: simulate session change mid-dispatch, assert tool_result is no-op.
  - `test_argv_no_shell_interpolation`: pass `task="; rm -rf /"` — assert it lands as a single argv element, never reaches a shell.

- **Integration test** (skipped if `bin/jarvis` not bootstrapped on CI):
  - `test_real_dispatch_explore`: actually run `bin/jarvis --print --subagent Explore "find dispatch_agent.py"` with a 30 s timeout, assert stdout mentions the file path. Real subprocess, real CLI, real round-trip.

- **Live smoke** (post-implementation):
  - Ask voice JARVIS "find where computer_use is defined" — expect an Explore dispatch returning the file path within ~5 s.
  - Ask "look up the latest on browser-use" — expect a researcher dispatch with synthesis + dead-air mitigated by the ack.

## References

- 2026-05-27 CLI-parity scorecard: this session's earlier brainstorm output (in-session, not yet a doc).
- 2026-05-20 rebuild rationale: `~/.claude/projects/-home-ulrich-Documents-Projects-jarvis/memory/project_jarvis_smartness_session_2026_05_10.md` (subagent tree torn down for stability).
- 2026-05-24 confab gate spec: `docs/superpowers/specs/2026-05-24-pre-tts-confab-gate-design.md` (explicitly chose NOT to restore subagent layer; this spec is the deferred decision).
- CC's AgentTool surface: `/home/ulrich/Documents/Projects/claude-code/src/tools/AgentTool/built-in/` (leaked source — reference shape only).
- JARVIS CLI: `src/cli/` and `bin/jarvis` (subprocess target).
- Existing tool registry: `src/voice-agent/tools/registry.py` + `src/voice-agent/tools/_adapter.py::load_all_livekit_tools` (auto-discovers `tools/*.py`).
- Front-loaded ack pipeline: `src/voice-agent/jarvis_agent.py` `_front_loaded_ack` helper (already plumbed for the confab gate; this tool reuses it).
- Pre-TTS confab gate: `src/voice-agent/pipeline/pre_tts_confab_gate.py` (this spec interacts with it as a normal tool call).
