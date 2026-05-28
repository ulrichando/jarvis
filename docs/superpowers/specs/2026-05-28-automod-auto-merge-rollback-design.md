# Auto-mod auto-merge with rollback ref — design

**Date:** 2026-05-28
**Status:** spec, pre-implementation
**Author:** Ulrich + Claude
**Scope:** `bin/jarvis-automod-impl` (extend), `src/voice-agent/pipeline/automod/finalize.py` (add helper), `src/voice-agent/pipeline/automod/cli.py` (extend `revert`), 1 new test file.

**Out of scope:** No changes to spawner / patterns / error_logger / blocklist. Auto-merge defaults OFF; existing manual-merge path is unchanged. No multi-PR coordination.

## TL;DR

When the spawned subagent generates a proposal that passes `finalize.py` checks (HARD_BLOCKLIST, diff-scope, line cap) AND `pytest` passes green AND `JARVIS_AUTOMOD_AUTO_MERGE=1` is set, the wrapper script:

1. Saves a rollback ref `refs/automod-rollback/<id>` pointing at master's pre-merge HEAD (pushed to origin so it survives local-machine death)
2. Merges the proposal branch into master with `--no-ff`
3. Pushes master
4. Restarts `jarvis-voice-agent.service` so the new code is live

Recovery: `bin/jarvis-automod revert <id>` looks up the rollback ref, hard-resets master to it, force-pushes (with `--force-with-lease`), restarts the service. One command, <30 s to a known-good state.

## Why now

Auto-mod's current flow ends at the wrapper writing a proposal artifact to `~/.jarvis/auto-mods/<id>/`. The user has to manually run `bin/jarvis-automod merge <id>` to land it. That's the v1 human gate. The user has explicitly opted for full auto-merge in 2026-05-28 — and the rollback ref is the safety net that makes auto-merge survivable when (not if) test overfitting produces a fix that passes pytest but breaks live behavior.

Research surfaced earlier in this session ([SelfHeal 2025], [test overfitting paper]) shows 22-35% of LLM-generated fixes that pass visible tests break hidden behavior. Without a rollback path, a single bad auto-merge can leave JARVIS broken with no quick recovery. The rollback ref turns "JARVIS is broken, find the bad commit, revert it manually, push, restart" into "`bin/jarvis-automod revert <id>` and wait 30s".

## Background — what's there today

- **Spawner** (`pipeline/automod/spawner.py`): forks `bin/jarvis-automod-impl` per admitted intent. Already gated by `JARVIS_AUTOMOD_SPAWN_LIVE=1`.
- **Wrapper** (`bin/jarvis-automod-impl`): branches from master to `automod/<id>`, runs `bin/jarvis -p` with the intent + HARD_BLOCKLIST in the prompt, runs `pytest`, commits if green, calls `finalize.py` to write the artifact.
- **Finalize** (`pipeline/automod/finalize.py`): validates diff against `is_blocked_path`, line cap, test-deletion regex, etc. Writes `<id>.json` artifact with status.
- **Merge CLI** (`pipeline/automod/cli.py::merge`): user runs `bin/jarvis-automod merge <id>` → fast-forward merges the `automod/<id>` branch into master.
- **Revert CLI** (`pipeline/automod/cli.py::revert`): currently takes a SHA, runs `git revert <sha>` (creates an inverse commit). NOT a hard reset.

## Part 1 — Auto-merge env gate

New env var `JARVIS_AUTOMOD_AUTO_MERGE`. Default `0` (OFF). When `1`, the wrapper executes the auto-merge tail block at the end of a successful proposal flow.

The flag is INDEPENDENT of `JARVIS_AUTOMOD_SPAWN_LIVE`:

| SPAWN_LIVE | AUTO_MERGE | Effect |
|---|---|---|
| 0 | (any) | No subagent spawns. Intents accumulate in queue.jsonl. |
| 1 | 0 | Subagent spawns + writes proposal artifact. Manual `bin/jarvis-automod merge <id>` required. |
| 1 | 1 | Subagent spawns + writes proposal + auto-merges if all checks pass. |

The `JARVIS_AUTOMOD_AUTO_MERGE` env must reach the wrapper subprocess. `bin/jarvis-automod-impl` is invoked by the spawner via `asyncio.create_subprocess_exec(str(WRAPPER_SCRIPT), str(intent_file), ...)` — env is inherited from the parent (the voice-agent worker). The voice-agent's systemd unit file is where the env gets set (alongside `JARVIS_AUTOMOD_SPAWN_LIVE=1`).

