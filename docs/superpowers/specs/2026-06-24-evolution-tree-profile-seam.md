# Evolution engine — the tree-profile seam (catalogue + design)

**Status:** catalogue + design (Phase 4 of the 2026-06-24 stabilization). The
extraction itself is deliberately deferred — see "Why not extract now".

**Goal:** today the self-evolution loop only operates on `src/voice-agent/`. To
generalize to `src/web/` and `src/voice-agent/desktop-tauri/`, the *engine* should gain a
swappable per-tree **profile** — the trees never merge, and the engine is not
rewritten. This doc catalogues every tree-specific site (verified by grep, not
estimate) and designs the `TreeProfile` so the future port is a config change.

## Two classes of seam

The hardcoded sites split cleanly, and the split is the whole point:

- **Config seams** — pure data. Trivial to parameterize. (path prefix, tree
  root, interpreter, test command, vendor hints, tree-specific blocklist.)
- **Behavioral seams** — per-tree *strategy*, not data. These cannot be designed
  from voice-agent alone (there is no "turn" or "service restart" in web/desktop)
  — they need the concrete second tree in hand. (health check, restart/deploy
  actuator, error/trigger source.)

## Inventory (every site, verified)

The 9 engine modules that hardcode `src/voice-agent/`:
`_state.py · coverage_gate.py · error_logger.py · error_log_fallback.py ·
finalize.py · watchdog.py · patterns.py · spawner.py · deploy.py`.

### Config seams → `TreeProfile` data fields
| Concept | Sites | voice-agent value | Field |
|---|---|---|---|
| Path scope prefix | `_state.py:132` `ALLOWED_PATH_PREFIX`; `coverage_gate.py:34` `_SRC_PREFIX`; `error_logger.py:36` `_PROJECT_PREFIX`; `error_log_fallback.py:145` | `"src/voice-agent/"` | `src_prefix` |
| Tree root | `finalize.py:29,100` `parents[2]`; `finalize.py:107`, `watchdog.py:143` | `<repo>/src/voice-agent` | `tree_root` |
| Interpreter | `watchdog.py:143`, `finalize.py:107` | `tree_root/.venv/bin/python` | `interpreter` |
| Test command (the GATE) | `finalize.py:111-114` (`pytest tests/` ± `coverage run`); watchdog selftest `:143` | `[py,-m,pytest,tests/,-q,--tb=no]` | `test_command` |
| Vendor/test hints | `error_logger.py:37` `_VENDOR_HINTS`; `coverage_gate.py:55` | `(".venv/","/site-packages/","tests/")` | `vendor_hints` |
| Tree-specific blocklist | `_state.py:106-111` | `sanitizers/ · confab_detector.py · pipeline/automod/ · evolution/ · skill_review.py · prompts/soul.md` | `blocklist_extra` |
| Agent edit-scope prompt | `patterns.py:395`, `spawner.py` prompt | `"Only edit files under src/voice-agent/"` | derived from `src_prefix` |

### Behavioral seams → `TreeProfile` strategy callables (need tree #2)
| Concept | Sites | voice-agent impl | Why per-tree |
|---|---|---|---|
| Health check | `watchdog.py` `_liveness`/`_real_turn_since`/`_smoke_turn`, `_VC_PORT` | voice-client `/status` + a real turn in `turn_telemetry.db` | web = HTTP 200 on the app; desktop = process alive / smoke-launch. No "turn" elsewhere. |
| Restart / deploy actuator | `deploy.py:142`, `cli.py:239`, `watchdog` rollback | `systemctl --user restart jarvis-voice-agent.service` | web = container/service restart; desktop = `cargo build --release` + relaunch |
| Error / trigger source | `error_logger.py` (Py tracebacks from `turn_telemetry.db`, filtered by prefix) | Python tracebacks | web = JS/TS errors; desktop = Rust panics |

### Shared — NOT in the profile (stays universal)
git mechanics (base ref `master`, cherry-pick, rollback `reset --hard`, worktree
isolation); the universal blocklist (`CLAUDE.md · MEMORY.md · USER.md ·
.claude/rules/ · bin/jarvis-automod* · bin/jarvis-evolution-*`); and the engine
proper — cycle orchestration, **the fault boundary**, marker/lock, throttle,
experience signal, artifact store, the `validate_diff` structure.

## `TreeProfile` design

```python
@dataclass(frozen=True)
class TreeProfile:
    name: str                       # "voice-agent"
    src_prefix: str                 # "src/voice-agent/"
    tree_root: Path                 # <repo>/src/voice-agent
    interpreter: Path               # tree_root/.venv/bin/python
    test_command: list[str]         # the fitness gate
    coverage_command: list[str] | None
    vendor_hints: tuple[str, ...]
    blocklist_extra: tuple[str, ...]  # tree-specific protected paths
    # behavioral seams (per-tree strategies):
    health_check: Callable[[], bool]
    restart: Callable[[], tuple[bool, str]]
    collect_faults: Callable[[], list[Fault]]
# Universal (shared, not per-tree): base_ref, universal blocklist, the engine.
```

## Why not extract now (deliberate)
1. **Sound abstraction needs the 2nd implementation.** The three behavioral
   seams differ fundamentally per tree and cannot be designed from voice-agent
   alone — designing the interface against one impl bakes in voice assumptions.
   Extract when web/desktop's concrete shape is in hand.
2. **Merge safety.** Extraction edits ~9 mostly-blocklisted safety files; doing
   that during an in-flight merge invites conflicts on the safety surface.

## Extraction plan (when tree #2 is tackled / merge settled)
1. `pipeline/automod/tree_profile.py`: `TreeProfile` + `VOICE_AGENT` instance.
2. Replace the scattered literals with reads from the active profile (one
   source of truth per concept).
3. Lift `watchdog` health/restart + `error_logger` source into the voice-agent
   profile's strategy functions.
4. Add `WEB` / `DESKTOP` profiles — the moment the behavioral seams get their
   real second design.

## Guard
`tests/test_evolution_tree_seam.py` pins the 9-file catalogue: a new module
hardcoding `src/voice-agent/` fails the test until it's added here — so the
catalogue can't silently rot before extraction.
