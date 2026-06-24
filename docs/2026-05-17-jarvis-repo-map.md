# JARVIS repo map — 2026-05-17

Completeness sweep of `/home/ulrich/Documents/Projects/jarvis`. Companion to [`2026-05-16-jarvis-global-review.md`](2026-05-16-jarvis-global-review.md) (10-domain review, `[2026-05-16 §X]`) and [`2026-05-17-jarvis-enterprise-grade-plan.md`](2026-05-17-jarvis-enterprise-grade-plan.md) (`[2026-05-17 §Y]`). This is a map, not a code review — purpose: ensure no directory or load-bearing file is unaccounted for.

---

## 1. TL;DR

8 subtrees under `src/`, ~95k Python+TS+TSX+JSX LoC. Tracked source roughly: voice-agent ~50k Py, cli ~524k TS (huge), web ~41k TSX, desktop-tauri ~3k JSX + 2,090 Rust, hub ~1.2k. Build artifacts on disk total ~3.8 GB (Rust `target` 1.2 GB, `.venv` 1.2 GB, web `node_modules` 1.1 GB, vendored `llama.cpp` 150 MB *in git*, `livekit-server.bin` 49 MB *in git*). `.worktrees/` absent; `git worktree list` shows only master. Two stale remote branches: `feat/ext-browser-control-v3` (already merged) and `chore/regression-prevention` (not merged, but equivalent work shipped via separate commits).

**Findings prior reviews missed:**
1. **`src/voice-agent/livekit-server.bin` is a 49 MB stripped ELF tracked in git** (last touched 2026-04-24, commit `48eeebe8 "update"`). [2026-05-17 §P1-SEC-4] addresses checksum verification but not the clone-time tax of keeping it in-tree.
2. **`src/cli/src/` (39 subdirs, 524k LoC) is entirely unreviewed beyond the bridge security pass.** The "off-limits" rule in CLAUDE.md shields it from review but doesn't shield production: `main.tsx` is 6,755 LoC, the voice subsystem lives in `src/cli/src/voice/`, and the bridge desktop+voice+ext rely on lives at `src/cli/src/bridge/server.ts`. This is the largest blind spot in the repo.
3. **CLAUDE.md / docs discrepancies confirmed by walk:**
   - `~/.local/share/jarvis/logs/` directory exists but is **empty** despite the doc claiming `voice-agent.log` rotates here.
   - `~/.jarvis/local-api-token.env` is **missing** despite CLAUDE.md saying it was added 2026-05-16 (install hook never ran on this machine, or the hook is broken).
   - `setup/systemd/livekit-server.service` ships in the repo but **no matching unit is installed** in `~/.config/systemd/user/`.
   - `state.db.backup-2026-05-16-prepurge` exists undocumented next to the live hub DB.

---

## 2. Top-level inventory

| Name | Purpose | Review / Health |
|---|---|---|
| `CLAUDE.md` (16 KB) | Project context | covered §all / active |
| `README.md` (5 KB) | Install + run guide | covered §all / active |
| `install.sh` (534 LoC) | One-shot installer | [2026-05-16 §P0-4], [2026-05-17 §P0-SEC-6] / drifting |
| `.env` (2.8 KB) | Live API keys, mode 600 | [2026-05-17 §P0-SEC-6] / active |
| `.gitignore`, `package.json`+`bun.lock` | Top-level meta; ~6-line `package.json` is `@ai-sdk/*`-only | [2026-05-17 §P0-DEP] / drifting (unclear purpose) |
| `bin/` (14 scripts) | Operator binaries (`jarvis`, `jarvis-rules`, `jarvis-soak-rescore.sh`, …) | partial / active |
| `scripts/` (8 scripts) | Maintenance (`jarvis-backup-local.sh`, `rotate-jarvis-logs.sh`, …) | partial / active |
| `setup/audio/` (1) + `setup/systemd/` (4 units) | PipeWire conf + `jarvis-{hub,voice-agent,voice-client}`+`livekit-server` units | covered audio + ops / active |
| `docs/` | All written docs (see §4) | self-referential / active |
| `src/` (8 subtrees) | All source (see §3) | mixed / mixed |
| `.claude/` | Claude Code agents/commands/hooks/rules | [2026-05-16 §tests-P0] / active |
| `.jarvis/agents/voice-log-analyzer.md` | Project-scoped agent def | **NOT COVERED** / active |
| `.github/workflows/` (3) | `desktop-tauri-smoke`, `security-audit`, `voice-agent-tests` | covered tests / active |
| `node_modules/` (9.3 MB) | From minimal top-level `package.json` | runtime |

