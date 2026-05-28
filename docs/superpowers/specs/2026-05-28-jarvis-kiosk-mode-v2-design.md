# JARVIS kiosk mode v2 — research-driven rebuild

**Status:** spec, ready for implementation plan.
**Date:** 2026-05-28.
**Supersedes:** `docs/superpowers/specs/2026-05-27-jarvis-kiosk-mode-design.md` (v1).

## Goal

Replace the v1 single-window/mode-flag kiosk with a **two-window architecture** where kiosk is a freshly-spawned Tauri WebviewWindow fullscreen on an **explicitly-selected** monitor. The kiosk window's content (iteration 1) is a black background with a centered SVG arc reactor — the J.A.R.V.I.S. visual signature — driven by the same `/status` poll the tray indicator uses. The original transparent HUD overlay is untouched by kiosk lifecycle.

## Why redo

v1 (`2026-05-27`) tried to flip the same overlay window between transparent-HUD and opaque-kiosk modes via React conditional render + Tauri window-flag flips. Two days of live use surfaced three structural problems, each independently sufficient to redo:

1. **Click-through race on exit.** `set_position` / `set_size` / `set_always_on_top` / `set_ignore_cursor_events` all dispatch async to the GTK/X11 event loop and don't complete in order. The hotspot-poller (`main.rs:1998`) that manages chat-panel click-through fires between calls, sees the window mid-restoration, and forces `ignore_cursor_events(false)`. Result: after kiosk exits, the entire overlay rect is non-click-through, and the user cannot click any window underneath. Research (Tauri issue #11461; community pattern: `kiosk_transitioning: AtomicBool`) confirms this is a structural race in the single-window approach.
2. **Multi-monitor support is fundamentally weak.** v1 used `window.current_monitor()` to "auto-pick", which assumes there is one right answer. On the live machine the overlay is positioned at origin (1920×1080) while the user's primary screen is at (1361, 1440) 2560×1600 — the auto-pick lands on the wrong monitor. There is no Tauri v2 API to "fullscreen on monitor idx" (`tao` PR #235 was never surfaced; `_NET_WM_FULLSCREEN_MONITORS` exists but isn't wrapped). The right pattern is **explicit selection**, never auto-inference.
3. **Submenu items don't dispatch clicks (Tauri Linux GTK).** Per-monitor `MenuItem`s built in a loop inside a submenu emit menu events that don't reach `on_menu_event` on the first click (Tauri issue #12649) or at all if the parent submenu was built from an async context (issue #11462). v1's per-monitor entries in the tray submenu silently failed. The mitigation pattern requires items to be retained in managed `AppState` (`Vec<MenuItem<Wry>>`) and dispatched via stable IDs.

Layering fixes on v1 (transitioning flag + better monitor picker + AppState-retained items) is possible but doesn't eliminate the structural source: the main overlay's state and the kiosk surface share one window. Spinning a *second* Tauri window for kiosk makes click-through races architecturally impossible (we never touch the main window's state), reduces the kiosk's state to "window exists or doesn't", and isolates the failure surface.

## Non-goals

- Wayland support. JARVIS is X11-only per CLAUDE.md; v2 stays X11. (Tauri v2 + GTK + webkit2gtk runs fine via XWayland for the future, but no Wayland-specific compositor protocols are pursued here.)
- Audio-reactive shader visualizer (LiveKit `AgentAudioVisualizerAura`). Iteration 1 uses pure SVG + CSS keyframes. Audio reactivity is deferred until / if the user asks.
- POS-style touch tile grid. Iteration 1 is just the arc reactor; future iterations may add tiles, transcript, status panels — those will be new specs.
- Mid-kiosk monitor switching (entering on Monitor A while already in kiosk on Monitor B). Exit first, re-enter on the new monitor. The tray gives the user explicit exit; the round-trip is intentional simplicity.
- Persisted last-selected-monitor preference. Each kiosk session picks fresh. If the user wants stickiness later, add a small preference file.
- Voice agent restart / migration of supervisor.md prompt — v2 keeps the v1 `toggle_kiosk` tool surface but updates schema to require `monitor` when `state="on"`.

## SCOPE (per `.claude/rules/regression-prevention.md` rule 1)

**In scope:**

- `src/desktop-tauri/src-tauri/src/kiosk.rs` — rewritten module (lifecycle commands, KIOSK_STATE, `WmctrlAdapter` trait carried forward from v1)
- `src/desktop-tauri/src-tauri/src/tray_kiosk.rs` — new module for tray submenu construction + dispatch
- `src/desktop-tauri/src-tauri/src/main.rs` — modified (revert v1 kiosk wiring, add tray_kiosk module hookup, single delegated `on_menu_event` arm)
- `src/desktop-tauri/src/components/KioskHUD.jsx` — rewritten as the arc-reactor surface (replaces v1's transcript-HUD)
- `src/desktop-tauri/src/components/KioskArcReactor.jsx` — new component (SVG + CSS arc reactor)
- `src/desktop-tauri/src/main.jsx` — modified (add `?route=kiosk` → KioskHUD branch)
- `src/desktop-tauri/src/App.jsx` — modified (revert v1 kiosk wiring entirely: drop kioskMode state, drop kiosk-changed listener, drop conditional render)
- `src/cli/src/bridge/server.ts` — modified (update `/api/kiosk` route schema to require monitor when state=on; same broadcast pattern)
- `src/voice-agent/tools/kiosk_tool.py` — modified (schema requires monitor parameter when state=on)
- `src/voice-agent/prompts/supervisor.md` — modified (one-paragraph update: ask user which screen if not specified)
- `bin/jarvis-kiosk` — rewritten (required monitor argument; no no-arg toggle)

**Out:**

- Sanitizers (`src/voice-agent/sanitizers/`), confab detector, automod blocklist files, soul.md, MEMORY.md, CLAUDE.md, `.claude/rules/*.md`
- Frozen tray indicator (`tray_image_for`, `apply_sharing_ring`, the colour palette, the React→Rust poll, `icons/tray.png`) — per `.claude/rules/desktop-tauri.md`
- The hotspot poller in `main.rs` — it stays exactly as it is; v2 doesn't touch chat-panel click-through (the entire reason we go two-window)
- The voice-client + voice-agent service paths
- Web app, Android, the rest of `src/cli/`

**Why out:** v2 is structured precisely so the failure surface of kiosk doesn't bleed into the main overlay. The hotspot poller, panel-rect mechanism, and main overlay state are *deliberately* left alone — touching them is what made v1 fragile.

## Architecture

```
   TRIGGERS (each MUST supply monitor_idx when state=on)
   ┌────────────────────────────────────────────┐
   │ tray submenu             voice tool         CLI
   │ click "kiosk_mon_N"      toggle_kiosk(      jarvis-kiosk N
   │      │                    state="on",        │
   │      │                    monitor=N)         │
   │      │                       │               ▼
   │      │                       ▼          bridge :8765
   │      │                  bridge :8765    POST /api/kiosk
   │      │                  POST /api/kiosk { state, monitor }
   │      │                       │               │
   │      │                       └───── WS ──────┘
   │      │                          {type:kiosk,
   │      │                           state, monitor}
   │      │                              │
   │      │                              ▼
   │      │                       MAIN window's WS
   │      │                       handler in App.jsx
   │      ▼                              │
   │  Rust on_menu_event ────────────────┴────┐
   │      │                                   │
   │      │   delegates to                    │ invokes via
   │      ▼   tray_kiosk::handle              ▼ Tauri command
   │  ┌──────────────────────────────────────────┐
   │  │ kiosk::enter_kiosk_on_monitor(app, idx)  │
   │  │ kiosk::exit_kiosk(app)                   │
   │  └──────────────────────────────────────────┘
   └────────────────────────────────────────────┘
                       │
                       ▼
   ┌────────────────────────────────────────────┐
   │ enter_kiosk_on_monitor(app, monitor_idx)   │
   │   1. KIOSK_STATE.lock() — refuse if Some   │
   │   2. monitors = app.get_webview_window     │
   │        ("main")?.available_monitors()?     │
   │   3. m = monitors.get(idx)?                │
   │   4. (pos, size) = (m.position(), m.size())│
   │      [scale-factor guard if size < 1024]   │
   │   5. wmctrl: enumerate non-JARVIS visible  │
   │      windows; record ids; minimize each    │
   │   6. WebviewWindowBuilder::new(app,        │
   │       "kiosk", WebviewUrl::App(            │
   │         "index.html?route=kiosk"))         │
   │       .decorations(false)                  │
   │       .transparent(false)                  │
   │       .always_on_top(true)                 │
   │       .focused(true)                       │
   │       .skip_taskbar(true)                  │
   │       .resizable(false)                    │
   │       .position(pos.x, pos.y)              │
   │       .inner_size(size.w, size.h)          │
   │       .build()?                            │
   │   7. wmctrl -r kiosk -b add,above          │
   │      (belt-and-suspenders for XFCE)        │
   │   8. KIOSK_STATE = Some(snapshot)          │
   │   9. emit "kiosk-changed" {on:true,monitor}│
   │  10. tray_kiosk: set_checked(idx, true)    │
   └────────────────────────────────────────────┘
                       │
                       ▼
   ┌────────────────────────────────────────────┐
   │ exit_kiosk(app)                            │
   │   1. KIOSK_STATE.lock() take snapshot      │
   │   2. app.get_webview_window("kiosk")?      │
   │       .close()?  ← Tauri handles teardown  │
   │   3. wmctrl unminimize each snapshot id    │
   │   4. emit "kiosk-changed" {on:false}       │
   │   5. tray_kiosk: clear all check-states    │
   └────────────────────────────────────────────┘
```

**The key invariant (the entire reason for v2):** during enter or exit, the main overlay window's *visual state and Tauri window flags* are never touched. No `set_position`, no `set_size`, no `set_always_on_top`, no `set_ignore_cursor_events` on the main window. The kiosk lives in its own window, born + destroyed per session. The hotspot poller, panel rect, chat panel state, voice chat panel state — every aspect of the main overlay's visible posture stays exactly where it was before kiosk started.

The main overlay does still see the `{type:'kiosk'}` WS broadcast (because it's the WS subscriber) and routes it to the appropriate Rust Tauri command. That routing is mechanical — no React state changes, no overlay flag flips. Once the command runs, the kiosk window is created/destroyed by Rust independently of the main overlay's React tree.

## Components

| File | Role | LOC budget |
|---|---|---|
| `kiosk.rs` (rewritten) | `KIOSK_STATE: Mutex<Option<KioskSnapshot>>` with `minimized_ids: Vec<String>` and `monitor_idx: usize`. `WmctrlAdapter` trait + `RealWmctrl` retained from v1. `enter_kiosk_on_monitor` / `exit_kiosk` / `kiosk_state` Tauri commands. No `enter_kiosk` (auto) and no `toggle_kiosk` — explicit-only. | ~180 |
| `tray_kiosk.rs` (new) | `KioskMonitorItems(Mutex<Vec<CheckMenuItem<Wry>>>)` AppState. `build_kiosk_submenu(app)` enumerates monitors via the main window and constructs the submenu with stable IDs (`kiosk_mon_0`, `kiosk_mon_1`, …) + `kiosk_off`. `handle_kiosk_menu_event(app, id)` dispatches to `kiosk::*` commands. `sync_check_states(app, on_idx: Option<usize>)` sets exactly one CheckMenuItem checked (or all off). | ~140 |
| `main.rs` (modified) | Revert v1 kiosk wiring (drop the inline `focus_mode_submenu` construction, drop the FocusModeItem state, drop the kiosk-changed listener). Add `mod tray_kiosk`. Call `tray_kiosk::build_kiosk_submenu(app)` from setup. Add one `on_menu_event` arm: `id if id.starts_with("kiosk_") => tray_kiosk::handle_kiosk_menu_event(app, id)`. Register new commands in `tauri::generate_handler!`. | -80 lines, +25 lines |
| `KioskHUD.jsx` (rewritten) | Full-screen root for `?route=kiosk`. Black background. Renders `<KioskArcReactor state={voiceState} />`. Polls `:8767/status` every 500 ms to derive state. ESC key handler invokes `exit_kiosk` Tauri command (in-HUD escape hatch). | ~80 |
| `KioskArcReactor.jsx` (new) | Pure SVG: concentric rings, dotted ring, broken outer arcs (matches reference image). Cyan `#1FD5F9` to match LiveKit Aura default. CSS keyframes for `idle` / `listening` / `speaking` / `thinking` / `offline` states — pulse rate, rotation, opacity per state. State driven by a single `state` prop. No per-frame React. | ~150 |
| `main.jsx` (modified) | Add `?route=kiosk` → `<KioskHUD />` branch alongside existing `?route=keys`. | +5 |
| `App.jsx` (modified, mostly revert v1) | Drop `kioskMode` state, drop `KioskHUD` import, drop `kiosk-changed` Tauri listener, drop the conditional render branch. KEEP a slim `{type:'kiosk'}` WS handler that invokes `enter_kiosk_on_monitor` or `exit_kiosk` Tauri commands — main overlay is the WS subscriber, but its role here is just routing the message to the Rust side. No kiosk-aware UI in the main overlay. | -24 lines, +6 lines |
| `bridge/server.ts` (modified) | Update `/api/kiosk` route: require `{state: "on"|"off", monitor?: integer}`. Validate `monitor` is required when state=on, integer ≥ 0. Broadcast `{type:"kiosk", state, monitor}`. | ~25 |
| `kiosk_tool.py` (modified) | Schema: `state: Literal["on","off"]`, `monitor: int` (optional but required-when-state=on; integer index ≥ 0). When `state="on"` and `monitor` is absent, return a `tool_error` instructing the supervisor to ask which monitor number. Name-based resolution ("main", "laptop") deferred to iteration 2 — iteration 1 takes integer indices only. | ~60 |
| `supervisor.md` (modified) | Replace v1 paragraph: "Focus mode requires a monitor. If user says 'focus mode' without naming a screen, ask which one before calling `toggle_kiosk`." | +5 lines, -10 lines |
| `bin/jarvis-kiosk` (rewritten) | `jarvis-kiosk <idx>` to enter on monitor `<idx>`; `jarvis-kiosk off` to exit. No no-arg toggle. Usage line: `usage: jarvis-kiosk <monitor-index>|off`. Monitor enumeration via `xrandr --listmonitors` for the help text (informational only — kiosk itself uses Tauri's enumeration). | ~30 |

Total: ~520 LOC new, ~30 LOC modified, ~110 LOC deleted from v1.

## Data flow

### Enter kiosk on monitor N

```
User → tray "kiosk_mon_N"           voice "Jarvis, focus mode on monitor 1"
        OR                           OR
        bin/jarvis-kiosk N           ALL converge into:
                                     POST /api/kiosk {state:"on", monitor:N}
        │                                          │
        ▼                                          ▼
   on_menu_event "kiosk_mon_N"             bridge broadcasts WS msg
        │                                  {type:"kiosk", state:"on", monitor:N}
        │                                          │
        ▼                                          ▼
   tray_kiosk::handle_kiosk_menu_event       App.jsx WS handler
        │                                          │
        └─────────────┬────────────────────────────┘
                      ▼
        kiosk::enter_kiosk_on_monitor(app, N)
                      │
                      ▼  (steps 1-10 from architecture diagram)
                      │
                      ▼
        New "kiosk" WebviewWindow exists
        emit kiosk-changed {on:true, monitor:N}
                      │
        ┌─────────────┴──────────────────┐
        ▼                                 ▼
   tray_kiosk sync_check_states(N)   "kiosk" window's React app boots
   (only mon_N item gets ✓)          → loads ?route=kiosk
                                     → renders <KioskHUD/>
                                     → KioskHUD polls :8767/status
                                     → KioskArcReactor renders with state
```

### Exit kiosk

```
User → tray "kiosk_off"   OR  ESC key inside kiosk   OR  bin/jarvis-kiosk off   OR
        tray "kiosk_mon_<currently-checked>" (toggle off)
                                          │
                                          ▼
                          kiosk::exit_kiosk(app)
                                          │
                                          ▼  (steps 1-5 from architecture diagram)
                                          │
                                          ▼
                          "kiosk" WebviewWindow destroyed
                          Previously-minimized windows restored
                          emit kiosk-changed {on:false}
                                          │
                                          ▼
                          tray_kiosk sync_check_states(None)
                          (all items unchecked)
```

Main overlay is unaffected. The user sees: kiosk window vanishes, restored windows reappear, tray check clears. No flicker on the main HUD because the main HUD didn't change.

### React side inside kiosk window

```
KioskHUD mounted (route=kiosk):
  useEffect: setInterval 500ms poll → fetch :8767/status
  useEffect: addEventListener keydown → ESC triggers invoke('exit_kiosk')

  render: black-bg div, centered <KioskArcReactor state={state} />

KioskArcReactor:
  receives `state` prop (idle/listening/speaking/thinking/offline)
  renders single SVG with state-derived className: "kiosk-arc-reactor--{state}"
  CSS keyframes scoped to each state class:
    --idle: 4s pulse, 30s slow rotate
    --listening: 1.5s pulse, brighter glow
    --speaking: 0.8s pulse, brightest
    --thinking: 6s rotate only, no pulse
    --offline: dim, no animation
  No per-frame React — only re-renders when state prop changes (every ~500ms at most)
```

## Error handling

| Failure | Behavior |
|---|---|
| `enter_kiosk_on_monitor(idx)` with `idx >= available_monitors().len()` | Return `Err("monitor index out of range; N monitors detected")`; emit no event; CLI/bridge surfaces the error to the user |
| `available_monitors()` returns empty list | Return `Err("no monitors detected — is the main window mapped?")`; CLI prints diagnostic |
| `WebviewWindowBuilder::build()` fails (port conflict, weird WM state) | Roll back: un-minimize the already-minimized windows; clear KIOSK_STATE; return Err to caller |
| `KIOSK_STATE` already Some on enter | Return `Err("kiosk already active on monitor N; exit first")` — explicit refusal, not silent re-enter |
| `KIOSK_STATE` already None on exit | Idempotent — return Ok; emit kiosk-changed{on:false} to allow tray re-sync |
| `wmctrl` binary missing | Enter still succeeds: kiosk window appears on the chosen monitor; other windows are not minimized; logged warning. Exit also succeeds (no-op restore). |
| `wmctrl` minimize/unminimize fails for a specific window | Log warning, continue with the rest |
| `kiosk` window closes via user keypress or WM kill (not via exit_kiosk command) | Tauri's `on_window_event(WindowEvent::CloseRequested)` listener on the kiosk window calls `kiosk::exit_kiosk()` to clean up state + restore windows |
| Bridge `/api/kiosk` 400 (missing monitor) | Voice tool / CLI surfaces error; voice asks user which screen |
| Bridge route 401 | Surface auth error |
| Tauri crashes mid-kiosk | Other windows stay minimized until manually restored; on next start KIOSK_STATE is None (no persistence). User can `wmctrl -lG` + `wmctrl -ir <id> -b remove,hidden` manually, or just hit Super+D and reopen them. |
| HiDPI scale factor causes wrong window size | Detect via `if size.width < 1024 && scale_factor > 1.5 { multiply by scale_factor }` heuristic; logged when triggered |

All failures are LOG + CONTINUE in the Rust module. Tauri command returns surface to the CLI / voice tool via the error string.

## Testing

**Rust unit tests** (`src-tauri/src/kiosk.rs`):

- `enter_with_invalid_idx_returns_err` — Mock monitor list of length 1; call `enter_kiosk_impl` with idx=5; assert error string mentions "out of range"
- `enter_when_already_on_returns_err` — Mock state Some; call enter; assert error "already active"
- `exit_when_off_is_idempotent` — Mock state None; call exit; assert Ok, no wmctrl calls
- `enter_minimizes_non_jarvis_windows` — Mock window list with one JARVIS + two others; assert other ids minimized + recorded in snapshot
- `exit_restores_minimized_windows` — Mock state with two ids; call exit; assert both unminimize calls fired
- `wmctrl_missing_graceful` — Mock adapter returns NotFound; enter succeeds with empty minimized_ids; exit succeeds

The Tauri-window-spawning logic in `enter_kiosk_on_monitor` is not unit-tested (no Tauri test harness for window creation); verified by manual + E2E.

**Voice agent tests** (`tests/test_kiosk_tool.py`):

- `test_state_on_requires_monitor` — `{"state":"on"}` without monitor → tool_error mentioning "which screen"
- `test_state_on_with_idx_succeeds` — `{"state":"on", "monitor":1}` → HTTP mock returns 200 → success string
- `test_state_off` — `{"state":"off"}` → HTTP success
- `test_invalid_state` — `{"state":"toggle"}` → tool_error (no toggle in v2)
- `test_monitor_non_integer` — `{"state":"on", "monitor":"main"}` → tool_error (name resolution deferred to iter 2)

**Bridge tests** (`tests/bridge/test_kiosk_route.ts`):

- `POST /api/kiosk {state:"on", monitor:0}` happy path → 200 + WS broadcast
- `POST /api/kiosk {state:"on"}` without monitor → 400
- `POST /api/kiosk {state:"on", monitor:"abc"}` non-integer → 400
- `POST /api/kiosk {state:"on", monitor:-1}` negative → 400
- `POST /api/kiosk {state:"off"}` → 200

**Manual E2E** (documented in PR description):

1. Start desktop. Verify the main HUD overlay is transparent + click-through as usual.
2. Click any underlying app (Firefox, terminal). Verify clicks land.
3. Right-click tray → "Focus mode (kiosk) ▸". Verify per-monitor entries are present, one per detected screen.
4. Click "kiosk_mon_0" (or whatever the first one is). Verify:
   - Black fullscreen on monitor 0 with arc reactor centered
   - Other windows minimized
   - Main overlay still transparent + click-through (you can click on it to confirm — though it's covered by kiosk on monitor 0)
   - Tray check mark on the chosen monitor's item
5. Press ESC inside kiosk. Verify exit: windows restore, kiosk vanishes, tray check clears.
6. Repeat step 4 for monitor 1. Confirm kiosk now fills monitor 1, not monitor 0.
7. Click "kiosk_mon_1" again while it's active. Verify exit (toggle behavior).
8. Speak: "Jarvis, focus mode on monitor 0." Verify enter.
9. Speak: "Jarvis, focus mode" (without monitor). Verify JARVIS asks "which screen?" — does NOT enter blindly.
10. `bin/jarvis-kiosk 0` from terminal → enter; `bin/jarvis-kiosk off` → exit.
11. **The critical regression check:** after exiting kiosk via any path, click on the main HUD region. Verify clicks pass through to underlying apps as expected (this was broken in v1).
12. `sudo mv $(which wmctrl) /tmp/wmctrl.bak`; enter kiosk → black overlay appears but other windows don't minimize; exit → no crash. Restore wmctrl.
13. Open the keys window via tray ("Manage API Keys…") while kiosk is active. Confirm it appears in normal posture, not affected by kiosk.

## Risks

1. **Two windows = double webview memory.** ~30MB while kiosk is active. On Ulrich's machine (16GB+), negligible. On a Raspberry Pi target this would matter — not relevant here.
2. **First-entry latency.** Spawning a fresh webview takes ~150-300ms. For a focus-mode toggle this is acceptable; for a hot-keyed gaming overlay it wouldn't be. JARVIS isn't a gaming overlay.
3. **`always_on_top` competition.** Both main and kiosk are `always_on_top`; on XFCE the newer one usually wins but not guaranteed. Mitigation: `wmctrl -r kiosk -b add,above` after spawn. Belt-and-suspenders.
4. **Tauri v2 issue #11462 (TrayIcon from async).** All our tray work is synchronous in `setup()`. v2 keeps this; do not move tray construction to an async block.
5. **Tauri v2 issue #12649 (first-click-as-submenu on Linux).** With items retained in AppState by stable ID, the second click consistently fires. Live observation in v1 (per-monitor items "do nothing") was likely this exact issue — items existed but the menu event didn't reach our handler. Holding refs in `KioskMonitorItems` is the documented community fix.
6. **HiDPI scale factor.** `Monitor.position()` / `.size()` *should* be physical pixels per Tauri docs, but issue #14630 confirms `workArea` returns logical. We don't use workArea; we use position+size directly. The scale-factor guard heuristic catches the small chance these are also logical.
7. **Window-close via WM (X close button — doesn't exist for decorations:false — or kill -9).** The `on_window_event(WindowEvent::CloseRequested)` listener handles the visible close cases; for SIGKILL we rely on the next `enter_kiosk_on_monitor` rebuilding state from scratch (since KIOSK_STATE is in-memory only).

## Iteration roadmap

This spec covers iteration 1:

- ✅ **Iter 1 (this spec):** two-window split, explicit monitor selection, black + arc reactor content
- 🔜 **Iter 2 (future spec):** state-aware arc reactor — pulse/rotation tied to live `idle/listening/speaking/thinking/offline` state from `:8767/status`
- 🔜 **Iter 3 (future spec):** add a slim text input strip + recent-transcript fade-up on the kiosk surface — still arc-reactor-centric, just with conversational context
- 🔜 **Iter 4 (future spec):** POS-style touch tile grid for common voice actions (open chat, web search, computer use, etc.) — explicit user trigger to upgrade
- 🔜 **Iter 5 (future spec):** persist last-used monitor + per-monitor layout preferences

Each future iteration is its own spec + plan. v2 (iter 1) is the foundation.

## Migration from v1

v1 spec at `docs/superpowers/specs/2026-05-27-jarvis-kiosk-mode-design.md` and its plan at `docs/superpowers/plans/2026-05-27-jarvis-kiosk-mode.md` are superseded. The implementation commits on branch `feat/kiosk-mode` (5390416d through e8f12a5d, plus 804e6519, b94d37c4, and the diagnostic build commits) remain in history but are functionally replaced when v2 lands. No revert is required — v2's component list deletes the v1-specific surfaces (the v1 kiosk wiring inside `App.jsx`, the single-window flag-based state machine, the v1-style submenu-with-CheckMenuItem-toggle) and adds v2 components in their place.

## Open questions

None. Every choice locked.
