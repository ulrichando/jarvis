---
description: CLI codebase boundary rule (separate codebase — active dev area; don't let CLI edits break sibling trees)
paths:
  - src/cli/**
---

# CLI rules — separate codebase, active dev area (don't break siblings)

`src/cli/` is the **`jarvis` CLI agent** (Claude-Code-shaped, TypeScript/Bun). It is a separate codebase from the voice-agent / desktop / web. **CLI edits are authorized (2026-06-27)** — the rule is no longer "don't touch," it's **"don't let a CLI change break a sibling tree."** Follow `.claude/rules/regression-prevention.md`, verify with `bun build <file> --no-bundle` + a targeted bundle of the *changed* file (never whole-graph-bundle `cli.tsx` — it always fails on lazy/native imports like `contextCollapse`/`modifiers-napi`), and remember the bridge + Tauri call into CLI paths over HTTP/IPC.

**You have the upstream source.** The original Claude Code checkout lives at `~/Documents/Projects/claude-code/src/` and mirrors `src/cli/src/`. When a module is missing or a capability is half-wired, `diff` the donor per-file first — port real logic rather than guessing. But note: the donor is the **public** build, so Anthropic-internal modules are stripped from it too (see the gate list below).

**Capability gating (why "it's there but does nothing").** Most CLI capabilities ship complete but compile to dead code behind Bun `feature('X')` macros (`import { feature } from 'bun:bundle'`): `feature('X')` is `true` only when `--feature=X` is passed in `src/cli/scripts/start.sh`. The launcher enables a deliberate subset; the rest are dark with **no error**. To turn one on: add its `--feature=` flag there, then **verify end-to-end with `bin/jarvis -p "<prompt that exercises it>"`** — parsing clean is not enough.

**Enabling a flag can wedge the whole CLI — test the boot path.** Some flags `require()` a module that was stripped from the public build. A top-level require throws "Cannot find module" (loud); a require inside an init function (e.g. `initBundledSkills`) can **silently hang** the headless/REPL boot before the first model request. After adding any flag, run `bin/jarvis -p "say OK"` and confirm it returns. If it hangs, bisect the flags you added. (This is how the 2026-06-27 "most tools aren't working" hang was found — see below.)

**Never enable these flags** — Anthropic-internal / phone-home / stripped-module, all either useless or fatal self-hosted:
`REVIEW_ARTIFACT` (requires `hunter.js` + `ReviewArtifactTool` + `ReviewArtifactPermissionRequest`, none of which exist in jarvis OR the donor — **hangs boot**), `KAIROS*`, `TEAMMEM`, `CHICAGO_MCP`, `DOWNLOAD_USER_SETTINGS`/`UPLOAD_USER_SETTINGS`, `COMMIT_ATTRIBUTION`, and the telemetry gates (`SLOW_OPERATION_LOGGING`, `HARD_FAIL`, `*_TELEMETRY`). Don't flip `USER_TYPE` to `ant` globally.

**Two from-scratch modules** were written because the donor lacks them (stripped): `src/cli/src/cli/bg.ts` (BG_SESSIONS — `jarvis ps|logs|attach|kill|--bg` over tmux + `~/.jarvis/sessions/`) and `src/cli/src/utils/taskSummary.ts` (BG_SESSIONS — writes a session's "what am I working on" line for `jarvis ps`). If BG_SESSIONS regresses, check these first.

**`src/cli/src/utils/jarvisInChrome/` is reserved** for future Firefox/Chrome extension work. Don't delete or refactor it as "unused." (Renamed from `claudeInChrome/` in the 2026-06-27 Claude→Jarvis rebrand; the internal MCP wire id stays `claude-in-chrome` for protocol stability.)

**Launcher shape:** `bin/jarvis` → `scripts/start.sh` (source-run: `bun --feature=… cli.tsx`). `start.sh` sources `start-env.sh` (keys/model/proxy env) + `proxy-runtime.sh` (proxy startup). Do NOT point `bin/jarvis` at a compiled binary unless it was built WITH the `--feature=` flags — a feature-less binary dead-code-eliminates every gated tool.

**Run:** `bin/jarvis` (top-level entrypoint, has gstack skill access).

**Build:** Bun-based; see [src/cli/package.json](../../src/cli/package.json).

The CLI has its own auto-memory layer separate from the voice-agent's memory dir. They don't share state.