---

## 3. `src/` deep tree

### `src/voice-agent/` — 1.2 GB on disk (mostly `.venv`)

- **Source:** ~270 Python files (excl. `.venv`+`__pycache__`), ~50k LoC.
- **Entry:** `jarvis_agent.py` (5,519 LoC; target <3,500 [2026-05-16 §Q2]).
- **Top-level Py:** `confab_detector.py` (292), `jarvis_voice_client.py` (843), `voice_client_{auth,http_api,screen_share,tray_config,watchdog}.py` (138/494/214/240/353).

| Subdir | Files | LoC | Purpose |
|---|---:|---:|---|
| `pipeline/` | 47 | 8,769 | Turn router, dispatcher, telemetry, memory extractor/consolidator, LangGraph slow-path, `evolution/` subpkg (16 files, 4k LoC) |
| `subagents/` | 15 | 3,163 | Registry + 9 specs + `HOW_TO_ADD_A_SUBAGENT.md` |
| `tools/` | 30 | 7,239 | bash, file_*, browser_ext_*, computer_use, memory, plan_mode, skill_runner, etc. |
| `sanitizers/` | 12 | 2,550 | Four load-bearing monkey-patches + 8 supporting |
| `resilience/` | 6 | 534 | Breaker, idle timeout, reconnect ladder, track guard, watchdog |
| `providers/` | 5 | 1,519 | `llm.py` (1k+), `tts.py`, `stt.py`, `edge_tts.py` |
| `tests/` | 115 | 19,886 | `pytest -q` full suite ~20s |
| `prompts/` | 3 md | — | `supervisor.md` (134 KB — [2026-05-16 §P1 supervisor compression]), `anchor_rules.md`, untracked `supervisor.md.backup-2026-05-16` |
| `skills/` | 2 dirs | — | `git-status/SKILL.md`, `system-stats/SKILL.md` |
| `.venv/` | n/a | n/a | 1.2 GB Python venv |

- **Stranded:** `livekit-server.bin` (49 MB tracked), `livekit.yaml` (38 LoC), `cli_voice_prompt.md` (135), `pyrightconfig.json`, `pytest.ini`, `requirements.txt` (81), `requirements-test.txt` (17), `LICENSE`.
- **Health:** active. Covered exhaustively by [`docs/reviews/2026-05-16/`](reviews/2026-05-16/).

### `src/voice-agent/desktop-tauri/` — 1.3 GB on disk

- Frontend: `src/{App,main,KeysSettings}.jsx + components/{ChatPanel,ContextBar,TodoBlock,ToolProgress}.jsx + hooks/useVoiceClient.js`. ~600 JSX LoC.
- Backend: `src-tauri/src/main.rs` (2,090 LoC, one Rust file — split planned [2026-05-16 §Q2]).
- Config: `package.json` (vite + Tauri 2.x + React 19), `Cargo.toml`, `capabilities/default.json`, `icons/`.
- **Health:** active; covered by [`reviews/2026-05-16/jarvis-review-desktop.md`](reviews/2026-05-16/jarvis-review-desktop.md).

### `src/cli/` — 257 MB on disk; **524,014 TS+TSX LoC across 1,934 source files**

