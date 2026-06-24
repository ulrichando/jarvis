# JARVIS Source-Code Self-Modification — Design Spec

> Created 2026-05-24. **Plane 3** of the three-plane self-modification
> architecture. **Plane 1 + Plane 2** (memory + procedure + skill self-
> authoring) shipped earlier today as [Spec A](2026-05-24-jarvis-memory-and-procedure-loop-design.md);
> this spec is the source-code self-modification follow-up.

## Context

After Spec A landed (17 commits, T1–T14 done, `JARVIS_SKILL_REVIEW_APPLY=1`
live), JARVIS can now:

- Save durable facts, preferences, and procedures via the `memory()`
  tool (deliberate path) AND via the autonomous reviewer's apply path
  (background, every turn).
- Self-author skills when the reviewer detects reusable patterns.
- Reject save-confabs ("I'll remember" without a memory tool call).
- Replay saved procedures on intent match.

What JARVIS still **cannot** do:

1. **Edit his own Python source code.** No autonomous changes to
   `pipeline/*.py`, `tools/*.py`, `jarvis_agent.py`, etc.
2. **Add new tools.** The tool registry is static at startup; new
   capabilities require source changes.
3. **Patch persistent bugs.** When the same correction recurs across
   sessions (e.g. *"stop saying 'sir'"* despite the persona prompt
   already removing it), the only fix path is git + human edit.
4. **Fix a tool gap.** When a multi-step manual workaround repeats
   (e.g. *"play music = sequence of `terminal('playerctl …')`"*), there's
   no path to "draft a `play_music` tool".
5. **Rewrite his own prompts.** `soul.md` / `supervisor.md` are git-only.

Spec A's reviewer LLM (llama-3.1-8b-instant) can express *intent* —
"this would be a `skill_create` if I could", "this would be a `memory`
write" — but it has no mechanism to propose a code-level change. Spec B
adds that mechanism: a **gated PR loop via CLI delegation**.

## Evidence: pre-existing infrastructure to lean on

| Component | Status | Use for Spec B |
|---|---|---|
| `bin/jarvis` (Claude-Code-shaped CLI) | active, `-p` non-interactive mode supported (`src/cli/src/interactiveHelpers.tsx`) | spawn target for autonomous code edits |
| `~/.jarvis/evolution_log.jsonl` | exists; 86 entries currently — all pytest-tmp pollution (`/tmp/pytest-*` paths) | repurpose for production audit trail after a filter pass |
| `pipeline/skill_review.py::autonomous_review_turn` | fires per turn; APPLY=1 live | extend with a 4th `_proposal_kind` for code-mod intent (proposal-only; no apply) |
| `confab_detector.py::looks_like_confabulation` | flags save-claim confabs | reuse pattern: detect tool-claim confabs ≥3 times → escalate to code-mod intent |
| `pipeline/file_memory.py` | atomic-replace + flock | reuse for the new auto-mod artifact JSON |
| `turn_telemetry.db` | 36 columns post-Spec-A | add 2 new tables for pattern tracking |
| `setup/systemd/jarvis-voice-agent.service` | env-driven | add `JARVIS_AUTOMOD_ENABLED` master kill switch |

## Goals

After Spec B ships, the following scenarios work:

1. **Explicit user request → proposal.** *"Jarvis, fix the bug where you keep
   saying 'sir' at the end of replies"* → supervisor calls
   `propose_code_mod(intent=...)` → background CLI subprocess branches
   from master, edits the relevant prompt/source, runs pytest, opens an
   auto-mod artifact for your review. You merge with one command.
2. **Repeated correction → proposal.** When you correct the same
   behaviour ≥3 times across sessions (tracked in
   `turn_telemetry.recurring_corrections`), the autonomous reviewer
   escalates: proposes a code-mod intent in the queue. After a 4th
   recurrence, the spawner picks it up. You see the artifact.
3. **Tool-gap pattern → proposal.** Same multi-step `terminal(...)` /
   `browser_task(...)` workaround used ≥3 times for the same intent →
   the reviewer proposes adding a dedicated tool. You review the diff +
   tests and merge if it looks right.