## Part 2 — Auto-merge sequence (wrapper)

`bin/jarvis-automod-impl` already has these stages (current):

1. Branch from master to `automod/<id>`
2. Run `bin/jarvis -p` with the prompt
3. Stage changes
4. Run `pytest`
5. Commit if green
6. Call `finalize.py` to write artifact

Add a new STAGE 7 at the end:

```bash
# --- Stage 7: Auto-merge (gated by JARVIS_AUTOMOD_AUTO_MERGE=1) ---
if [ "${JARVIS_AUTOMOD_AUTO_MERGE:-0}" = "1" ] && [ "$FINALIZE_PASSED" = "1" ] && [ "$PYTEST_PASSED" = "1" ]; then
    echo "[$(date -u +%FT%TZ)] [automod-impl] AUTO_MERGE=1 — proceeding"

    # 7a. Save rollback ref BEFORE touching master.
    git fetch origin master --quiet
    PRE_MERGE_SHA=$(git rev-parse origin/master)
    ROLLBACK_REF="refs/automod-rollback/$ID"
    git update-ref "$ROLLBACK_REF" "$PRE_MERGE_SHA"
    if ! git push origin "$ROLLBACK_REF:$ROLLBACK_REF" --quiet; then
        echo "[$(date -u +%FT%TZ)] [automod-impl] rollback ref push FAILED — aborting auto-merge"
        # Don't continue — leave the proposal in the queue for manual merge.
        # The local ref still exists; the user can recover via the artifact.
        exit 0
    fi

    # 7b. Merge proposal branch into master.
    git checkout master
    git pull origin master --ff-only --quiet
    if ! git merge --no-ff "$BRANCH" -m "automod: auto-merge $ID"; then
        echo "[$(date -u +%FT%TZ)] [automod-impl] merge FAILED — aborting"
        git merge --abort 2>/dev/null || true
        exit 0
    fi

    # 7c. Push master.
    if ! git push origin master --quiet; then
        echo "[$(date -u +%FT%TZ)] [automod-impl] master push FAILED — reverting local merge"
        git reset --hard "$PRE_MERGE_SHA"
        exit 0
    fi

    MERGE_SHA=$(git rev-parse master)
    echo "[$(date -u +%FT%TZ)] [automod-impl] auto-merged: id=$ID merge_sha=$MERGE_SHA"

    # 7d. Stamp the artifact JSON with auto-merge metadata.
    "$REPO_ROOT/src/voice-agent/.venv/bin/python" -m pipeline.automod.finalize mark-auto-merged \
        "$ID" \
        --rollback-ref "$ROLLBACK_REF" \
        --rollback-sha "$PRE_MERGE_SHA" \
        --merge-sha "$MERGE_SHA"

    # 7e. Restart the voice-agent so the new code is live.
    # Respect the existing "active session" guard — if the last turn
    # was <60s ago, defer the restart (artifact still records auto-merge).
    LAST_TURN_TS=$(sqlite3 ~/.local/share/jarvis/turn_telemetry.db \
        "SELECT strftime('%s', ts_utc) FROM turns ORDER BY id DESC LIMIT 1" 2>/dev/null || echo "0")
    NOW=$(date +%s)
    AGE=$((NOW - LAST_TURN_TS))
    if [ "$AGE" -gt 60 ]; then
        systemctl --user restart jarvis-voice-agent.service
        echo "[$(date -u +%FT%TZ)] [automod-impl] voice-agent restarted"
    else
        echo "[$(date -u +%FT%TZ)] [automod-impl] restart deferred (session active ${AGE}s ago); new code loads on next natural restart"
    fi
fi
```

Idempotent on partial failure: if step 7b fails after step 7a saves the rollback ref, the local ref + remote ref are still in place — user can run `bin/jarvis-automod revert <id>` to clean up (it'd be a no-op since master wasn't moved).

## Part 3 — Finalize helper: `mark_auto_merged`

Add to `pipeline/automod/finalize.py`:

```python
def mark_auto_merged(
    automod_id: str,
    rollback_ref: str,
    rollback_sha: str,
    merge_sha: str,
) -> None:
    """Stamp an automod artifact JSON with auto-merge metadata.
    Idempotent: re-stamps if called twice (the wrapper isn't supposed to
    fire twice for the same id, but a retry-after-crash is recoverable)."""
    artifact_file = artifact_path(automod_id)
    if not artifact_file.exists():
        # No artifact yet — the wrapper hasn't completed its normal finalize.
        # Create a minimal record so revert can find it.
        artifact_file.parent.mkdir(parents=True, exist_ok=True)
        record = {"id": automod_id}
    else:
        record = json.loads(artifact_file.read_text(encoding="utf-8"))

    record["auto_merged_at"] = _now_iso()
    record["rollback_ref"] = rollback_ref
    record["rollback_sha"] = rollback_sha
    record["merge_sha"] = merge_sha
    artifact_file.write_text(json.dumps(record, indent=2), encoding="utf-8")
    audit("automod_auto_merged",
          id=automod_id,
          rollback_ref=rollback_ref,
          rollback_sha=rollback_sha,
          merge_sha=merge_sha)
```

Wire into the existing CLI in `finalize.py` (it already has argparse for other subcommands):

```python
mp = sub.add_parser("mark-auto-merged",
                    help="Stamp artifact with auto-merge metadata")
mp.add_argument("id")
mp.add_argument("--rollback-ref", required=True)
mp.add_argument("--rollback-sha", required=True)
mp.add_argument("--merge-sha", required=True)
mp.set_defaults(handler=lambda args:
                mark_auto_merged(args.id, args.rollback_ref,
                                 args.rollback_sha, args.merge_sha))
```

## Part 4 — Extend `revert` CLI

Current `cli.py::revert` takes a SHA. Extend to ALSO accept an automod ID (e.g. `automod-2026-05-28-abc123`). On ID input, look up the rollback ref from the artifact JSON and hard-reset master.

```python
def revert(args) -> int:
    target = args.target
    # Case 1: looks like an automod ID → use rollback ref
    if target.startswith("automod-") and "/" not in target:
        artifact_file = artifact_path(target)
        if not artifact_file.exists():
            print(f"error: artifact not found: {artifact_file}", file=sys.stderr)
            return 2
        rec = json.loads(artifact_file.read_text(encoding="utf-8"))
        rollback_ref = rec.get("rollback_ref")
        rollback_sha = rec.get("rollback_sha")
        if not rollback_ref or not rollback_sha:
            print(f"error: artifact {target} has no rollback metadata "
                  "(was it manually merged?)", file=sys.stderr)
            return 2
        # Fetch the rollback ref + hard-reset master to it.
        subprocess.check_call(
            ["git", "fetch", "origin",
             f"{rollback_ref}:{rollback_ref}"], cwd=REPO_ROOT,
        )
        subprocess.check_call(
            ["git", "checkout", "master"], cwd=REPO_ROOT,
        )
        subprocess.check_call(
            ["git", "reset", "--hard", rollback_sha], cwd=REPO_ROOT,
        )
        subprocess.check_call(
            ["git", "push", "--force-with-lease",
             "origin", "master:master"], cwd=REPO_ROOT,
        )
        # Restart voice-agent so the reverted code is live.
        subprocess.run(
            ["systemctl", "--user", "restart",
             "jarvis-voice-agent.service"], check=False,
        )
        audit("automod_reverted",
              id=target, rollback_ref=rollback_ref,
              rollback_sha=rollback_sha)
        print(f"reverted: master reset to {rollback_sha[:8]} "
              f"(rollback ref {rollback_ref})")
        return 0

    # Case 2: existing SHA-based revert (git revert <sha>)
    return _legacy_revert_by_sha(target)
```

Migrate the existing revert body into `_legacy_revert_by_sha` to keep it working.

## Part 5 — Risk + safety

### What can go wrong

- **Rollback ref push fails.** Then master wasn't moved (step 7b never ran). User can manually clean up the local ref.
- **Merge conflict on master.** Shouldn't happen if the proposal branched cleanly from master, but possible if the user committed to master between branch+merge. Mitigation: `git pull origin master --ff-only` before merge; if conflict, abort.
- **Push to master rejected** (e.g. branch protection). Mitigation: detected, local merge reset, exit. Manual recovery.
- **Restart kills in-flight session.** Mitigation: existing 60s check from CLAUDE.md ops rule — skip the restart if `last turn < 60s ago`.
- **Test overfitting** (the load-bearing concern). The rollback ref is the answer. Worst case: bad merge lands, user notices JARVIS is acting wrong, runs `bin/jarvis-automod revert <id>`, <30s back.
- **Bad fix that breaks the rollback path itself.** HARD_BLOCKLIST already protects `pipeline/automod/` — auto-mod cannot edit its own modules. Bash wrapper (`bin/jarvis-automod-impl`) is NOT in the blocklist; consider adding (yes — add to blocklist as part of this PR).