- **Off-limits per CLAUDE.md** when working other subtrees. Largest tree in repo.
- **39 subdirs under `src/cli/src/`:** `assistant, bridge, buddy, cli, commands, components, constants, context, coordinator, entrypoints, hooks, ink, keybindings, memdir, migrations, moreright, native-ts, outputStyles, plugins, proxy, query, remote, schemas, screens, server, services, skills, state, tasks, tools, types, upstreamproxy, utils, vim, voice`.
- **Largest files:** `main.tsx` (6,755), `cli/print.ts` (5,594), `utils/messages.ts` (5,512), `utils/sessionStorage.ts` (5,105), `utils/hooks.ts` (5,022), `screens/REPL.tsx` (5,005).
- **Production-critical exports:** `src/bridge/server.ts` (the bridge desktop/voice/extension call), `scripts/start-desktop.sh` (desktop launcher). Both in [2026-05-16 §P0-1 to §P0-3].
- 34 bundled "skills" under `src/skills/bundled/` (`stuck, loop, simplify, verify, keybindings, claudeApi, skillify, ...`).
- Reserved: `src/utils/claudeInChrome/` per CLAUDE.md (future Firefox/Chrome ext).
- `vendor/bun/` (16 KB — purpose unclear, likely Bun runtime placeholder). `.env.local` present.
- **Health:** active, off-limits-by-policy; effectively a black box to both reviews.

### `src/web/` — 1.1 GB on disk; 41k TSX+TS LoC

- **Stack:** Next.js (custom-patched per `AGENTS.md`), Drizzle ORM, Tailwind 4, vitest.
- **`src/`:** `app/{api,(app)}`, `components/{chat,code,design,layout,markdown,projects,settings,ui,workbench}`, `hooks`, `lib/{ai,actions,bridge,chat,db,deploy,design,fixers,hub,settings,tools,verify,workspace}`, `stores`.
- **API routes:** `bridge, chat, conversations, dbg, hub-settings, logs, memories, sessions, settings, workspace`.
- **Largest:** `workbench/tabs/settings-tab.tsx` (3,024), `chat/chat.tsx` (1,808), `design/design-preview.tsx` (1,406), `app/api/chat/route.ts` (740).
- **Auxiliary:** `drizzle/` (2 migrations), `public/` (logos), `scaffolds/` (3 starter templates), `scripts/{pty-server.mjs, workbench-image/Dockerfile}`, own `docs/superpowers/`, own `CLAUDE.md` + `AGENTS.md`.
- **Health:** drifting per [2026-05-17 §3.2] ("looks enterprise, actually wide open" — 60+ routes, no auth, hardcoded `LOCAL_USER_ID`).

### `src/hub/` — 1.5 MB on disk

- 9 source files: `client.py` (245), `server.py` (336), `settings_watcher.py` (109), `migrate_conversations.py` (107), `migrate_settings.py` (45), `client-core.ts` (130), `client.ts` (173), `schema.sql` (59), empty `__init__.py`.
- Materializes `~/.jarvis/hub/state.db` from Redis Streams (`events:{conversation,settings,memory}`). NOT the bridge [2026-05-16 §P0-5]. Covered by memory + ops reviews.

### `src/extensions/jarvis-screen/` — 332 KB on disk

