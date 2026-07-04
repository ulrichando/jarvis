# climb.md — JARVIS session status (2026-07-02)

Working map for the fix-everything climb. Branch: `cli-feature-unlock`.
Parallel agent session shares this tree — pathspec commits only, STAGED AUDIT before every commit
(`git reset -- plans docs/plans src/web/src/components/settings/appearance.tsx src/web/src/components/settings/keyboard.tsx`
pre-commit, verify `git diff --cached --stat` count, re-add after).

## TODO list state

| # | Task | Status |
|---|------|--------|
| 1 | Autonomous (bypass) interactive + Shift+Tab carousel | ✅ done (`7f057aec`, superseded path-wise by `2131d8a6`) |
| 2 | Blank interactive REPL — TRUE root cause | ✅ **done (`2131d8a6`)** |
| 3 | Side-by-side sweep: 448 differing files vs donor | ✅ done (3 real bug classes found+fixed; rest = rebrand/compiler artifacts/features; behavioral battery green) |
| 4 | Rebuild `dist/jarvis-linux-x64` from fixed source + verify | ✅ **done (`5a9dd902`)** — Jul-2 binary, 122M, has `keys`, paints |

## THE BIG ONE — blank interactive REPL: solved (`2131d8a6`), fact-checked twice

**Mechanism** (surfaced as a clean error once lazily loaded):
`BackgroundTasksDialog.tsx` used feature-gated **module-level `require()`s** (upstream's DCE pattern)
for `WORKFLOW_SCRIPTS` + `MONITOR_TOOL`. Jarvis ENABLES both flags (upstream doesn't → dead-code'd
there). On source runs, bun promotes the 291-module import cycle to async evaluation; a synchronous
`require()` re-entering that in-flight cycle can never be satisfied → the REPL import wave deadlocked
silently → blank terminal in EVERY permission mode. Bundled builds are immune (binary always painted;
upstream never sees it). This is the concrete mechanism behind cli.md's "enabling a flag can silently
hang the REPL boot".

**Corrections to earlier beliefs** (kept honest):
- There was never a "deadlock #2" / mode dependence — bypass "painting" was the bypass-warning dialog
  rendering BEFORE the hang; once its acceptance persisted, bypass blanked too.
- The bootstrap/state re-export cycle fix (`eb65fd29`) was correct hygiene (fork graph now matches
  donor exactly: [291,27,3,2,2,2]) but was NOT the cure.
- Two of my analysis tools had bugs found by fact-checking: SCC resolver missed `.jsx` specifiers,
  and missed double-quoted imports (React-Compiler output). Both fixed; graphs re-verified.

**Fix**: React.lazy for WorkflowDetailDialog + MonitorMcpDetailDialog (Suspense at the 2 usage sites),
on-demand `import()` wrappers for kill/skip/retryWorkflowAgent + killMonitorMcp. `feature()` gates stay
outside → bundler DCE unchanged. PromptInput static import = donor shape. Pattern copied from
`cli/handlers/util.tsx` (DoctorLazy).

**`bin/jarvis` is back to ONE path** — source `start.sh` for everything. Binary = installer artifact only.

**Verification (twice — lab worktree + main tree)**: source paints in default/acceptEdits/bypass;
tasks dialog opens and renders Shell details with a live bg task (the exact former deadlock site);
`-p "say OK"`→OK; `keys --help` intact; carousel bypass→default→accept-edits→plan→bypass via bin/jarvis.

**Residual hazard (documented, deliberately untouched)**: same require() pattern exists at
`state/AppState.tsx:16` (VOICE_MODE — enabled, currently evaluates fine by order), `commands.ts:119`
(TORCH — off), Messages.tsx / AgentTool (PROACTIVE/KAIROS — off), ExitPlanModePermissionRequest
(TRANSCRIPT_CLASSIFIER — off), SystemTextMessage (TEAMMEM — off). Rule: **before enabling any of those
flags, convert their module-level require()s to the lazy pattern first.**

## Earlier fixes this session (all committed, evidence-backed)

- `4dd0a784` voice-client SIGABRT on stop (PortAudio write/close race + alert text de-lied)
- `c58f1722` services round: log rotation ALL logs; drain_timeout 20s + TimeoutStopSec=45; OnFailure×3;
  mode-resume/bt-route rescued; kokoro→127.0.0.1; weaviate stopped; cron timer enabled
- `088ae369` `jarvis keys pull` (strict-gated route; server=source of truth) + start.sh subcommand
  skip-list (ALL commander subcommands were broken)
- `fdebe89e` keys window restyle; `480eb509` chat panels (stall watchdogs, restart/CLI buttons)
- `f79b76b0` proxy kill-race — `jarvis-proxy.service` ENABLED; never disable it
- `48277ee4`→`e842dd0a` tray CLI cwd `~/Jarvis` ($HOME trust is memory-only upstream, by design)
- `eb65fd29` bootstrap/state cycle hygiene; `7f057aec` carousel flags; `2131d8a6` THE fix

## Sweep closure (task 3) — method + evidence

- 448 diffs = 248 proven pure-rebrand (normalizer) + React-Compiler compiled-output inflation (fork
  files are compiled `_c()` output, donor is clean source — line-diffs are NOT semantic) + intentional
  fork features (main.tsx commands, voiceStreamSTT, Clawd, effort, login, workflow/snip/keys).
- 3 REAL bug classes surfaced by the sweep's graph tooling, all fixed: bootstrap/state re-export cycle
  (`eb65fd29`), gated module-level require() deadlock (`2131d8a6`), npm-ink strays (`5a9dd902`).
- Behavioral battery (source path): /permissions rules UI ✓ · `!` bash mode ✓ (`SWEEP_OK_42`) ·
  `/` palette ✓ · /model ✓ · /effort ✓ · `?` ✓ · tasks dialog + Shell details ✓ · Shift+Tab carousel ✓ ·
  e2e "say OK" ✓ · `keys --help` ✓ · `-p` ✓.
- Fork-only bare-import sweep clean (only legit SDK/bun:/node:/livekit/jarvisInChrome specifiers).

## NEW binary (`5a9dd902`)

`dist/jarvis-linux-x64` — Jul-2, 122M, compiles clean (npm-ink fix unblocked it), `keys` present,
paints with bypass footer. Old Jun-29 binary backed up at `/tmp/jarvis-binary-jun29.bak`.

## Standing rules distilled this climb

1. Never import bare `'ink'` in src/cli — always vendored `src/ink.js` (npm ink has TLA; breaks
   source-run AND `bun --compile`). Grep before releases.
2. Convert a feature-gated MODULE-LEVEL `require()` to the lazy pattern BEFORE enabling its flag
   (live list in `2131d8a6` commit + BackgroundTasksDialog comment; AppState VOICE_MODE is order-lucky).
3. Never disable `jarvis-proxy.service`. 4. Pathspec commits + STAGED AUDIT, always.

## User-action items (unchanged)

Anthropic credits LOW; Google prepaid depleted (rotation DECLINED); CF Access bypass rules for
`/api/auth/*` + `/api/bridge/*` before `jarvis auth login --url https://0wlan.com`.

## Lab / tooling notes

- Lab worktree `/tmp/lab2` (eb65fd29) — now carries the dialog fix copy; donor node_modules symlinked
  at `~/Documents/Projects/claude-code/node_modules` + a minimal tsconfig.json added there (donor source
  itself is UNRUNNABLE — public build has stripped modules, e.g. TungstenTool).
- Breadcrumbs: `require("fs").appendFileSync("/tmp/bc2.log",...)` ONLY (Ink patchStderr swallows stderr).
- tmux paint-check: `tmux capture-pane -p | grep -cE "."` — and ALWAYS `cd src/cli` in the pane command.
- SCC tool: `/tmp/scc.py` (quote-agnostic imports, strips `.jsx|.js`).

## Deep import-integrity audit (continuation, 2026-07-02) — COMPLETE

Method: 4 orthogonal scans, each fact-checked before acting.
1. SCC cycle diff fork-vs-donor (REPL root) → found+fixed bootstrap/state cycle (eb65fd29, hygiene) and
   the gated-require deadlock (2131d8a6, THE fix).
2. npm vs vendored ink → 2 strays fixed (5a9dd902); unblocked `bun --compile`; new binary shipped.
3. Unconditional static runtime imports to missing files → 1 real: claude-api skill content (ef22cf44,
   graceful WebFetch fallback). verify skill = false-positive (content present).
4. Dynamic import()/require() to missing files → 43 after multi-line-gate filtering; ALL verified guarded:
   `"external"==='ant'` (compile-false), `process.env.USER_TYPE==='ant'` (runtime-false in fork),
   `feature(X)` off, or ant-only subcommands. **Zero unguarded crashes on normal fork paths.**
5. Enabled-flag module-level require() audit → all on the static boot chain → proven safe by passing boot.

RESULT: no remaining import/module-integrity bug on any normal fork path. Behavioral battery green
(permissions UI, `!` bash, `/` palette, /model, /effort, `?`, tasks dialog + Shell details, Shift+Tab
carousel, e2e, keys, -p).

RESIDUAL (accepted, NOT fixed — deliberate): ant-only subcommands (`jarvis up|rollback|serve|daemon|
environment-runner|self-hosted-runner`) crash with an ugly ResolveMessage if EXPLICITLY invoked
(handlers are stripped ant/cloud modules). Low severity (labeled [ANT-ONLY], no fork user runs them,
error not data loss). Fix would require hand-guarding ~15 dynamic-import sites in the 6900-line COMPILED,
parallel-session-shared main.tsx → regression risk > benefit. Offered to user; do only on request.

Scanner caveats learned: (a) skip `X as typeof import()` (type-only, runtime-erased); (b) feature() gates
span multiple lines in ternaries — look back ~4 lines, not just the call line; (c) `"external"==='ant'`
is the compiled USER_TYPE macro (always false in fork) — a valid guard.