### Safety addition: extend HARD_BLOCKLIST

Add `bin/jarvis-automod-impl` and `bin/jarvis-automod` to `HARD_BLOCKLIST_PATHS` in `_state.py`. The auto-mod can't modify the rollback machinery itself.

```python
HARD_BLOCKLIST_PATHS = (
    "src/voice-agent/sanitizers/",
    "src/voice-agent/confab_detector.py",
    "src/voice-agent/pipeline/automod/",
    "src/voice-agent/pipeline/skill_review.py",
    "src/voice-agent/prompts/soul.md",
    "CLAUDE.md",
    ".claude/rules/regression-prevention.md",
    "MEMORY.md",
    "USER.md",
    # 2026-05-28: protect the auto-merge wrapper + CLI
    # from being modified by auto-mod itself.
    "bin/jarvis-automod-impl",
    "bin/jarvis-automod",
)
```

This is a config addition; it'll be tested by adding 1 unit test to `test_automod_pattern_*` (existing tests cover blocklist enforcement; just need to verify the new entries are present).

## Part 6 — Testing

New test file `tests/test_automod_auto_merge.py` (~6 tests):

1. `mark_auto_merged` creates artifact metadata correctly (rollback_ref, rollback_sha, merge_sha, auto_merged_at present)
2. `mark_auto_merged` is idempotent (second call updates timestamps without crashing)
3. `revert` CLI with automod ID looks up the rollback ref from artifact and prints rollback SHA
4. `revert` CLI with malformed automod ID returns exit 2 with error message
5. `revert` CLI with automod ID lacking rollback metadata returns exit 2
6. HARD_BLOCKLIST_PATHS includes `bin/jarvis-automod-impl` and `bin/jarvis-automod`

Tests for `revert` use `subprocess.check_call` — these need mocking. Use `unittest.mock.patch("subprocess.check_call")` to avoid actually running `git fetch` / `git reset` etc. during tests.

The wrapper script (`bin/jarvis-automod-impl`) is bash; not unit-tested directly. Manually smoke-tested in Part 7.

## Part 7 — Verification path

1. Unit tests pass: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_automod_auto_merge.py -v`
2. Lint the bash wrapper: `shellcheck bin/jarvis-automod-impl` (existing CI does this if configured; otherwise run locally)
3. Set both flags: `JARVIS_AUTOMOD_SPAWN_LIVE=1` (already set) + `JARVIS_AUTOMOD_AUTO_MERGE=1` (new). Restart voice-agent.
4. Force an intent into the queue (e.g. via the existing `propose_code_mod` voice tool with a tiny, safe request).
5. Wait for the spawner to fork the wrapper. Watch logs: `tail -f /home/ulrich/.jarvis/auto-mods/<id>.log`.
6. Verify the rollback ref was created: `git ls-remote origin "refs/automod-rollback/*"`.
7. Verify master moved: `git fetch origin master && git log master --oneline -3`.
8. Verify the artifact has auto-merge metadata: `cat ~/.jarvis/auto-mods/<id>.json | jq .auto_merged_at`.
9. Voice-agent should be running the new code (check log for "registered worker" after the auto-merge timestamp).
10. Smoke the revert: `bin/jarvis-automod revert <id>` should reset master and restart the agent.

## Part 8 — What this spec deliberately does NOT do

- **No CI integration.** Auto-merge fires locally, on the user's machine. The pushed master may have failing CI; user's responsibility to monitor.
- **No PR creation.** The wrapper merges directly to master — no PR opens. The user opted into auto-merge with this caveat.
- **No multi-machine coordination.** This is single-machine. If the user pulls master onto another box, they get the auto-merged code with no special handling.
- **No "trial period" for the merged fix.** Once merged + restarted, the code is live. The rollback ref is the only safety net.
- **No automatic detection of post-merge breakage.** If a turn fails 10 minutes after auto-merge, that triggers a NEW intent through the error-driven scanner — eventually. There's no immediate "was this fix bad?" check.

## References

- Auto-mod parent spec: `docs/superpowers/specs/2026-05-24-jarvis-source-code-self-mod-design.md`
- Error-driven scanner spec: `docs/superpowers/specs/2026-05-27-automod-error-driven-branch-design.md`
- Research synthesis (in-session): test overfitting, SelfHeal fix patterns, Sentry Seer architecture