- Chrome MV3 ext: `manifest.json, background.js, content.js, actions.js, safety.js, side_panel.{html,js}` (591 LoC main UI), `popup.{html,js}, options.{html,js}, tests/{actions,safety}.test.js`. ~1.7k LoC.
- `safety.js` flagged kill/rewrite [2026-05-17 §3.1 — doesn't actually load: CommonJS in MV3 SW]. Covered by bridge + extension specialist.

### `src/android/` — 505 MB on disk; 28,851 Kotlin LoC excl. vendored llama.cpp

- Vendored `app/src/main/cpp/llama.cpp/` = 150 MB (~30k Kotlin/Java/CPP LoC, in-tree not submodule).
- Subdirs under `app/src/main/java/com/jarvis/android/`: `core/{designsystem,network}, di, domain/{model,repository,usecase}, navigation, presentation/localai/{benchmark,settings}, service, startup, system/{adb,permissions}, util`. 128 Kotlin files outside llama.cpp.
- Last touched 2026-04-26. Talks homelab `10.10.0.50:8765`, zero integration with JARVIS core. `.gradle/` 56 KB tracked, `app/.cxx/` 354 MB untracked. [2026-05-17 §3.1] recommends standalone-repo split.

### `src/convex/` — 8 KB on disk

- Single `.env.local` (197 B). Empty otherwise. Convex retired Phase 7 [2026-05-17 §3.1]. `convex~=0.7` still pinned in `requirements.txt:42`. Dead — flagged kill.

---

## 4. `docs/` inventory

| Doc | Last commit | Authoritative? |
|---|---|---|
| `2026-05-15-pre-realtime-snapshot.md` | 2026-05-15 | Active revert checkpoint |
| `2026-05-16-jarvis-global-review.md` (191 LoC) | 2026-05-17 | **Canonical baseline**, except where 2026-05-17 supersedes |
| `2026-05-17-jarvis-enterprise-grade-plan.md` (581 LoC) | 2026-05-17 | **Active plan** |
| `2026-05-17-jarvis-repo-map.md` | new | This doc |
| `runbook/{credential-rotation,encryption-at-rest,git-history-scrub,jarvis-voice}.md` | all 2026-05-04 | All appear authoritative; `jarvis-voice.md` refs non-existent `jarvis-bridge.service` [2026-05-16 §P0-5] |
| `superpowers/vm-validation-2026-04-19.md` | 2026-04-19 | Old VM baseline; superseded |
| `superpowers/plans/` (32) | 2026-04-18 → 2026-05-12 | Per-feature plans; most shipped; archival |
| `superpowers/specs/` (31) | 2026-04-23 → 2026-05-12 | Per-feature specs; archival |
| `reviews/2026-05-16/` (10) | 2026-05-17 | Source-of-truth for 10-domain sweep |

---

## 5. Operational state files (`~/.jarvis/`, `~/.local/share/jarvis/`, systemd units)

| Path | Status | Notes |
|---|---|---|
| `~/.jarvis/hub/state.db` + `state.db.backup-2026-05-16-prepurge` | exists | Backup undocumented |
| `~/.jarvis/cli/sessions.db`, `cli-model`, `voice-model`, `tts-provider` | exists | Active |
| `~/.jarvis/{cache,plans,projects,sessions,session-env,shell-snapshots}/` | exists | CLI working dirs |
| `~/.jarvis/backups/` | empty | `bin/jarvis-canary` work not started [2026-05-17 §P0-DATA] |
| `~/.jarvis/conversations.db` | **0 bytes** | **Zombie [2026-05-17 §3.1]** |
| `~/.jarvis/evolution_log.jsonl` | exists (5.7 KB) | Active |
| `~/.jarvis/livekit-keys.yaml` + `.bogus-format` sibling | exists | `.bogus-format` is kill-flagged [2026-05-17 §3.1] |
| `~/.jarvis/local-api-token.env` | **missing** | Required per CLAUDE.md (added 2026-05-16) — **install hook didn't run** |
| `~/.local/share/jarvis/turn_telemetry.db` (80 KB) | exists | Active |
| `~/.local/share/jarvis/logs/` | **empty** | **Discrepancy with CLAUDE.md "rotated daily"** — logs aren't landing here |
| `~/.config/systemd/user/jarvis-{hub,voice-agent,voice-client}.service` | 3 active | **No `jarvis-bridge.service` [2026-05-16 §P0-5]; no `livekit-server.service`** despite `setup/systemd/livekit-server.service` shipping |

---

## 6. Worktrees + branches

- **`.worktrees/` dir:** absent.
- **`git worktree list`:** single entry — `master`.
- **Local branches:** `master` only.
- **Remote branches:**
  - `origin/master` (2026-05-15)
  - `origin/feat/ext-browser-control-v3` (2026-05-12) — **already merged into master** per `git merge-base --is-ancestor`. Safe to delete remote.
  - `origin/chore/regression-prevention` (2026-05-07) — **NOT merged**, 44-line diff. The regression-prevention rule landed on master via separate commits (`a11ccf8e` ... `f7dffa7f`), so this branch is stale WIP. Recommend delete after confirming nothing unique remains.
- **No forgotten WIP elsewhere.**

---

## 7. Build artifacts + caches

| Path | Size | Tracked? | Notes |
|---|---:|---|---|
| `src/voice-agent/.venv/` | 1.2 GB | gitignored | Python venv |
| `src/voice-agent/desktop-tauri/src-tauri/target/` | 1.2 GB | gitignored | Rust release+debug |
| `src/web/node_modules/` | 1.1 GB | gitignored | |
| `src/web/.next/` | 15 MB | gitignored | Next build cache |
| `src/cli/node_modules/` | 222 MB | gitignored | |
| `src/voice-agent/desktop-tauri/node_modules/` | 144 MB | gitignored | |
| `node_modules/` (top-level) | 9.3 MB | gitignored | From the 6-line `package.json` |
| `src/android/app/.cxx/` | 354 MB | gitignored | NDK build cache |
| `src/android/.gradle/` | 56 KB | gitignored | |
| `src/voice-agent/desktop-tauri/dist/` | 256 KB | gitignored | Vite output (re-embedded into Tauri binary per CLAUDE.md two-step) |
| `src/voice-agent/desktop-tauri/src-tauri/gen/` | 340 KB | tracked | Tauri capability schemas — fine |
| `src/voice-agent/.pytest_cache/` | 160 KB | tracked (oops — `.gitignore` should exclude) | Inspect; minor bloat |
| `src/voice-agent/__pycache__/`, `src/hub/__pycache__/` | small | untracked, gitignored | OK |
| `src/voice-agent/livekit-server.bin` | **49 MB** | **TRACKED** | **Should be moved out of git; verify-via-checksum from release URL** [2026-05-17 §P1-SEC-4 partial] |
| `src/android/app/src/main/cpp/llama.cpp/` | **150 MB** | **TRACKED** | **Should be a git submodule** [2026-05-17 §3.1 partial] |

**Total on-disk:** ~3.8 GB. Total tracked-in-git bloat: ~200 MB attributable to `livekit-server.bin` + vendored llama.cpp.

---

## 8. The "uncovered" section — flagged `[UNCOVERED]`

| Path | Notes |
|---|---|
| `src/cli/src/` (39 subdirs, 524k LoC) | High-leverage blind spot. Off-limits rule justified-by-policy but [2026-05-16 §P0] necessarily reaches `bridge/`. |
| `src/cli/scripts/{bunw.{sh,mjs,cmd,ps1}, run-{cli,proxy}.mjs, start.{mjs,sh,cmd,ps1}}` | Cross-platform launcher boilerplate; bypasses systemd visibility. |
| `src/web/scaffolds/{express-sqlite-api, next-14-tailwind, vite-react-tailwind}` | Confirm intentional, not stale starter templates. |
| `src/web/scripts/workbench-image/Dockerfile` | Builds `jarvis-workbench:latest`; not in CI smoke. |
| `src/web/public/jarvis-shadcn.mjs` | Standalone shadcn helper served from `/public` — unusual location. |
| `src/web/docs/superpowers/` | Own copy of plans+specs (1+1 from 2026-04-27); confirm not stale. |
| `src/voice-agent/cli_voice_prompt.md`, `livekit.yaml` | Ownership + fingerprint; not in CLAUDE.md. |
| `src/voice-agent/desktop-tauri/src-tauri/{capabilities/default.json, gen/schemas/}` | Read once for permission surface. |
| `scripts/{audit-hallucinations.py, render-canned-phrases.py}` | Verify cron-status. |
| `bin/{jarvis-rules-migrate-v2.py, jarvis-backfill-null-routes.py}` | One-shot migrations; archive once run. |
| `.jarvis/agents/voice-log-analyzer.md` | Project-scoped Claude subagent definition. |
| `src/voice-agent/skills/{git-status, system-stats}/SKILL.md` | Verify wired via `skills_loader.py`. |

---

## 9. Dead-file / deletion candidates (beyond [2026-05-17 §3.1])

| File / dir | Why dead | Confidence |
|---|---|---|
| `src/voice-agent/prompts/supervisor.md.backup-2026-05-16` | 134 KB working-tree backup; not tracked; supervisor edits already on master | High |
| `~/.jarvis/conversations.db` (0 bytes) | Confirmed zombie [2026-05-17 §3.1] | High |
| `~/.jarvis/livekit-keys.yaml.bogus-format` | Confirmed [2026-05-17 §3.1] | High |
| `src/hub/__init__.py` (0 bytes) | Convention; keep — Python package marker | Low (do not delete) |
| `src/cli/vendor/bun/` (16 KB) | Unclear purpose; likely placeholder for vendored Bun runtime never populated | Medium — verify before delete |
| `src/web/tests/{kimi/*, _msw/, bridge/, sanity.test.ts}` | Per `git diff origin/feat/ext-browser-control-v3 master` these were *deleted* on master (148998 line removal in that diff) — clean | Verified clean |
| `origin/chore/regression-prevention` remote branch | Not merged; equivalents shipped via separate commits | High |
| `origin/feat/ext-browser-control-v3` remote branch | Already merged into master | High |
| `src/voice-agent/.pytest_cache/` (tracked) | Should be gitignored | High — should `git rm -rf --cached` |
| `src/voice-agent/livekit-server.bin` (49 MB) | Should be fetched-at-install, not committed | High |
| `src/android/app/src/main/cpp/llama.cpp/` (150 MB) | Should be git submodule | High |

---

## 10. Cross-tree dependencies

Off-limits-but-required tensions:

```
desktop-tauri (Tauri tray)
   │
   ├─► src/cli/scripts/start-desktop.sh        (the launcher)
   │      │
   │      ├─► spawns src/cli/src/bridge/server.ts  (HTTP+WS on 127.0.0.1:8765)
   │      │      ├─► /api/livekit/token  ← Tauri React, voice-agent, ext call
   │      │      ├─► /ws (extension_hello) ← Chrome ext jarvis-screen connects
   │      │      └─► proxy → src/voice-agent/* over HTTP
   │      │
   │      ├─► spawns proxy on :4000 (litellm_config.yaml route map)
   │      └─► spawns Tauri desktop binary
   │
   └─► talks LiveKit room ← voice-agent joins same room
                              │
voice-agent (jarvis-voice-agent.service)
   │
   ├─► subagent registry → tools/ → bash, browser_ext, computer_use, file_*
   │     └─► browser_ext/ → HTTP → bridge:8765 → WS → jarvis-screen extension
   │
   ├─► pipeline/turn_router → providers/llm.py → Groq/DeepSeek/OpenAI/etc.
   ├─► pipeline/memory_extractor → hub.client.publish → Redis Streams
   │                                                    │
hub (jarvis-hub.service)                                ▼
   ├─► server.py consumes events:* → materializes ~/.jarvis/hub/state.db
   └─► tools/memory.py reads back via hub.client.read

web (Next.js, dev only)
   ├─► /api/* → ~/.jarvis/hub/state.db (read)
   └─► /api/bridge/* → bridge:8765 (proxied)

android (standalone)
   └─► talks 10.10.0.50:8765 (homelab) — NOT 127.0.0.1:8765
```

**Implication:** changes in `src/cli/src/bridge/server.ts` (off-limits subtree) silently affect desktop+voice+extension+web. The bridge unit move proposed in [2026-05-16 §Q2] / [2026-05-17 §3.2] would convert this hidden cross-coupling into an explicit `src/desktop-bridge/` package, lifting the off-limits-but-edited paradox.

---

**End of map.** Generated 2026-05-17. Cross-referenced against `[2026-05-16 §X]` (10-domain review) and `[2026-05-17 §Y]` (enterprise plan). Word count ~2450.
