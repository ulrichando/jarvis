# JARVIS Production Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Take the JARVIS monorepo from "works on Ulrich's box" to a lean, correct, enterprise-grade private repo — purge ~430M of tracked junk + leaked secrets, fix the real bugs, harden CI/docs/security, and apply safe dependency bumps.

**Architecture:** Work on one branch (`chore/production-hardening`) off `master`. Phases run in dependency order; each ends committed + verified. The git-history rewrite is the LAST phase (Phase 9), gated, irreversible, run once after every content change is committed and all worktrees are reconciled.

**Tech Stack:** Python 3.13 (voice-agent, pytest), TS/Bun (cli — review-only), Next.js (web), Tauri 2 Rust+React (desktop), Kotlin/NDK (android), `git-filter-repo`.

**User decisions baked in (2026-05-29):**
- Target = **private repo**, **no key rotation** (owner's call). History scrub still removes keys from the repo.
- **android = active ship target** → fix it properly (untrack `.cxx`, `llama.cpp`→submodule, signing, CI).
- **history rewrite = YES** (`git-filter-repo` + `gc`).
- **harvest** `origin/claude/jarvis-project-review-K53es`.

**DECISION GATES (resolve at the marked phase, do NOT guess):**
- ~~G1 (Phase 4)~~ **RESOLVED 2026-05-29: KEEP** `/etc/sudoers.d/jarvis` NOPASSWD-ALL (single-user personal box) — do NOT touch the system file; only document the accepted risk in `CLAUDE.local.md`.
- G2 (Phase 7): project license — unify to MIT, unify to Apache-2.0, or document the cli=MIT/voice-agent=Apache split intentionally?
- G3 (Phase 3/7): `src/cli` is off-limits — removing tracked `jarvis-proxy.err.log` + flipping bridge auth fail-closed need explicit sign-off (they live in `src/cli`).
- G4 (Phase 8): per-branch fate of the 8 unmerged worktree branches (merge / keep / abandon).

**Operational facts:** `origin` = github.com/ulrichando/jarvis.git (private, HTTPS). `git-filter-repo` installed at `~/.local/bin`. 9 worktrees (8 on unmerged branches — see Phase 8/9). 10 merged branches prunable.

---

## Phase 0 — Safety net & working branch

### Task 0.1: Create the working branch

**Files:** none (git state)

- [ ] **Step 1: Confirm clean-ish tree & stash the stray edit**

Run: `git -C /home/ulrich/Documents/Projects/jarvis status --short`
Expected: only `M src/desktop-tauri/src/App.jsx` (the in-flight chat rewire — handled in Task 5.4). Note it; do not discard.

- [ ] **Step 2: Branch off master**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git checkout master
git checkout -b chore/production-hardening
```

- [ ] **Step 3: Verify branch**

Run: `git rev-parse --abbrev-ref HEAD`
Expected: `chore/production-hardening`

### Task 0.2: Inventory uncommitted work across all worktrees (blocks Phase 9)

**Files:** none

- [ ] **Step 1: Dump per-worktree status**

```bash
for w in /home/ulrich/Documents/Projects/jarvis-maya-speech \
         /home/ulrich/Documents/Projects/jarvis/.worktrees/*; do
  echo "=== $w ==="; git -C "$w" status --short 2>/dev/null | head
done
```
Expected: record any worktree with uncommitted changes. **Phase 9 cannot run until every worktree is clean** (commit or stash). This is inventory only — do not modify worktrees yet.

---

## Phase 1 — Make the test suite runnable (unblocks all voice-agent verification)

The documented command `pytest -x` aborts at collection because `tests/test_memory_injection_no_bump.py` imports `server` from a non-existent `src/hub/`. Fix this FIRST so later phases can verify.

### Task 1.1: Guard the missing-hub import

**Files:**
- Modify: `src/voice-agent/tests/test_memory_injection_no_bump.py:1-15`

- [ ] **Step 1: Reproduce the failure**

Run: `cd src/voice-agent && .venv/bin/python -m pytest -x -q tests/test_memory_injection_no_bump.py`
Expected: `ModuleNotFoundError: No module named 'server'`

- [ ] **Step 2: Read the head of the test to see the import shape**

Read `src/voice-agent/tests/test_memory_injection_no_bump.py:1-20` — confirm the `sys.path.insert(... 'hub')` + `import server` lines.

- [ ] **Step 3: Replace the hard import with importorskip**

Change the import block so collection skips cleanly when `src/hub/` is absent:

```python
import sys
from pathlib import Path

import pytest

_hub = Path(__file__).parent.parent.parent / "hub"
if not (_hub / "server.py").exists():
    pytest.skip("src/hub/ not present in this checkout", allow_module_level=True)
sys.path.insert(0, str(_hub))
import server  # noqa: E402
```

- [ ] **Step 4: Verify collection no longer aborts**

Run: `cd src/voice-agent && .venv/bin/python -m pytest -x -q 2>&1 | tail -5`
Expected: suite runs to completion (`2733 passed, 2 skipped` ballpark — the new skip is fine), NO collection error.

- [ ] **Step 5: Commit**

```bash
git add src/voice-agent/tests/test_memory_injection_no_bump.py
git commit -m "fix(voice-agent): skip test_memory_injection_no_bump when src/hub absent (unblocks pytest -x)"
```

---

## Phase 2 — Harvest the prior-review branch (the big deletions)

`origin/claude/jarvis-project-review-K53es` = 4 commits, 493 files, −33,738 lines: untrack `.cxx` (354M), remove `docs/API keys`, delete 3 dead voice-agent modules.

### Task 2.1: Fetch & re-verify the branch is still safe to harvest

**Files:** none

- [ ] **Step 1: Fetch and list its commits**

```bash
git fetch origin claude/jarvis-project-review-K53es
git log --oneline master..origin/claude/jarvis-project-review-K53es
```
Expected: 4 commits (e6e5b9a5 .cxx untrack, 4b693a4f API-keys removal, 57b70d08 dead-module deletion, a996f675 test fixup).

- [ ] **Step 2: Re-confirm the 3 modules are STILL orphaned on current master**

```bash
cd src/voice-agent
for m in _function_call_recovery path_security memory_gate; do
  echo "=== $m ==="; grep -rn "$m" --include='*.py' . | grep -v -E "/(test_|tests/)" | grep -v "def |class " | grep "$m"
done
```
Expected: no live (non-test, non-self) importers. If ANY appears, exclude that deletion from the cherry-pick and flag it.

### Task 2.2: Cherry-pick the cleanup commits

**Files:** deletions per the 4 commits (`src/android/app/.cxx/**`, `docs/API keys`, the 3 modules + their tests)

- [ ] **Step 1: Cherry-pick the four commits**

```bash
git cherry-pick e6e5b9a5 4b693a4f 57b70d08 a996f675
```
If a cherry-pick conflicts (master drift), resolve by preferring the deletion, then `git cherry-pick --continue`.

- [ ] **Step 2: Verify the big wins landed**

```bash
git ls-files 'src/android/app/.cxx/**' | wc -l   # expect 0
git ls-files | grep -c 'docs/API keys'            # expect 0
ls src/voice-agent/sanitizers/_function_call_recovery.py 2>&1 # expect: No such file
```

- [ ] **Step 3: Run the suite**

Run: `cd src/voice-agent && .venv/bin/python -m pytest -q 2>&1 | tail -5`
Expected: green (2733-ish passed). If red, a dead-module deletion was premature — restore it.

- [ ] **Step 4: (already committed by cherry-pick) — no extra commit needed**

---

## Phase 3 — .gitignore hardening

### Task 3.1: Close the .gitignore gaps

**Files:**
- Modify: `.gitignore`

- [ ] **Step 1: Append the new rules**

Add to `.gitignore` (under a new `# Android NDK build scratch` block — `.cxx` is the confirmed offender; scope `*.a`/`*.o` carefully so they don't catch any first-party tracked lib):

```
# Android NDK external-native-build scratch (CMake/Ninja, config-hash keyed) — regenerated every build
src/android/app/.cxx/

# Native build outputs (defense-in-depth; .cxx covers the current offender)
*.o
*.a
*.ninja_deps
*.ninja_log

# Stray runtime error logs
*.err.log

# Belt-and-suspenders: never re-commit a docs-stashed credential note
**/API keys
**/*api*key*.txt
```

- [ ] **Step 2: Verify no FIRST-PARTY tracked file gets newly ignored by `*.a`/`*.o`**

```bash
git ls-files | grep -E '\.(a|o)$' | grep -v 'llama.cpp'
```
Expected: empty. If anything first-party appears, narrow the glob to `src/android/app/.cxx/` only and drop the broad `*.a`/`*.o`.

- [ ] **Step 3: Verify .cxx is now ignored**

Run: `git check-ignore -v src/android/app/.cxx/Release/x.o`
Expected: matches `.gitignore` `src/android/app/.cxx/`.

- [ ] **Step 4: Commit**

```bash
git add .gitignore
git commit -m "chore: gitignore Android .cxx NDK scratch, native build outputs, err logs, credential notes"
```

---

## Phase 4 — Secrets-at-rest + sudoers (G1)

### Task 4.1: Lock down the world-readable .env

**Files:** `src/voice-agent/.env` (filesystem perms only; gitignored, not committed)

- [ ] **Step 1: Confirm + fix perms**

```bash
stat -c '%a' src/voice-agent/.env   # expect 664
chmod 600 src/voice-agent/.env
stat -c '%a' src/voice-agent/.env   # expect 600
```

- [ ] **Step 2: Add an install-time guard**

Read `install.sh` around the voice-agent setup (line ~153). Add, after `.env` is created/copied:

```bash
# Harden secret-bearing env files (owner-only)
for f in "$VOICE_AGENT_DIR/.env" "$HOME/.jarvis/keys.env" "$HOME/.jarvis/local-api-token.env"; do
  [ -f "$f" ] && chmod 600 "$f"
done
```

- [ ] **Step 3: Commit**

```bash
git add install.sh
git commit -m "fix(security): chmod 600 secret-bearing .env files at install + lock src/voice-agent/.env"
```

### Task 4.2: G1 RESOLVED — KEEP sudoers, document accepted risk

**Files:** `CLAUDE.local.md` (gitignored — do NOT touch the system `/etc/sudoers.d/jarvis`)

- [ ] **Step 1:** Do NOT modify the system sudoers file (user chose KEEP for the single-user personal box).
- [ ] **Step 2:** In `CLAUDE.local.md`, under the existing sudoers section, add an explicit "ACCEPTED RISK (2026-05-29)" note: blanket NOPASSWD-ALL is retained intentionally for personal-box convenience; the residual mic/prompt-injection→root blast radius is accepted; revisit if the box ever becomes multi-user or the repo's threat model changes. (CLAUDE.local.md is gitignored, so this is a local-only edit — no commit.)

---

## Phase 5 — Real bug fixes (TDD)

### Task 5.1: Offload sync tool handlers off the event loop

**Files:**
- Modify: `src/voice-agent/tools/_adapter.py` (the `_run` handler dispatch, ~line 142-148)
- Test: `src/voice-agent/tests/test_adapter_sync_offload.py` (create)

- [ ] **Step 1: Write the failing test** — a sync handler that blocks must not block the loop.

```python
import asyncio, time
import pytest
from tools import _adapter  # adjust import to the module's actual package path

@pytest.mark.asyncio
async def test_sync_handler_runs_off_event_loop():
    started = asyncio.Event()
    def blocking_handler(args):
        started.set()
        time.sleep(0.3)   # simulates blocking HTTP
        return {"ok": True}

    wrapped = _adapter._build_wrapped_handler(  # use the real factory
        name="dummy", handler=blocking_handler, is_async=False,
    )
    # While the blocking handler runs, the loop must still service other tasks.
    task = asyncio.create_task(wrapped({}))
    await asyncio.wait_for(started.wait(), timeout=1.0)
    # If the handler ran inline (blocking), this sleep(0) would be starved past the handler's 0.3s.
    t0 = time.monotonic()
    await asyncio.sleep(0)
    assert time.monotonic() - t0 < 0.1, "event loop was blocked by sync handler"
    assert (await task)["ok"] is True
```

- [ ] **Step 2: Run it, confirm it fails** (loop blocked / or factory signature mismatch). Adjust the test to the real `_build_wrapped_handler` signature by reading `_adapter.py` first.

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_adapter_sync_offload.py -v`
Expected: FAIL.

- [ ] **Step 3: Fix the adapter** — read `_adapter.py:140-150`, change the sync branch:

```python
# was: result = handler(args)
result = await asyncio.to_thread(handler, args)
```
(Keep the `await handler(args)` branch for `is_async=True`.)

- [ ] **Step 4: Run test + full suite**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_adapter_sync_offload.py -v && .venv/bin/python -m pytest -q 2>&1 | tail -3`
Expected: new test PASS, suite green.

- [ ] **Step 5: Fix the misleading comment** in `src/voice-agent/tools/image_gen.py:666` (`# sync SDK / HTTP calls; the adapter runs sync handlers fine.`) → `# sync SDK / HTTP; adapter offloads sync handlers via asyncio.to_thread`.

- [ ] **Step 6: Commit**

```bash
git add src/voice-agent/tools/_adapter.py src/voice-agent/tools/image_gen.py src/voice-agent/tests/test_adapter_sync_offload.py
git commit -m "fix(voice-agent): offload sync tool handlers via asyncio.to_thread (no more 30s event-loop freeze on x_search)"
```

### Task 5.2: Web — sanitize rehypeRaw markdown (XSS)

**Files:**
- Modify: `src/web/src/components/markdown/markdown.tsx` (~line 406), `src/web/src/components/markdown/code-block.tsx` (~line 128)
- Modify: `src/web/package.json` (add `rehype-sanitize`)

- [ ] **Step 1: Add the dep**

```bash
cd src/web && bun add rehype-sanitize
```

- [ ] **Step 2: Insert sanitize AFTER rehypeRaw** in `markdown.tsx` rehype plugin array, using a schema that preserves the existing caret/code spans. Read the file first; then:

```tsx
import rehypeSanitize, { defaultSchema } from "rehype-sanitize";
// ...
const sanitizeSchema = {
  ...defaultSchema,
  attributes: { ...defaultSchema.attributes, span: [...(defaultSchema.attributes?.span || []), "className"] },
};
// rehypePlugins: [rehypeRaw, [rehypeSanitize, sanitizeSchema], ...rest]
```

- [ ] **Step 3: HTML-escape the code-block fallback** at `code-block.tsx:128` (escape `&<>"'` before interpolation in the pre-Shiki path).

- [ ] **Step 4: Verify build + the existing tests**

Run: `cd src/web && bun run build 2>&1 | tail -5 && bun test 2>&1 | tail -5`
Expected: build OK; tests pass. Manually confirm a code block + the JARVIS caret indicator still render (sanitize schema not too tight).

- [ ] **Step 5: Commit**

```bash
git add src/web/src/components/markdown/markdown.tsx src/web/src/components/markdown/code-block.tsx src/web/package.json src/web/bun.lock
git commit -m "fix(web): sanitize rehypeRaw markdown + escape code-block fallback (XSS hardening)"
```

### Task 5.3: Web — remove the /api/dbg debug sink

**Files:**
- Delete: `src/web/src/app/api/dbg/route.ts`
- Modify: `src/web/src/components/chat/composer.tsx:~82`, `src/web/src/app/chat-test/page.tsx:~15`

- [ ] **Step 1: Confirm callers**

Run: `cd src/web && grep -rn '/api/dbg' src/`
Expected: composer.tsx + chat-test/page.tsx.

- [ ] **Step 2: Remove the route + both fetch calls.** Delete `route.ts`; delete the `fetch('/api/dbg', …)` line in each caller (it's fire-and-forget logging, no return value used).

- [ ] **Step 3: Verify**

Run: `cd src/web && grep -rn '/api/dbg' src/ ; bun run build 2>&1 | tail -3`
Expected: no matches; build OK.

- [ ] **Step 4: Commit**

```bash
git add -A src/web/src
git commit -m "chore(web): remove disposable /api/dbg debug sink + callers"
```

### Task 5.0: Gate flaky token-costing live-integration tests behind an opt-in (found in Phase 1)

**Files:**
- Modify: `src/voice-agent/tests/test_dispatch_agent_integration.py` (add an opt-in skip)

**Why:** `test_real_dispatch_explore_finds_file_path` spawns the real `bin/jarvis`, costs API tokens, and asserts an exact substring (`dispatch_agent.py`) on non-deterministic LLM output. It's gated only on `ANTHROPIC_API_KEY` being present, so it runs by accident anywhere the key is set (local dev, and CI if the key is provided) and fails flakily. Same class as `test_github_subagent.py` (already `--ignore`d in CI).

- [ ] **Step 1:** Add a module-level opt-in skip alongside the existing key/bin guards:

```python
import os, pytest
if os.environ.get("JARVIS_RUN_INTEGRATION", "").strip() != "1":
    pytest.skip("integration test — set JARVIS_RUN_INTEGRATION=1 to run (spawns bin/jarvis, costs tokens)", allow_module_level=True)
```
- [ ] **Step 2:** Verify it skips by default: `cd src/voice-agent && .venv/bin/python -m pytest -q tests/test_dispatch_agent_integration.py` → `skipped`. And that `JARVIS_RUN_INTEGRATION=1 ... ` still selects it (don't actually run the token-costing dispatch in CI). Commit.
- [ ] **Step 3:** Apply the same opt-in gate to `tests/test_github_subagent.py` so CI no longer needs the `--ignore` hack (then drop `--ignore=tests/test_github_subagent.py` from voice-agent-tests.yml in Phase 7 CI work).

### Task 5.4: Desktop — App.jsx stuck loading spinner

**Files:**
- Modify: `src/desktop-tauri/src/components/ChatPanelVscode.jsx` (~line 443 setIsLoading)

- [ ] **Step 1: Read the in-flight diff first**

Run: `git diff -- src/desktop-tauri/src/App.jsx`
Decide: complete it or revert. The bug is in ChatPanelVscode: `isLoading` set true on send, cleared only on SSE `assistant_says`. A dropped SSE leaves it stuck.

- [ ] **Step 2: Add a safety timeout** that clears `isLoading` after N seconds if no response arrives (clear it in the SSE handler too, so the timer is cancelled on success):

```jsx
// on send, after setIsLoading(true):
const t = setTimeout(() => setIsLoading(false), 60000);
// store t in a ref; clearTimeout(t) wherever setIsLoading(false) fires on success/error
```

- [ ] **Step 3: Verify build**

Run: `cd src/desktop-tauri && npm run build 2>&1 | tail -3`
Expected: vite build OK (~7s). (Release rebuild `cargo build --release` deferred to a release task.)

- [ ] **Step 4: Commit** (include the App.jsx in-flight edit if completing it)

```bash
git add src/desktop-tauri/src
git commit -m "fix(desktop): clear chat loading spinner on timeout (stuck 'thinking' on dropped SSE)"
```

---

## Phase 6 — Dependency bumps (item #5)

### Task 6.1: setuptools security bump (validate pkg_resources)

**Files:** `src/voice-agent/requirements.txt`

- [ ] **Step 1: Change the pin** `setuptools<70` → `setuptools>=78.1.1,<82` (keeps `pkg_resources`, carries CVE-2025-47273 fix).
- [ ] **Step 2: Install + validate pkg_resources consumers still import**

```bash
cd src/voice-agent && .venv/bin/pip install 'setuptools>=78.1.1,<82' && .venv/bin/python -c "import pkg_resources; print('pkg_resources OK')" && .venv/bin/python -m pytest -q 2>&1 | tail -3
```
Expected: `pkg_resources OK`, suite green. If anything breaks, revert the pin and flag.

- [ ] **Step 3: Commit** `git commit -am "deps(voice-agent): setuptools>=78.1.1,<82 (CVE-2025-47273, keeps pkg_resources)"`

### Task 6.2: Bound ai-edge-litert

**Files:** `src/voice-agent/requirements.txt`

- [ ] **Step 1:** `ai-edge-litert>=2.1.5` → `ai-edge-litert~=2.1`. Run `.venv/bin/pip install -r requirements.txt` (no-op at 2.1.5) + `pytest -q`. Commit.

### Task 6.3: LiveKit + anthropic minor bumps

**Files:** `src/voice-agent/requirements.txt` (specifiers already allow; this updates installed)

- [ ] **Step 1:** `.venv/bin/pip install -U 'livekit-agents[silero,openai,groq,anthropic]' livekit-plugins-deepgram anthropic` (→ livekit 1.5.14, anthropic 0.105.2).
- [ ] **Step 2:** Validate the load-bearing `anthropic_strict_schema` monkey-patch still holds: `.venv/bin/python -m pytest -q -k 'strict_schema or anthropic or sanitiz' 2>&1 | tail` then full `pytest -q`. If the anthropic 0.105 changelog reshaped tool-schema serialization, fix the patch.
- [ ] **Step 3:** Commit.

### Task 6.4: Tauri 2.10.3 → 2.11.2

**Files:** `src/desktop-tauri/src-tauri/Cargo.lock`

- [ ] **Step 1:** `cd src/desktop-tauri/src-tauri && cargo update -p tauri --precise 2.11.2` (pulls wry 0.55 transitively).
- [ ] **Step 2:** `cargo check` then a release rebuild + smoke per the desktop release rule: `cd .. && npm run build && cargo build --release`. Launch + verify the WebKitGTK ghost-frame fix and the tray indicator still behave.
- [ ] **Step 3:** Commit `Cargo.lock`.

### Task 6.5: Android Compose BOM bump

**Files:** `src/android/gradle/libs.versions.toml`

- [ ] **Step 1:** Compose BOM `2024.12.01` → `2026.05.01`. (Standalone, low-risk; independent of the AGP decision.)
- [ ] **Step 2:** `cd src/android && ./gradlew :app:assembleDebug 2>&1 | tail` (needs NDK/SDK; if unavailable here, mark for the android dev box). Commit.

### Task 6.6: Delete the vestigial root package.json

**Files:** Delete `package.json`, `bun.lock`; remove root `node_modules/` (gitignored)

- [ ] **Step 1: Re-confirm zero consumers**

```bash
grep -rn '@ai-sdk/' --include='*.ts' --include='*.tsx' --include='*.js' . | grep -v node_modules | grep -v 'src/cli' | grep -v 'src/web'
```
Expected: empty (the 5 root @ai-sdk deps have no root consumer).

- [ ] **Step 2:** `git rm package.json bun.lock && rm -rf node_modules`. Verify nothing at root runs `bun install` (grep `bin/`, `install.sh`, `.github/`). Commit.

### Task 6.7: Document the AGP 8→9 deferral

**Files:** `src/android/README.md` (or a new `docs/deps-roadmap.md`)

- [ ] **Step 1:** Add a note: "AGP 8.7.3 → 9.x is a planned major (Gradle 9, DSL-interface changes, AGP-10 deadline mid-2026 removes the opt-out). Deferred — schedule deliberately; verify LiteRT-LM/Kotlin-2.2 coupling before Kotlin 2.3." Commit.

---

## Phase 7 — Docs + enterprise hardening

### Task 7.1: DECISION GATE G2 — license, then add root LICENSE

**Files:** Create `LICENSE`; modify `package.json` license fields; modify `README.md`

- [ ] **Step 1:** G2 RESOLVED 2026-05-29 = **unify to Apache-2.0**. Add a root `LICENSE` (Apache-2.0, copyright Ulrich Ando), replace `src/cli/LICENSE` (MIT) with Apache-2.0 to match — **NOTE: `src/cli/LICENSE` is in the off-limits tree; replacing a LICENSE file is not code, but confirm under G3 before touching it**. Set `"license": "Apache-2.0"` in each first-party `package.json` + `pyproject.toml`. Add a Licensing section to README. Commit.

### Task 7.2: SECURITY.md + ARCHITECTURE.md + CHANGELOG + CONTRIBUTING

**Files:** Create `SECURITY.md`, `ARCHITECTURE.md`, `CHANGELOG.md`, `CONTRIBUTING.md`

- [ ] **Step 1: SECURITY.md** — vuln-report path; document the threat model (mic/prompt-injection, the sudoers posture per G1, the secret-handling model). 
- [ ] **Step 2: ARCHITECTURE.md** — distill `docs/2026-05-17-jarvis-repo-map.md` + CLAUDE.md's "Voice-agent architecture" into a non-Claude-facing overview; **link, don't copy** (avoid drift).
- [ ] **Step 3: CHANGELOG.md** — seed from `git log` highlights.
- [ ] **Step 4: CONTRIBUTING.md** — build/test commands per subtree; the regression-prevention rules summary.
- [ ] **Step 5:** Commit.

### Task 7.3: Fix CLAUDE.md drift (human-edit; CLAUDE.md is on automod blocklist — that's fine for a human)

**Files:** `CLAUDE.md`

- [ ] **Step 1:** Soften "NO subagent layer" → note `tools/dispatch_agent.py` exists (out-of-process CC-style dispatch since 2026-05-27, distinct from the banned in-process HandoffSubagent). Add an `android` row + an ACP note to the stack table. Commit.

### Task 7.4: .env reference doc (201 JARVIS_* flags)

**Files:** Create `docs/env-reference.md`; optionally a CI drift-check

- [ ] **Step 1:** Generate a categorized reference (required / optional / kill-switch) from `grep -rhoE 'JARVIS_[A-Z0-9_]+' src/voice-agent/ | sort -u`, grouped by subsystem, REQUIRED keys flagged (LiveKit, provider keys, DEEPGRAM_API_KEY). 
- [ ] **Step 2:** (optional) add a `voice-agent-tests.yml` step that diffs code-referenced vars vs the doc and warns. Commit.

### Task 7.5: AGENTS.md symlink → regular file (Windows)

**Files:** `AGENTS.md`

- [ ] **Step 1:** Replace the `120000` symlink with a regular file mirroring `src/web/AGENTS.md`'s approach (a short pointer doc, or a checked-in copy regenerated by a hook). Verify `git ls-tree HEAD AGENTS.md` shows `100644`. Commit.

### Task 7.6: docs/ archive split + index

**Files:** Create `docs/superpowers/specs/README.md`, `docs/superpowers/specs/archive/`

- [ ] **Step 1:** Add a specs index marking superseded/tombstoned specs (e.g. the LangGraph pair deleted in f38c358). Move clearly-tombstoned specs to `archive/`. **Rewrite any CLAUDE.md / cross-doc links** that point at moved files (grep `docs/superpowers/specs/<moved>`). Commit.

### Task 7.7: llama.cpp vendored tree → pinned git submodule (android active)

**Files:** `.gitmodules` (create), `src/android/app/src/main/cpp/llama.cpp/` (untrack vendored, add as submodule), `src/android/app/src/main/cpp/CMakeLists.txt` (update stale submodule comments)

- [ ] **Step 1:** Identify the vendored commit (check `llama.cpp/` for a version marker). 
- [ ] **Step 2:** `git rm -r --cached src/android/app/src/main/cpp/llama.cpp && rm -rf <dir>` then `git submodule add https://github.com/ggml-org/llama.cpp src/android/app/src/main/cpp/llama.cpp` and `cd` in to `git checkout <pinned-sha>`. 
- [ ] **Step 3:** Update `CMakeLists.txt:55,172` comments to match the submodule reality. 
- [ ] **Step 4:** Verify the android build still finds `llama.cpp/CMakeLists.txt` (`./gradlew :app:assembleDebug` on the android box, or static check). Commit (`.gitmodules` + the submodule gitlink + CMake comment).
- [ ] **Step 5:** Add `git submodule update --init --recursive` to `install.sh`'s android note + README prerequisites.

### Task 7.8: livekit-server.bin → fetch-at-install with SHA256

**Files:** `install.sh`, create `setup/livekit-server.bin.sha256`; eventually untrack the 49M binary (its history removal happens in Phase 9)

- [ ] **Step 1:** Pin a LiveKit release URL + record its SHA256 in `setup/livekit-server.bin.sha256`. 
- [ ] **Step 2:** Add an `install.sh` step: download to `src/voice-agent/livekit-server.bin`, verify SHA256, fail loudly on mismatch. 
- [ ] **Step 3:** `git rm --cached src/voice-agent/livekit-server.bin` + add to `.gitignore`. Verify `livekit-server.service` + `install.sh:405 generate-keys` still resolve the on-disk binary. 
- [ ] **Step 4:** Commit (the in-history 49M blob is purged in Phase 9).

### Task 7.9: Extend backups to conversations.db + memory store

**Files:** `scripts/jarvis-backup-local.sh`

- [ ] **Step 1: First verify the live conversations.db path** (`~/.jarvis/conversations.db` is 0 bytes — confirm the real store before assuming empty-by-design).
- [ ] **Step 2:** Add `backup_one "conversations" <real-path>` (uses the SQLite `.backup` API the script already has) + a tar/rsync snapshot of `~/.claude/projects/-home-ulrich-Documents-Projects-jarvis/memory/` (plain markdown). Run the script once, confirm snapshots appear. Commit.

### Task 7.10: CI — add the missing jobs + CODEOWNERS

**Files:** `.github/workflows/web-tests.yml` (create), `.github/workflows/android-smoke.yml` (create), add a lint job, `.github/CODEOWNERS` (create); G3 for cli

- [ ] **Step 1:** `web-tests.yml` — path-filter `src/web/**` → `bun install && bun test && bun run build`. Mirror `voice-agent-tests.yml`'s path-filter pattern.
- [ ] **Step 2:** `android-smoke.yml` — path-filter `src/android/**` → `./gradlew testDebugUnitTest assembleDebug` (provision NDK/SDK; gate carefully — slow).
- [ ] **Step 3:** Add a fast lint job: `ruff check src/voice-agent`, `tsc --noEmit` for web, `cargo clippy` (start `continue-on-error: true`, ratchet later).
- [ ] **Step 4:** `CODEOWNERS` requiring review on `src/voice-agent/sanitizers/`, `confab_detector.py`, `prompts/soul.md`, `pipeline/automod/`, `CLAUDE.md`, `.claude/rules/` (mirror the automod HARD_BLOCKLIST).
- [ ] **Step 5 (G3):** cli test job + bridge fail-closed default need sign-off (src/cli off-limits) — leave a documented stub, do not wire without approval.
- [ ] **Step 6:** Commit.

### Task 7.11: Android release signing + status

**Files:** `src/android/app/build.gradle.kts`

- [ ] **Step 1:** Add a `release` `signingConfig` reading keystore creds from `local.properties` (same gitignored pattern as `hf.token`); uncomment the release signing wire-up. Fix README Gradle `8.9`→`8.11.1`. Commit. (Android = active ship target per decision.)

---

## Phase 8 — Branch cleanup (G4)

### Task 8.1: Reconcile unmerged worktree branches

**Files:** none (git)

- [ ] **Step 0 (SECURITY — found in Phase 0 inventory):** Worktrees `news-widget`, `screen-watching`, `voice-quality` have secret files **staged** (`A`) in their index — `.env`, `src/cli/.env.local`, `src/cli/.env.providers`, `src/convex/.env.local`, `src/voice-agent/.env`, `src/web/.env.local`. Before merging ANY of these branches, `git -C <wt> restore --staged <those files>` so a `git commit` can't land live secrets. (They are not yet committed, so they're not in history — but they are a landmine.) Also note a `src/convex/` subtree appears in these branches. `barge-in-truncation` has uncommitted new files (sanitizer + tests); `kimi-supreme` has uncommitted web work — preserve or commit deliberately.
- [ ] **Step 1: DECISION GATE G4** — for each of the 8 unmerged branches (`barge-in-truncation`, `kimi-supreme-mode`, `kiosk-mode`, `news-widget`, `screen-watching`, `voice-quality`, `regression-prevention`, `feature/app-builder-ui-redesign`): decide merge-now / keep / abandon. **This must happen before Phase 9** — the rewrite changes their base SHAs.
- [ ] **Step 2:** Merge the keepers into master (or onto the hardening branch); for keepers not yet ready, note that Phase 9 will rewrite them too (they'll need rebasing); abandon the rest with `git branch -D` + `git worktree remove`.

### Task 8.2: Prune merged branches

- [ ] **Step 1:** Delete the 10 merged branches: `for b in feat/automod-auto-merge-rollback feat/automod-error-driven feat/cli-output-token-bumps feat/cli-proxy-bugfixes feat/cli-proxy-vision feat/ext-browser-control-v3 feat/gemini-live-screen-share feat/post-tool-reply-gate-heartbeat fix/automod-wrapper-stale-master-and-finalize-gate; do git branch -d "$b"; done` (use `-d`, not `-D`, so git refuses any not-actually-merged). 
- [ ] **Step 2:** `git worktree prune` (clears the prunable `maya-speech`). Confirm `git worktree list` is clean.

---

## Phase 9 — History rewrite (CAPSTONE — irreversible, gated)

> **DO NOT START** until: all content phases committed, ALL worktrees clean (Task 0.2), branches reconciled (Phase 8), and the user gives explicit go for the force-push. Follow `docs/runbook/git-history-scrub.md`.

### Task 9.1: Full backup

- [ ] **Step 1:** `git clone --mirror /home/ulrich/Documents/Projects/jarvis /tmp/jarvis-prerewrite-backup.git` + `cp -r .git /tmp/jarvis-dotgit-backup`. Verify the mirror has all refs.

### Task 9.2: Run filter-repo

- [ ] **Step 1:** Purge the historical blobs + the secret file:

```bash
cd /home/ulrich/Documents/Projects/jarvis
git filter-repo --force \
  --path 'docs/API keys' --invert-paths \
  --path-glob 'src/android/app/.cxx/*' --invert-paths \
  --path-glob 'src/desktop-tauri/public/*.wasm' --invert-paths \
  --path src/voice-agent/livekit-server.bin --invert-paths
```
(Plus `--replace-text` for the two literal keys embedded in `docs/runbook/*.md`, using a replacements file → `REDACTED`.)

- [ ] **Step 2:** `git reflog expire --expire=now --all && git gc --aggressive --prune=now`
- [ ] **Step 3:** Verify shrink + scrub:

```bash
du -sh .git                                   # expect <50M (was 302M)
git log --all --oneline -- 'docs/API keys'    # expect empty
git rev-list --all --objects | grep -E '\.cxx/|livekit-server.bin|\.wasm$'  # expect empty
```

### Task 9.3: Re-sync worktrees + force-push

- [ ] **Step 1:** For each surviving worktree: `git -C <wt> fetch` then `git -C <wt> reset --hard <its-branch>` (SHAs changed). Or remove + recreate. 
- [ ] **Step 2:** `git remote add origin …` if filter-repo stripped it; then **explicit-go force-push:** `git push --force --all origin && git push --force --tags origin`. 
- [ ] **Step 3:** Final verify: fresh `git clone https://github.com/ulrichando/jarvis.git /tmp/jarvis-verify` → `du -sh /tmp/jarvis-verify/.git`, confirm `docs/API keys` absent from `git log --all`, suite still green.

---

## Self-review notes

- **Spec coverage:** every audit finding maps to a task — secrets (P0→Phase 4/9), .cxx/llama.cpp/livekit bloat (Phase 2/7/9), bugs (Phase 1/5), docs/LICENSE/CLAUDE-drift (Phase 7), CI/CODEOWNERS/backups/android (Phase 7/11), deps incl. item-5 options (Phase 6), branch hygiene (Phase 8), history rewrite (Phase 9). Item-1 deletes = Phase 2/3/6.6/9; item-2 docs+gitignore = Phase 3/7; item-3 bugs = Phase 1/5; item-4 enterprise = Phase 4/7; item-5 deps = Phase 6 (options presented, user picks safe set).
- **Gates:** G1 sudoers, G2 license, G3 cli-tree edits, G4 branch fates — all explicit, none guessed.
- **Ordering:** test-suite-runnable (P1) before verification-dependent work; all content committed before the irreversible rewrite (P9); worktrees reconciled (P8) before SHA rewrite.
- **Off-limits respected:** no `src/cli` code edits without G3 sign-off; cli findings are flag-only.