4. **Confab self-flag → proposal.** Save-claim confab fires ≥3 times in
   a week → the reviewer proposes a prompt patch (e.g. *"strengthen the
   memory-tool description's 'silent save' instruction"*).
5. **One-keystroke merge / revert.** `bin/jarvis-automod merge <id>`
   merges the auto-mod branch into master with `--ff-only` + tags the
   commit. `bin/jarvis-automod revert <sha>` does `git revert` if a
   merged auto-mod turns out to be wrong.
6. **No surprise restarts.** Service restart after merge is operator-
   driven, with the existing 60s-idle check (CLAUDE.md operational rule).

## Non-goals

- **Auto-merge.** Every code-mod artifact requires explicit
  `bin/jarvis-automod merge <id>` from you. No silent merges, no
  scheduled merges.
- **Edits outside `src/voice-agent/`.** The CLI spawner enforces a hard
  blocklist; any artifact that touches `src/voice-agent/desktop-tauri/`,
  `src/cli/`, `src/web/`, `setup/`, or `docs/` outside Spec-B's
  whitelist is rejected before review.
- **Edits to safety surfaces.** Blocked files (always): `sanitizers/`,
  `confab_detector.py`, `regression-prevention.md`, the self-mod loop
  itself (`pipeline/automod/*`), CLAUDE.md, MEMORY.md. JARVIS cannot
  weaken the constraints that contain JARVIS.
- **Edits to tests deletion.** The CLI spawner refuses any diff that
  REMOVES test files or assertions (it can ADD tests but never weaken
  them). Test deletion is human-only.
- **Auto-rollback on test failure.** The CLI is allowed to commit only
  on green tests; on red tests it discards the branch and logs the
  failure. No "ignore the red and commit anyway" path.
- **Network access during edits.** The CLI subprocess inherits
  `JARVIS_AUTOMOD_NO_NETWORK=1`; only the LLM proxy is reachable.
- **Reviewer-prompt edits via auto-mod.** The autonomous reviewer's own
  prompt (`pipeline/skill_review.py::_REVIEW_PROMPT`) is on the
  blocklist — JARVIS cannot rewrite the prompt that decides what to
  rewrite. (Soul-style invariant.)
- **CLI codebase edits.** `src/cli/` is off-limits per CLAUDE.md and
  reinforced here. The CLI is a delegated worker, not a target.

## Sourced principles

- **[Anthropic — Claude's character](https://www.anthropic.com/research/claude-character):**
  honest about capability limits; refuse politely; cannot act outside
  declared scope. Self-mod intents must be transparent in artifact
  output.
- **[OpenAI Model Spec — 2025-04-11](https://model-spec.openai.com/2025-04-11.html)**
  § Tool use + § Safety: high-blast-radius actions require explicit
  confirmation; do not chain irreversible operations without checkpoints.
- **EU AI Act, Article 14** — high-risk system transparency: the user
  must be able to inspect, correct, override the AI's decisions. The
  artifact JSON + diff + test output give you full inspection before
  merge.
- **Reversibility-first design (CLAUDE.md operational rules):** "Don't
  take risky actions as a shortcut to simply make it go away. Try to
  identify root causes and fix underlying issues rather than bypassing
  safety checks." Spec B preserves this via the test-gate (no commit
  on red), the blocklist (no edits to safety surfaces), and the manual
  merge gate.
- **Spec A's two-gate design:** the regex is liberal but the supervisor
  LLM is the second gate. Spec B has THREE gates: pattern-detection
  threshold (≥3 occurrences), test pass (green pytest), human merge.

## Capability surface — ground truth

What's editable, what's spawned, what's audited:

| Layer | Mechanism | Status today | Post-Spec-B |
|---|---|---|---|
| Code edits inside `src/voice-agent/` | direct file write | possible via voice-agent's own `write_file`/`patch` tools but never used autonomously | CLI subprocess does the edits, NOT the voice-agent's tool loop (keeps voice latency clean) |
| Branch + commit | `git` | manual | `bin/jarvis-automod-impl` (new shell helper) does branch + commit; CLI sees a clean checkout |
| Test execution | `.venv/bin/python -m pytest` | manual | CLI runs it; on red the auto-mod is discarded |
| Artifact storage | n/a | n/a | `~/.jarvis/auto-mods/<id>.json` + `~/.jarvis/auto-mods/throttle.json` |
| Audit log | `~/.jarvis/evolution_log.jsonl` | 86 polluted entries from pytest | repurposed for production self-mod events; filter rejects `/tmp/pytest-*` paths on write |
| Pattern detection | n/a (Spec A reviewer flags `kind=skill_patch` but can't see recurring corrections cross-session) | n/a | new tables `recurring_corrections` + `tool_gap_patterns` in `turn_telemetry.db`; populated by autonomous reviewer; queried by automod pattern detector |
| Voice tool surface | `memory()`, `procedure(...)`, `skill_*`, etc. | Spec A live | + `propose_code_mod(intent: str, rationale: str)` registered when `JARVIS_AUTOMOD_ENABLED=1` |
| Service restart | systemd | manual + 60s-idle check | unchanged — no auto-restart in Spec B |

## Architecture — eight deltas

```
                          USER UTTERANCE / RECURRING PATTERN
                                   │
                                   ▼
        ┌──────────────────────────────────────────────────┐
        │  TURN → autonomous_review_turn (existing, Spec A) │
        │                                                  │
        │  + Delta 1: pattern-detect 4 classes:            │
        │    - correction repeat (≥3 same correction)      │
        │    - tool-gap repeat (≥3 multi-step workaround)  │
        │    - confab self-flag (≥3 save-claim confabs)    │
        │    - explicit user request via propose_code_mod  │
        └──────────────────────┬───────────────────────────┘
                               │
                               ▼
              ┌────────────────────────────────────┐
              │  Delta 2: intent queue + throttle  │
              │  ~/.jarvis/auto-mods/queue.jsonl   │
              │  + throttle.json (3/day cap)       │
              │  + blocklist enforcement (paths)   │
              └─────────────────┬──────────────────┘
                                │ on gate-pass
                                ▼
              ┌────────────────────────────────────┐
              │  Delta 3: CLI subprocess spawner   │
              │  pipeline/automod/spawner.py        │
              │  spawns: bin/jarvis-automod-impl    │
              │    └─ bin/jarvis -p "intent + rules"│
              │  in background asyncio task         │
              └─────────────────┬──────────────────┘
                                │
                                ▼
              ┌────────────────────────────────────┐
              │  CLI (Claude-Code-shaped) does:    │
              │  1. branch from master              │
              │  2. edit src/voice-agent/ ONLY      │
              │  3. run .venv pytest                │
              │  4. on green: commit + write artifact│
              │  4'. on red: discard + log failure  │
              └─────────────────┬──────────────────┘
                                │
                                ▼
              ┌────────────────────────────────────┐
              │  Delta 4: artifact + audit         │
              │  ~/.jarvis/auto-mods/<id>.json     │
              │  ~/.jarvis/evolution_log.jsonl     │
              └─────────────────┬──────────────────┘
                                │
                                ▼ (user-driven)
              ┌────────────────────────────────────┐
              │  Delta 5: review CLI               │
              │  bin/jarvis-automod                │
              │   ├ list                            │
              │   ├ show <id>                       │
              │   ├ merge <id>  (--ff-only)         │
              │   ├ reject <id>                     │
              │   └ revert <sha>                    │
              └────────────────────────────────────┘
```

The 8 deltas in rollout order:

| # | Delta | What | Files | Risk |
|---|---|---|---|---|
| 1 | **Pattern detection + queue** | `pipeline/automod/patterns.py` (new) — scans `turn_telemetry.db` for the 4 pattern classes. `recurring_corrections` + `tool_gap_patterns` tables added via idempotent ALTER. Emits intent records to `~/.jarvis/auto-mods/queue.jsonl`. Background asyncio task, fires every N minutes (default 30). | new | low — read-only of telemetry; writes only to JSONL queue |
| 2 | **Throttle + blocklist** | `pipeline/automod/throttle.py` (new) — drains queue.jsonl, applies daily cap (3 PRs/day) + per-topic in-flight limit (1) + path blocklist + content blocklist (no edits to lines containing `pytest.skip` / `@pytest.mark.skipif` / similar). | new | medium — gate is critical; tests cover every blocklist class |
| 3 | **CLI subprocess spawner** | `pipeline/automod/spawner.py` (new) — on gate-pass, spawns `bin/jarvis-automod-impl` via `asyncio.create_subprocess_exec`. Inherits restricted env (`JARVIS_AUTOMOD_NO_NETWORK=1`, `JARVIS_AUTOMOD_FILE_ALLOWLIST=src/voice-agent`). Captures stdout/stderr to log. Timeouts at 10 min. | new | high — code editing surface; mitigated by file allowlist + test gate |
| 4 | **Artifact + audit** | `pipeline/automod/artifact.py` (new) — schema validation + atomic write to `~/.jarvis/auto-mods/<id>.json`. Audit-log helper writes JSONL entries to `~/.jarvis/evolution_log.jsonl` with a pytest-tmp path filter. | new | low |
| 5 | **Review CLI** | `bin/jarvis-automod` (new shell entry) + `src/voice-agent/tools/automod_cli.py` (Python impl). Subcommands: `list / show / merge / reject / revert`. `merge` uses `git merge --ff-only`; `revert` uses `git revert`. | new | medium — git ops; ff-only ensures no surprise merge commits |
| 6 | **Voice tool surface** | `src/voice-agent/tools/code_mod.py` (new) — `propose_code_mod(intent, rationale)` tool. Registered when `JARVIS_AUTOMOD_ENABLED=1`. Writes to the same queue.jsonl. | new | low — supervisor LLM gates content; throttle/blocklist gate paths |
| 7 | **Test gate** | `pipeline/automod/test_gate.py` (new) — wraps pytest invocation; rejects diffs that delete tests; verifies pytest exited 0; captures full output for artifact. | new | medium |
| 8 | **Rollback infrastructure** | `bin/jarvis-automod revert <sha>` (in Delta 5's CLI) + rehearsal via `tests/test_automod_revert.py`. | within Delta 5 + new test | low |

## Detailed design

> **Track naming convention.** "Delta N" = stable identifier matching
> the table column. Rollout order is in the Rollout sequencing section.

### Delta 1 — Pattern detection + intent queue

**Schema migration** (`pipeline/turn_telemetry.py::init_db`):

```python
# 2026-05-24 — Spec B (Plane 3) — pattern tracking for auto-mod proposals.
_add_column_if_missing(conn, "turns", "correction_signal", "TEXT")
# correction_signal: extracted lowercase form of any user correction in
# this turn (e.g. "stop saying sir", "too verbose"). NULL when no correction
# detected. Reused by Delta 1's pattern detector.

conn.executescript("""
    CREATE TABLE IF NOT EXISTS recurring_corrections (
        signal TEXT PRIMARY KEY,
        first_seen TEXT NOT NULL,
        last_seen TEXT NOT NULL,
        count INTEGER NOT NULL DEFAULT 1,
        proposed_at TEXT,  -- NULL until pushed to queue
        resolved_at TEXT   -- NULL until the proposal is merged/rejected
    );
    CREATE TABLE IF NOT EXISTS tool_gap_patterns (
        intent_hash TEXT PRIMARY KEY,
        canonical_intent TEXT NOT NULL,
        first_seen TEXT NOT NULL,
        last_seen TEXT NOT NULL,
        count INTEGER NOT NULL DEFAULT 1,
        sample_tools_json TEXT,
        proposed_at TEXT,
        resolved_at TEXT
    );
""")
```

**Pattern detector** (`pipeline/automod/patterns.py`):

```python
async def scan_patterns_and_emit() -> int:
    """Scan turn_telemetry for 4 pattern classes. Emit intents for any
    that crossed the ≥3-occurrence threshold AND haven't been proposed yet.

    Returns number of intents emitted. Pure read on telemetry; writes
    only to queue.jsonl + telemetry's proposed_at column.

    Fires every 30 min via a background asyncio task scheduled at
    voice-agent startup (sibling to fire_self_improvement)."""
```

4 pattern classes:

1. **Correction repeat** — scan `turns` for `correction_signal IS NOT NULL`,
   GROUP BY signal, count ≥ 3 + first_seen older than 24h + not already
   proposed.
2. **Tool-gap repeat** — scan for TASK turns with tool_call_count ≥ 3
   that have a recognizable intent verb (reusing
   `_INTENT_VERB_RE` from Spec A Task 9), hash the (verb, object) → bucket
   into `tool_gap_patterns`. ≥ 3 buckets with similar hashes + no
   existing tool that names that intent.
3. **Confab self-flag** — count `turns WHERE confab_check_state =
   'save_claim'` in last 7 days; ≥ 3 → emit a prompt-strengthening intent.
4. **Explicit request** — handled by Delta 6's `propose_code_mod()` tool;
   bypasses pattern detection entirely.

**Intent record** (queue.jsonl):

```json
{"id": "automod-2026-05-24-001",
 "kind": "correction|tool_gap|confab|explicit",
 "intent": "stop saying 'sir' at end of replies — find and remove from prompts",
 "rationale": "user corrected 3 times across sessions 5/22, 5/23, 5/24",
 "evidence_ts": ["2026-05-22T...", "2026-05-23T...", "2026-05-24T..."],
 "proposed_paths_hint": ["src/voice-agent/prompts/soul.md", "src/voice-agent/prompts/supervisor.md"],
 "created_at": "2026-05-24T20:00:00Z"}
```

### Delta 2 — Throttle + blocklist

**Gates applied per intent** (`pipeline/automod/throttle.py::admit_intent`):

```python
HARD_BLOCKLIST_PATHS = (
    "src/voice-agent/sanitizers/",        # safety surface
    "src/voice-agent/confab_detector.py", # safety surface
    "src/voice-agent/pipeline/automod/",  # the self-mod loop itself
    "CLAUDE.md",
    ".claude/rules/regression-prevention.md",
    "src/voice-agent/prompts/soul.md",    # persona — git-only per CLAUDE.md
    "MEMORY.md",                          # user memory (per .claude/projects/)
    "USER.md",
)

ALLOWED_PATH_PREFIX = "src/voice-agent/"

# JARVIS_AUTOMOD_BLOCKLIST_EXTRA can append more (comma-separated paths).

DAILY_PR_CAP = int(os.environ.get("JARVIS_AUTOMOD_DAILY_CAP", "3"))
IN_FLIGHT_PER_TOPIC_CAP = 1
```

`admit_intent(intent)` returns `(admit: bool, reason: str)`. Reasons for
rejection: `"daily_cap_reached"`, `"topic_in_flight"`,
`"blocked_path:<path>"`, `"path_outside_allowed_prefix"`, `"empty_intent"`.

The blocklist is **advisory** at intent emission time and **enforced** at
the spawner's diff-check step (Delta 7): even if a wrong path slips
through, the spawner refuses to commit a diff touching a blocked path.

### Delta 3 — CLI subprocess spawner

**`pipeline/automod/spawner.py`:**

```python
async def spawn_for_intent(intent: dict) -> str:
    """Spawn bin/jarvis-automod-impl in the background. Returns artifact
    id on launch (artifact starts in status='spawning'); the implementer
    transitions it to 'pending' on green test or 'failed' on red.

    Never blocks. Subprocess runs with restricted env + 10-min timeout.
    Output streamed to ~/.jarvis/auto-mods/<id>.log."""
```

**`bin/jarvis-automod-impl`** (new shell helper):

```bash
#!/usr/bin/env bash
# Wrapper invoked by spawner.py. Restricts the inherited CLI session:
#   - hard CWD = project root
#   - file-allowlist via prompt injection (CLI respects "edit only in X")
#   - JARVIS_AUTOMOD_NO_NETWORK passes through to skip non-essential calls
#   - timeout 10 min (CLI side)
set -euo pipefail

INTENT_FILE="$1"  # path to ~/.jarvis/auto-mods/<id>.intent.txt
ID=$(basename "$INTENT_FILE" .intent.txt)
ARTIFACT="$HOME/.jarvis/auto-mods/$ID.json"
LOG="$HOME/.jarvis/auto-mods/$ID.log"

cd /home/ulrich/Documents/Projects/jarvis

BRANCH="automod/$ID"
git checkout master >>"$LOG" 2>&1
git pull --ff-only >>"$LOG" 2>&1 || true
git checkout -b "$BRANCH" >>"$LOG" 2>&1

PROMPT="$(cat "$INTENT_FILE")
RULES:
- Edit ONLY files under src/voice-agent/.
- Do NOT touch: $(printf '%s, ' "${HARD_BLOCKLIST_PATHS[@]}").
- Do NOT delete tests or weaken assertions; you may ADD tests.
- Run cd src/voice-agent && .venv/bin/python -m pytest tests/ -q before committing.
- If tests fail: do NOT commit. Print the failure summary.
- If tests pass: git add only the files you edited (no git add -A). Commit
  with a Conventional Commit message describing the intent.
- DO NOT push.
- DO NOT change CLAUDE.md, regression-prevention.md, sanitizers/, or
  confab_detector.py — those are explicitly off-limits.
- Stop after one commit. Do not iterate."

# Spawn the CLI in non-interactive mode.
export JARVIS_AUTOMOD_NO_NETWORK=1
timeout 600 bin/jarvis -p "$PROMPT" >>"$LOG" 2>&1 || true

# Capture HEAD + diff + test output into the artifact.
python3 /home/ulrich/Documents/Projects/jarvis/src/voice-agent/pipeline/automod/finalize.py "$ID" "$BRANCH"
```

The finalize.py script:
1. Detects whether a commit landed (`git rev-parse HEAD` differs from
   `master`).
2. If yes: computes diff, validates against blocklist, writes artifact
   JSON with status=`pending`.
3. If no commit (CLI gave up or tests red): writes artifact JSON with
   status=`failed` + log excerpt.

### Delta 4 — Artifact + audit

**Artifact schema** (`~/.jarvis/auto-mods/<id>.json`):

```json
{
  "id": "automod-2026-05-24-001",
  "kind": "correction",
  "intent": "stop saying 'sir' …",
  "branch": "automod/automod-2026-05-24-001",
  "parent_sha": "abc123…",
  "head_sha": "def456…",
  "files_changed": ["src/voice-agent/prompts/supervisor.md"],
  "diff_summary": "+2/-5",
  "test_output_tail": "2392 passed, 1 skipped in 67s",
  "status": "pending|merged|rejected|failed|expired",
  "created_at": "2026-05-24T20:00:00Z",
  "merged_at": null,
  "merged_by": null,
  "rejected_at": null,
  "rejection_reason": null,
  "revert_sha": null
}
```

`~/.jarvis/auto-mods/<id>.log` holds the full CLI subprocess output for
manual inspection.

**Audit log** (`~/.jarvis/evolution_log.jsonl`):

```json
{"ts": "2026-05-24T20:00:00Z", "kind": "automod_proposed", "id": "automod-2026-05-24-001", "intent_class": "correction"}
{"ts": "2026-05-24T20:08:32Z", "kind": "automod_committed", "id": "automod-2026-05-24-001", "head_sha": "def456…"}
{"ts": "2026-05-24T21:14:00Z", "kind": "automod_merged", "id": "automod-2026-05-24-001", "merge_sha": "ghi789…"}
{"ts": "2026-05-26T11:00:00Z", "kind": "automod_reverted", "id": "automod-2026-05-24-001", "revert_sha": "jkl012…"}
```

Filter on write: paths starting with `/tmp/pytest-` are dropped (the
existing pollution source documented in Spec A).

### Delta 5 — Review CLI (`bin/jarvis-automod`)

Subcommands:

```
$ jarvis-automod list
ID                              KIND         STATUS    AGE    INTENT
automod-2026-05-24-001          correction   pending   12m    stop saying 'sir' at end of replies
automod-2026-05-24-002          tool_gap     pending    4m    add play_music tool (terminal playerctl workaround)

$ jarvis-automod show automod-2026-05-24-001
[displays the full artifact JSON + diff + test output]

$ jarvis-automod merge automod-2026-05-24-001
Checking artifact… OK.
Checking diff vs HEAD blocklist… OK.
Running git merge --ff-only automod/automod-2026-05-24-001…
Merged. SHA: ghi789…
[NEXT] Restart the service to pick up the change:
       systemctl --user daemon-reload && systemctl --user restart jarvis-voice-agent.service
       (check turn_telemetry.db for >60s idle first)

$ jarvis-automod reject automod-2026-05-24-001 "wrong scope; should patch soul.md not supervisor.md"
Rejected. Branch automod/automod-2026-05-24-001 deleted.

$ jarvis-automod revert ghi789
Running git revert ghi789…
Revert SHA: jkl012… Artifact updated.
```

**Hard constraint:** `merge` uses `git merge --ff-only` and aborts if
fast-forward isn't possible (means master moved during review — user
rebases manually). No merge commits, no force-merges.

### Delta 6 — Voice tool surface

**`src/voice-agent/tools/code_mod.py`** (new):

```python
CODE_MOD_SCHEMA = {
    "name": "propose_code_mod",
    "description": (
        "Propose a source-code modification when no skill/memory/procedure "
        "can fix the issue. Use SPARINGLY — only when the user explicitly "
        "asks you to fix a recurring bug, add a tool, or patch a prompt. "
        "The proposal opens a branch + runs tests + writes an artifact "
        "for the user to manually merge. Do NOT use for routine memory/"
        "preference saves (use memory() instead) or skill authoring (the "
        "autonomous reviewer handles those).\n\n"
        "Required: intent (one-sentence description of the change), "
        "rationale (why this needs code, not memory/skill)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "intent": {"type": "string"},
            "rationale": {"type": "string"},
        },
        "required": ["intent", "rationale"],
    },
}
```

Registration gated by `JARVIS_AUTOMOD_ENABLED=1`. When unset, the tool
isn't in the supervisor's surface — explicit-request path is dark.

### Delta 7 — Test gate

The wrapper in `bin/jarvis-automod-impl` is the first gate (CLI side).
A second gate runs server-side in `pipeline/automod/finalize.py`:

1. Re-run `.venv/bin/python -m pytest tests/ -q` in a clean working tree
   to confirm the CLI's report.
2. Diff-validate: no path matches `HARD_BLOCKLIST_PATHS`; no diff line
   matches `^-.*def test_` or `^-.*pytest.skip` (no test deletion / no
   new skip markers).
3. Diff-size check: total diff < 2000 lines AND fewer than 5 files
   changed (whichever limit hits first). The file-count gate catches
   scope creep ("refactor half the codebase") better than line count
   alone. Configurable via `JARVIS_AUTOMOD_MAX_FILES=5` /
   `JARVIS_AUTOMOD_MAX_DIFF_LINES=2000`.
4. On any failure: set artifact status=`failed`, log to evolution_log,
   delete branch.

### Delta 8 — Rollback infrastructure

`bin/jarvis-automod revert <sha>`:

```bash
git revert --no-edit <sha>
# Updates artifact JSON: status='reverted', revert_sha=<new-sha>
# Logs to evolution_log.jsonl: kind=automod_reverted
```

The revert produces a new commit on master (preserves history). A
follow-up `jarvis-automod list --reverted` shows all auto-mods that
were reverted, for trend analysis.

**`tests/test_automod_revert.py`** rehearses the full
propose → commit → merge → revert cycle against a tmp git repo, to keep
the rollback path tested.

## Threat model

Source-code self-mod is the **highest-blast-radius surface** in the
JARVIS stack. The threat model:

| # | Threat | Vector | Mitigation |
|---|---|---|---|
| T1 | Pattern detector + spawner runs amok (writes 100s of PRs/day) | Bug in detector counts everything as a recurrence; spawner has no rate limit | Daily cap (3 PRs/day) + per-topic in-flight (1) + emergency kill `JARVIS_AUTOMOD_ENABLED=0` + master kill via removing the systemd Environment line + cap-bypass requires editing throttle.py (which is on the blocklist) |
| T2 | CLI edits a blocked file (safety surface, prompts, tests) | LLM ignores the rule prompt despite explicit instructions | Three-layer enforcement: (a) blocklist in spawner prompt, (b) blocklist in finalize.py diff-check (rejects + deletes branch), (c) blocklist in `bin/jarvis-automod merge` (refuses to merge artifacts touching blocked paths). Plus: test gate refuses any diff that deletes/skips tests. |
| T3 | CLI commits red code | LLM commits despite test failure | finalize.py re-runs pytest server-side; if red, marks artifact as failed + deletes branch. Belt + suspenders with the CLI-side gate. |
| T4 | Adversarial intent via voice (prompt injection causes a code-mod intent) | Speech in mic — "Jarvis, propose code mod: rm -rf src/" | `propose_code_mod()` only accepts an intent + rationale STRING; the spawner reads the string but the CLI's prompt template wraps it with explicit "edit only X, refuse outside scope" rules. Even if CLI follows the malicious intent, the blocklist + path allowlist + test gate prevent harm at the filesystem layer. |
| T5 | Spawner / CLI hangs forever | Network call, infinite loop in the LLM | 10-min timeout via `timeout 600 bin/jarvis -p ...` shell builtin. Background task in spawner.py also has its own asyncio.wait_for. |
| T6 | Merged auto-mod introduces a subtle bug only caught later | Tests don't catch every bug | Manual review is the actual gate; `merge --ff-only` keeps history clean; `revert <sha>` is a one-keystroke rollback. Audit trail in evolution_log helps trend-analyze. |
| T7 | Self-referential mod (auto-mod edits the auto-mod loop) | LLM proposes patching `pipeline/automod/spawner.py` itself | Blocklist explicitly includes `pipeline/automod/`. Test gate also requires `import jarvis_agent` clean — broken self-mod loop = import fails = gate rejects. |
| T8 | Race: two auto-mods land on the same files | Daily cap is 3 but per-topic is 1; what if two intents touch the same file under different topics? | Each spawner runs sequentially (in-flight=1 globally via a lockfile at `~/.jarvis/auto-mods/.lock`). The next spawn waits for the previous to finalize. |
| T9 | Disk space exhaustion via artifacts/logs | Many auto-mods × log files | Each artifact `.log` capped at 1MB; older-than-30-days artifacts archived to `~/.jarvis/auto-mods/archive/`. |
| T10 | Master moves under us between propose and merge | User merged other PRs in the interim | `merge --ff-only` aborts; user rebases the auto-mod branch manually or rejects + lets the pattern re-trigger. |
| T11 | Auto-mod produces a commit that breaks the live service | Even with green pytest, runtime behavior diverges | Merge does NOT auto-restart. User runs the standard restart sequence (60s-idle check). If service fails to start, `journalctl` + `jarvis-automod revert <sha>` + restart restores. |

The **load-bearing assumption** of Spec B: **the CLI is trusted to
respect the rule prompt + the user manually reviews every diff.**
Without those two, the safety story collapses.

## Privacy + data handling

- **Artifact + log files** live in `~/.jarvis/auto-mods/` and stay
  local. Never transmitted off the device.
- **Intent text** may contain user PII (e.g. "stop suggesting fish since
  I'm allergic"). It's logged in `evolution_log.jsonl`. Treat the
  evolution log the same way as `~/.jarvis/memories/`: PII present;
  inspect/delete via standard filesystem tools.
- **CLI subprocess** runs under the same user (`ulrich`); same threat
  model as the voice-agent itself. No new privilege escalation.

## Concurrency analysis

- **Global lockfile** at `~/.jarvis/auto-mods/.lock` (fcntl exclusive)
  serializes spawner runs. In-flight cap = 1.
- **Pattern detector** is read-only on telemetry except for the
  `proposed_at` column update (which uses `WHERE proposed_at IS NULL` so
  it's idempotent).
- **Review CLI** uses git locking; `merge` and `revert` are atomic git ops.
- **Voice-agent's** propose_code_mod tool writes to queue.jsonl via
  `fcntl.flock` (consistent with file_memory's pattern).

## Performance budget

| Component | Hot path? | Cost |
|---|---|---|
| Pattern detector | No (30-min background) | ~50ms per scan (SQL aggregations on ≤10k turns) |
| Throttle gate | No | <1ms per intent |
| Spawner subprocess launch | No (background asyncio task) | 200ms launch + 10min budget for the CLI work |
| Voice tool `propose_code_mod` | Hot (a tool call) | ~10ms (writes ~500B JSON to queue.jsonl) — same as memory() |
| Test gate (server-side re-run) | No | ~70s (full pytest suite) |
| Review CLI | N/A (user-driven) | manual |

**Voice latency impact: zero.** All Spec B work is off the voice loop.
The supervisor's `propose_code_mod` call is the only synchronous
component; it just queues an intent and returns.

## Observability + metrics

New telemetry tables:
- `recurring_corrections` (signal, first_seen, last_seen, count, proposed_at, resolved_at)
- `tool_gap_patterns` (intent_hash, canonical_intent, first_seen, last_seen, count, sample_tools_json, proposed_at, resolved_at)

New `turns` column:
- `correction_signal TEXT` (lowercase form of any user correction in this turn; NULL otherwise)

New audit-log `kind` values (in `~/.jarvis/evolution_log.jsonl`):
- `automod_proposed`, `automod_committed`, `automod_failed`,
  `automod_merged`, `automod_rejected`, `automod_reverted`, `automod_expired`

New log lines (`jarvis.automod` logger):
- `[automod] pattern detected: kind=correction signal='stop saying sir' count=3`
- `[automod] spawning: id=automod-2026-05-24-001 timeout=600s`
- `[automod] spawn complete: id=... status=pending|failed exit=...`
- `[automod] merge: id=... merge_sha=...`
- `[automod] revert: id=... revert_sha=...`

Dashboard hooks (deferred follow-up, not Spec B):
- Auto-mod proposal rate by kind per day
- Pending / merged / rejected / reverted ratios
- Mean time to review (proposal → merge or reject)

## Risks & mitigations

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| R1 | Reviewer LLM (llama-8b) emits noisy auto-mod intents | High (the model is weak — Spec A risk R2 already documented) | Medium (each intent is gated by daily cap + manual review) | Pattern detector ≥3 occurrences threshold; daily cap of 3; manual merge gate. User notices noise → flips `JARVIS_AUTOMOD_ENABLED=0` and pattern detector keeps recording but spawner is dark. |
| R2 | CLI subprocess uses tokens unboundedly | Medium | Medium-high (cost) | 10-min timeout + the CLI's own context limits. Track per-day cost in evolution_log; flag if >$X/day. |
| R3 | Merge introduces a behavior regression not caught by tests | Medium | High (service may degrade) | Manual diff review is the actual gate; `revert <sha>` is one keystroke; we run `tests/` (2400+ tests) — coverage is broad. |
| R4 | User forgets to restart after merge | High | Medium (code on disk diverges from running process) | `bin/jarvis-automod merge` prints the restart command + idle-check command at the end. Optional: `--restart` flag that does daemon-reload + restart with the idle check baked in. |
| R5 | Auto-mod loop locks up due to lockfile contention | Low | Medium (proposals queue indefinitely) | Lockfile has a 30-min stale-check; old locks are cleared on next pattern detector run. |
| R6 | Race: master moves between propose and merge | Medium | Low (merge --ff-only aborts) | User rebases the auto-mod branch manually OR rejects and lets the pattern re-trigger. Documented in the merge command's failure output. |
| R7 | Pattern detection false positive — proposes a fix for something that isn't actually broken | Medium | Low (merge gate filters) | Threshold (≥3 occurrences) + intent must include a `rationale` field that user can sanity-check. |
| R8 | Adversarial prompt injection causes harmful intent emission | Low (mic must be hot during malicious speech) | High (could attempt to write garbage) | Defense-in-depth: blocklist + file allowlist + test gate + manual review. Even an "exfil my keys" intent gets rejected by the blocklist before the CLI runs. |
| R9 | Disk fills with artifacts | Low | Low | 30-day archive rotation. |
| R10 | Self-mod that intends to weaken the safety story (e.g. "remove the blocklist") | Medium (LLM may not understand recursion implications) | Critical | Blocklist explicitly includes `pipeline/automod/`. Even if a proposal slips past detection, finalize.py's diff-check rejects it before committing. Belt + suspenders + locked deadbolt. |

## Testing strategy

| Component | Test file | Cases |
|---|---|---|
| Pattern detector | `tests/test_automod_patterns.py` (NEW) | 4 pattern classes, threshold edge cases, idempotency of proposed_at writes |
| Throttle + blocklist | `tests/test_automod_throttle.py` (NEW) | daily cap, in-flight, path blocklist (each entry), allowlist, edge cases (empty intent, malformed paths) |
| Spawner | `tests/test_automod_spawner.py` (NEW) | subprocess launches; env vars set correctly; timeout enforces; lockfile excludes concurrent spawns |
| Artifact + audit | `tests/test_automod_artifact.py` (NEW) | schema validation, atomic write, pytest-tmp filter on evolution log |
| Review CLI | `tests/test_automod_cli.py` (NEW) | `list`, `show`, `merge --ff-only` (success + abort cases), `reject`, `revert` |
| Voice tool | `tests/test_propose_code_mod_tool.py` (NEW) | tool registered when env set; queue.jsonl written; idempotent |
| Test gate | `tests/test_automod_test_gate.py` (NEW) | rejects blocklist-violating diff; rejects test deletion; rejects new pytest.skip; passes clean diff |
| Rollback | `tests/test_automod_revert.py` (NEW) | full propose → merge → revert cycle in a tmp git repo |

Soak: 7-day shadow run with `JARVIS_AUTOMOD_ENABLED=1` but the spawner
gated behind a `JARVIS_AUTOMOD_SPAWN_LIVE=0` flag — pattern detection
populates the queue but no subprocess fires. Manual review of the
queued intents validates the pattern detector before live spawning.

## Success criteria — measurable

**Phase 1 (days 1–7):** Deltas 1, 2, 4, 6, 7 land; spawner is built but
gated off (`JARVIS_AUTOMOD_SPAWN_LIVE=0`).

- ✓ `pytest tests/` green; no regression vs Spec A baseline.
- ✓ Pattern detector runs every 30 min; populates `recurring_corrections`
  and `tool_gap_patterns` tables.
- ✓ Manual audit of 10 queued intents: ≥80% are "actually worth a code
  change" (vs noise / wrong-target / overreach).

**Phase 2 (days 8–14):** Delta 3 + 5 land; spawner goes live; first
auto-mod cycle.

- ✓ A real auto-mod proposed, reviewed, merged, and restarted by the
  user; no service breakage.
- ✓ One auto-mod proposed, reviewed, *rejected* (intentional — test
  the reject path).
- ✓ One auto-mod merged then reverted; service unaffected.

**Phase 3 (days 15–30):** Delta 8 / soak.

- ✓ 5+ auto-mods land + are merged with zero reverts in week 3.
- ✓ Daily cap never reached (i.e. pattern detector is conservative
  enough that 3/day isn't binding).
- ✓ No safety-surface file ever appears in a diff (blocklist working).

**Anti-success (red flags — pause + tune):**
- Pattern detector emits more than 1 intent/day on average → too noisy.
- Any auto-mod merged then reverted within 24h → review gate too lax.
- CLI subprocess fails (timeout / red tests) more than 30% of the time
  → the model isn't capable enough for the assigned intent shape.
- ANY blocked-path file appears in a diff → critical bug; pause the
  entire system.

## Rollout sequencing

| Order | Delta | Goes live when |
|---|---|---|
| 1 | Schema migration (turns.correction_signal + new tables) | Commit + restart |
| 2 | Delta 1: pattern detector + queue | Commit + restart |
| 3 | Delta 2: throttle + blocklist | Commit + restart |
| 4 | Delta 4: artifact + audit (no spawn yet) | Commit + restart |
| 5 | Delta 6: `propose_code_mod` tool registered, env-gated OFF | Commit + restart |
| 6 | Delta 7: test gate | Commit |
| 7 | Delta 3: CLI spawner (gated by `JARVIS_AUTOMOD_SPAWN_LIVE=0`) | Commit |
| 8 | Delta 5: review CLI + Delta 8: revert | Commit |
| 9 | **7-day shadow soak** — queue populates, no spawn | Wait + audit |
| 10 | Set `JARVIS_AUTOMOD_ENABLED=1` | systemd env edit + restart |
| 11 | Set `JARVIS_AUTOMOD_SPAWN_LIVE=1` after manual queue review | systemd env edit + restart |

Each step is independently reversible. Kill switches at every layer.

## Kill switches

| Env var | Default | Effect |
|---|---|---|
| `JARVIS_AUTOMOD_ENABLED` | unset (OFF) | When `=1`, the `propose_code_mod` tool is registered + the pattern detector is scheduled. Master kill. |
| `JARVIS_AUTOMOD_SPAWN_LIVE` | unset (OFF) | When `=1`, the spawner actually launches CLI subprocesses. When unset, intents queue but never spawn (shadow mode). |
| `JARVIS_AUTOMOD_DAILY_CAP` | `3` | Override daily PR cap (lower is safer). |
| `JARVIS_AUTOMOD_NO_NETWORK` | passed to CLI as `1` always | Always set by the wrapper; not user-tunable. |
| `JARVIS_AUTOMOD_BLOCKLIST_EXTRA` | unset | Comma-separated extra blocklist paths (additive to hard blocklist). |
| `JARVIS_AUTOMOD_PATTERN_INTERVAL_S` | `1800` | Pattern detector scan interval (seconds). |

## Future work (out of scope here)

- **Cross-repo self-mod.** Currently scoped to `src/voice-agent/`. Could
  extend to `src/voice-agent/desktop-tauri/` or `src/cli/` later — but each tree
  would need its own test gate + blocklist.
- **GitHub PR integration.** Currently the artifact is a local branch
  + JSON; could open a real GitHub PR via `gh pr create`. Trade-off:
  exposes auto-mod history publicly.
- **Reviewer LLM upgrade.** Spec A's Spec A.1 noted llama-8b → Haiku
  4.5; for Spec B the upgrade would significantly improve pattern
  recognition (which is the noisy step).
- **Cost dashboards.** Track per-day CLI subprocess token spend.
- **Soul.md edits via auto-mod.** Persona is currently git-only. A
  future "Spec B.1" could allow soul.md edits under a stricter
  approval flow (e.g. require 2 confirmations).
- **Auto-restart after merge.** Currently manual. Could add a `--restart`
  flag with the operational-rule idle check.

## Resolved design decisions (locked 2026-05-24)

The 5 open questions are resolved by professional-engineering judgment
calls; the rationale is documented here so future work can revisit
if the empirical data disagrees.

1. **Pattern detector cadence: 30 minutes.** `JARVIS_AUTOMOD_PATTERN_INTERVAL_S=1800` default. Faster signal-to-proposal latency is the feature, not the bug — JARVIS shouldn't make you wait an hour to acknowledge a recurring correction. Scan cost is negligible (~50ms per 10k turns). User can dial to 3600 via env if 30 min proves noisy.

2. **CLI delegation (not voice-agent's own tools).** The voice-agent's `write_file`/`patch` tools fire INLINE during supervisor inference — adopting them for code-mod would freeze the voice loop while the LLM thinks. The natural alternative is a "background LLM session for code edits" — which is exactly what `bin/jarvis -p` already provides. Reuse existing infrastructure; subprocess flakiness risk is bounded by the 10-min `timeout`.

3. **Double test gate (CLI + finalize.py re-run).** The 70s server-side re-run runs against a clean working tree, catching pytest-pollution from the CLI's conversational env (residual tmp files, env-var leakage, etc.). The cost is cheap (off-latency); the safety story improves measurably. When something goes wrong, having both logs aids diffing the failure.

4. **Diff-size cap: 5 files OR 2000 lines.** File count catches scope creep better than line count alone. A legitimate "add a new tool" change is typically 2-4 files (the tool + tests + maybe a schema). A diff touching 6+ files is almost always over-reach.

5. **Autonomous reviewer does NOT know about `propose_code_mod`.** Symmetry with Spec A's posture: the reviewer LLM (llama-3.1-8b-instant) is judgment-bounded; the higher-blast-radius surfaces are gated by something other than the LLM's say-so. Code-mod intents come from exactly two paths: (a) explicit user voice request via the `propose_code_mod` tool (supervisor LLM is the gate — Sonnet/Haiku, not llama-8b), OR (b) cross-session pattern detection hitting the ≥3-occurrence threshold. Letting the reviewer emit directly would bypass the threshold and hand the weakest LLM the loaded gun.

These five decisions are baked into the architecture above; the
"Detailed design" sections already reflect them. No alternative
configurations are part of Spec B's surface; future Spec B.1 may
revisit if empirical data warrants.

## Self-review notes (completed inline)

- **Placeholders:** none remaining. Each "deferred follow-up" is
  explicit ("dashboard hooks", "auto-restart", etc.) — not TODOs.
- **Internal consistency:** Delta numbers in the table match section
  headers in Detailed Design. Blocklist defined once
  (`HARD_BLOCKLIST_PATHS`) and referenced from throttle, spawner,
  finalize, and merge — single source of truth.
- **Scope:** Plane 3 only. Plane 1+2 (Spec A) explicitly cited as
  prerequisite. Cross-repo self-mod, GitHub PR integration, reviewer
  LLM upgrade, soul.md edits, auto-restart — all deferred with
  rationale.
- **Ambiguity:** Pattern thresholds (≥3 occurrences) are explicit.
  Time windows (24h for correction, 7 days for confab, etc.) are
  explicit. Blocklist paths are enumerated. Daily cap is concrete (3).
- **Reversibility:** Every Delta has a kill switch (env var or removal
  of code). `bin/jarvis-automod revert` is documented + tested. `merge
  --ff-only` keeps history linear so reverts are clean.
- **Enterprise lens:** sourced principles (Anthropic / OpenAI / EU AI
  Act / Spec A's two-gate precedent) cited. Threat model enumerated
  (T1–T11). Privacy section explicit. Concurrency analysis covers
  lockfile + pattern detector + git ops. Performance budget shows
  zero voice-loop impact. Risk register (R1–R10) with
  likelihood/impact/mitigation. Success criteria measurable across
  3 phases with anti-success red flags.
