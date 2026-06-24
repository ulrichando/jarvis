# JARVIS desktop-tauri review — 2026-05-16

Scope: `src/voice-agent/desktop-tauri/` after today's realtime-mode tray removal.
Read-only. Cross-checked against `.claude/rules/desktop-tauri.md`,
`.claude/rules/regression-prevention.md`, and `CLAUDE.md`.

---

## TL;DR — top 5

1. **Realtime-mode cleanup is complete**, but the screen-share emit is
   now an orphan: `main.rs:1830` still fires `tray-toggle-screen-share`
   and **nothing in JS listens for it**. Either drop the emit or wire
   it through React if you want a UI confirmation toast. (P1)
2. **`main.rs` is 2090 lines in a single file** with eight responsibilities
   (icon tinting, env-keys CRUD, IPC commands, model-switch curls, browser
   probe, embedded HTML, tray menu, hotspot poller). The only real harm
   today is review/grep cost — but the next contributor will keep piling
   on. A module split would pay for itself in one PR. (P2)
3. **TTS is the only submenu with `✓` markers; Speech & Tool submenus
   are silently inconsistent.** The user can tell which TTS voice is
   active from the menu alone, but has to read the header line to see
   which Speech / Tool model is active. Mirror the TTS pattern. (P1)
4. **`speech.silentMode` is set but never used in `tray-state` math
   that the user would notice**. The effect at App.jsx:222 promotes
   it to `muted`, which is fine — but the wiring in the hook
   (`useVoiceClient.js:128`) is the only field that depends on the
   voice-client's separate `silent_mode` flag, and there's no
   tray-menu surface that distinguishes "user muted" from "silent
   mode" (a different state). Either drop `silentMode` from the
   public hook shape, or surface it. (P2)
5. **Incremental build behaviour is correct but undocumented in
   tooling** — `cargo build --release` is required for JS changes to
   ship per `.claude/rules/desktop-tauri.md`, and there is no
   `npm run release` wrapper that does both. A two-line script
   eliminates the entire class of "stale dist" bugs. (P1)

---

## 1. main.rs structure

**Score: B-. Works fine, hard to maintain at 2090 lines.**

The file owns 8 distinct concerns:

1. PNG icon decoding + RGBA tint pipeline (lines 58–211)
2. `xdotool` helpers (213–256)
3. API key CRUD (260–615) — including `_keys_file`,
   `_repo_env_files`, `_parse_env_file`, `_remove_key_from_file`,
   `_keys_write_map`
4. Voice-session-active check + 5 unit tests (305–431) — **all
   dead code today** with `#[allow(dead_code)]`
5. `set_*` IPC commands (631–945)
6. Web probe + bun spawner + diagnostic window (962–1323)
7. Tauri Builder + tray menu construction + hotspot poller (1327–2090)
8. Embedded HTML in `show_web_not_running_window` (1226–1284) — ~60
   lines of HTML/CSS/JS as a raw string inside Rust

**Send/Sync ownership is clean.** Every shared mutable state is
`Arc<Mutex<T>>` and held via `tauri::State`. The five label/item
mutexes (`ProviderLabel`, `SpeechLabel`, `TtsLabel`, `TtsVoiceItems`,
`ShareLabel`) follow the same shape — easily abstractable into a
`TrayMenuRefs` struct.

**Error handling is mixed.** Many `?` for `MenuItemBuilder::build`
inside `setup`, but a lot of `let _ =` discards — 45 of them,
including `let _ = win.set_ignore_cursor_events(true)` in the hot
poll loop, where silent failure means the panel sticks in
click-through-on or click-through-off forever. Acceptable as a UX
trade (don't spam logs every 33ms) but worth a one-time
`if let Err(e)` to print on first failure then suppress.

**Dead code:**
- `voice_session_within_60s` (305) + its 5 tests (344–430) —
  marked `#[allow(dead_code)]` with a comment explaining why it
  was retained ("if a future 'Pause' / 'Restart' tray option
  wants the same guard"). Reasonable to keep — the tests still
  fire on `cargo test` and the function will need to come back
  for tray-Restart.
- `cli_model_pretty` lists `deepseek-reasoner` (688) but
  `speech_model_pretty` does not list it — yet `model_deepseek-reasoner`
  IS in the menu (line 1648) and routes to `switch_cli_model`
  (1884) which is correct. So no orphan; the menu correctly
  exposes reasoner for **tool** use but not for **speech** use.
  Comment-worthy at least.

**One latent bug:** Line 1748 — `let chat_open_tray = Arc::clone(&chat_open);` —
captures the original `chat_open` (which is moved into `.manage(...)` at line
1352 via `chat_open_state`). This works because of `Arc::clone` semantics on
the original variable BEFORE the move, but the variable shadowing is
confusing. It's correct, just hard to follow.

---

## 2. React App.jsx

**Score: B+. Tight, just a couple of effect-deps gotchas.**

**Tray state machine (lines 209–230)** has correct priority cascade and
deps (line 229: 8 deps + `pushTrayState`). `lastTrayStateRef` dedupes
identical state pushes, so the IPC traffic at 10 Hz poll → effect →
`set_tray_state` is bounded to actual transitions.

**Bug — effect deps mismatch (line 120):**
```js
useEffect(() => { ... }, [wsMessages])
```
The effect reads `openChat`, `closeChat`, and `lastHandledRef` (closure-only,
fine). But it doesn't include `openChat` / `closeChat` in deps. The handlers
themselves are stable via `useCallback` but their identity changes whenever
their own deps (`setClickThrough`, `setLayer`, `syncChatState`, `reportPanelBounds`)
change. In practice those are all `useCallback([])`, so they're stable —
but a linter would warn and the next refactor would silently break things.
Add them or mark with `eslint-disable`. (P2)

**Bug — race condition (line 51):**
```js
useEffect(() => { connect() ... }, [connect])
```
On `connect` identity change (URL change), the prior socket isn't closed
**before** the new one is opened. The cleanup runs on unmount only.
For a constant URL this never fires, but the pattern is fragile. Fine
as-is since `WS_URL` is module-scope frozen. (P2 if URL ever becomes dynamic)

**Click-through layer logic** correctly delegates the live cursor-vs-rect
decision to the Rust hotspot poller. The fallback `setClickThrough(false)`
on open (line 166) is defensive — if the X11 poll fails to load, the panel
still becomes interactive at open and the cursor-position toggle is just lost.

**WS reconnect (line 39)** uses a 3-second fixed backoff. No jitter, no
cap, no exponential growth. Reasonable for a localhost socket — the
bridge is on `127.0.0.1` and will be back in <1s when restarted. Not
worth fixing.

**Panel bounds reporting (line 137):** Stamps `(0,0,0,0)` on `closeChat`
(line 176). Rust polls this and treats `w <= 0 || h <= 0` as "no panel
visible" (main.rs:2025). Good, idempotent.

**Hot path concern:** the effect at line 218 fires on **8 deps changing**
in the hook. `useVoiceClient` polls /status at 10 Hz and writes ALL of
those fields, so every state field that changes will trigger this effect.
`pushTrayState` is itself a `useCallback([])` and uses a ref to dedupe.
This is fine.

---

## 3. IPC contract — 14 commands, 12 invoke sites, 4 orphans (mostly OK)

**Rust `#[tauri::command]` declarations (line numbers in main.rs):**

| Line | Command | Caller (JS) | Status |
|---|---|---|---|
| 521 | `keys_read` | KeysSettings.jsx:22 | OK |
| 567 | `keys_set` | KeysSettings.jsx:36 | OK |
| 584 | `keys_clear` | KeysSettings.jsx:56 | OK |
| 617 | `keys_restart_agent` | KeysSettings.jsx:70 | OK |
| 631 | `set_click_through` | App.jsx:124, 266 | OK |
| 638 | `set_layer` | App.jsx:128 | OK |
| 648 | `set_chat_state` | App.jsx:132 | OK |
| 659 | `set_panel_rect` | App.jsx:139 | OK |
| 673 | `set_tray_state` | App.jsx:161 | OK |
| 855 | `set_speech_label` | App.jsx:249 | OK |
| 876 | `set_tts_label` | App.jsx:254 | OK |
| 915 | `set_provider_label` | App.jsx:244 | OK |
| 937 | `set_share_label` | App.jsx:259 | OK |
| 947 | `get_primary_monitor_info` | **none** | **Orphan** (P2) |

**`invoke_handler!` declarations (main.rs:2072):** all 14 of the
above are registered. `get_primary_monitor_info` is registered but
never called from JS — kill it or document why it stays. Probably
a holdover from the old monitor-snap logic that now lives in
`snap_to_cursor_monitor`.

**Tray events (Rust emit → JS listen):**

| Event | Rust emit | JS listen | Status |
|---|---|---|---|
| `tray-open-chat` | main.rs:1777 | App.jsx:186 | OK |
| `tray-close-chat` | main.rs:1768 | App.jsx:187 | OK |
| `tray-toggle-mute` | main.rs:1814 | App.jsx:189 (no-op) | OK by design |
| `tray-toggle-chat` | main.rs:1347 | App.jsx:191 | OK |
| `tray-toggle-screen-share` | main.rs:1830 | **none** | **Orphan emit (P1)** |

The `tray-toggle-screen-share` emit fires every time the user clicks
"Start / Stop Screen Share" but nothing in React reacts. The label
update path goes through `/status` poll → `speech.sharingScreen` →
`set_share_label`, so the menu DOES eventually flip. But the emit
is dead code, and the comment at main.rs:1830 doesn't acknowledge
this. Either delete the emit (preferred — `/status` poll catches it
within 100ms anyway) or add a JS toast for parity with `tray-toggle-mute`.

**Listener cleanup (App.jsx:195-200)** correctly awaits each
`listen()` promise then calls `f()` to unsubscribe.

---

## 4. Tray menu UX — Models submenu

**Current layout (main.rs:1694–1703):**
```
Models
├── Speech: <pretty-name>   (header, disabled)
├── Tool: <pretty-name>     (header, disabled)
├── TTS: <pretty-name>      (header, disabled)
├── ──────────
├── Speech model ▸          (submenu, 17 entries)
├── Tool model ▸            (submenu, 17 entries)
├── ──────────
└── TTS voice ▸             (submenu, 2 entries)
```

**Discoverability:** The three header lines tell the user the current
selection. The three submenus let them change it. The disabled-header
+ enabled-submenu split is a known pattern (Chrome's "Help → About
Chrome" version line follows it), so this is fine.

**✓-marker consistency:** **Inconsistent.** TTS marks the active
voice with `✓` in its submenu (main.rs:1627, 800–810). Speech and
Tool submenus do **not** — to see the active model in those submenus,
the user must close, look at the header, re-open. Three reasons to
add ✓ to both:

1. The label sync infra already exists for TTS (`TtsVoiceItems`
   state + `set_text` calls). Cloning it for speech/tool is ~40
   lines of additional state + a `SpeechModelItems` /
   `ToolModelItems` mutex.
2. The speech list has **17 items**; visual scanning for "which
   one's selected" is the slowest UX flow in the entire menu.
3. The optimistic-update path in `switch_speech_model` (829) and
   `switch_cli_model` (741) already rewrites the header — adding
   item-level `✓` updates is one more call.

**Recommendation:** mirror the TTS pattern across all three submenus.
File two new state holders, two new save-on-disk reads at startup,
two new `set_text` calls per switch. Cost is small, UX win is large.
(P1)

**Submenu sizing:** 17 entries in both Speech and Tool. With section
labels (DeepSeek / Groq / Anthropic / OpenAI) the menu would be far
easier to scan. Tauri 2's menu API supports `PredefinedMenuItem::separator`
(already used elsewhere in this file) and supports disabled header
items (already used for the dynamic Speech/Tool/TTS lines). Both
together would give:

```
Speech model ▸
├── DeepSeek
├── ──────
├── deepseek-chat
├── deepseek-v4-flash
├── deepseek-v4-pro
├── ──────
├── Groq
├── ──────
...
```

(P2 — quality-of-life, not a bug)

---

## 5. Indicator system — tray icon dynamic tinting

**The /status fields the React hook reads (useVoiceClient.js:110–119):**
- `s.connected` → tray state machine input
- `s.muted` (used inside `recording`)
- `s.listening` → `voiceActive`
- `s.speaking`
- `s.agent_present` → drives `booting`
- `s.tool_running` + `s.agent_thinking` → drives `processing`
- `s.silent_mode` → `silentMode`
- `s.sharing_screen`
- `s.cli_model`, `s.speech_model`, `s.tts_provider`

**The state machine in App.jsx:218–228** evaluates priorities in
this order: `offline → muted (voiceMuted || silentMode) → talking
→ listening → booting → thinking → idle`. Maps 1:1 to the 7
distinct tray-icon colours in `tray_image_for` (main.rs:141–149).

**Missing states / quality:**

- `processing` is a single bucket spanning two distinct conditions:
  `tool_running` (a function tool is in-flight, can be 5–60s) and
  `agent_thinking` (LLM token generation, typically 0.3–3s).
  Sub-second `agent_thinking` is the common case — a single amber
  flicker is fine. But `tool_running` can sit at amber for minutes
  on a long bash call, and the user can't tell from the tray whether
  JARVIS is "thinking" or "running a tool". Consider a second amber
  shade or a brightness pulse for `tool_running`. (P2)

- No state for "WS connected to bridge but voice-client down". The
  comment at App.jsx:217 explicitly chooses voice-client as the
  offline signal because "voice works fine without it [bridge]" —
  reasonable, but the chat panel WS status dot at ChatPanel.jsx:604
  is the only surface that signals bridge offline. A user with
  voice-client up + bridge down sees green tray + orange status
  dot inside chat. Acceptable; document it or surface bridge
  offline in the tray via a secondary indicator. (P2)

**Anti-recommendation:** Do **NOT** drive `audioLevel` (currently
hard-coded `0` per `useVoiceClient.js:68`) into the tray icon. Per
`.claude/rules/desktop-tauri.md` and CLAUDE.md, the voice reactor
sphere was intentionally removed; per-frame React state in the
voice UI is a regression vector.

---

## 6. Click-through overlay — hotspot poller

**Score: A-. Solid. One race + one log-spam concern.**

**Implementation** (main.rs:1978–2068): 30 Hz poll via X11 `XQueryPointer`,
compares cursor screen position to panel rect (translated from viewport
coords by adding window outer position), flips `set_ignore_cursor_events`
on transition.

**Strengths:**
- `last_inside` tracks transitions, so `set_ignore_cursor_events` is
  only called on edges, not on every tick. Bounded to ~tens of calls
  per second worst case (rapid mouse-in/mouse-out fluttering).
- `chat_open` check happens FIRST — if the panel is closed the poll
  short-circuits without an X11 call.
- Pure X11; no GTK/Tauri internal locks held across the 33ms sleep.
- 33ms tick is well below human reaction time and well above the X
  server's load tolerance.

**Concerns:**

- **Race at panel-open transition**: `chatOpen` flips true, React calls
  `setClickThrough(false)` (App.jsx:166), then the next tick of the
  poller reads `panel_rect_poll` — which is still `(0,0,0,0)` until
  ChatPanel mounts and calls `reportPanelBounds` (ChatPanel.jsx:232,
  fires via RAF after layout). For ~50–100 ms after the tray click,
  the poller sees `rect.w <= 0`, treats the panel as "no panel
  visible", and flips click-through ON (main.rs:2026). React's
  explicit `set_click_through(false)` already happened, so the user
  ends up with click-through ON at the moment they want to interact.

  Mitigation: the next valid `set_panel_rect` from RAF arrives in 16 ms
  on a 60Hz refresh, and `inside` then becomes true within one more
  poll tick (33 ms). Total stuck-in-click-through window: ~50 ms. The
  user's hand isn't on the panel yet anyway. So technically a race,
  practically invisible. Worth a comment though. (P2)

- **No log on X11 errors** in the hot loop (lines 2049, 2057). If
  `XQueryPointer` returns 0 (root window mismatch — happens on
  multi-screen XRandR reconfiguration) the loop silently continues.
  Add a counter + log every 100th failure so a broken X11 session
  surfaces in `/tmp/jarvis-desktop.log`. (P2)

- **Hardcoded `wx + rect.x`** assumes the Tauri window is a
  full-screen transparent overlay aligned to monitor origin. True for
  current setup. If the overlay ever supports per-monitor placement
  (e.g. a docked-display config that shifts coords), this math needs
  a recompute. Document the assumption above the inside-check. (P2)

**Panel-bounds tracking (ChatPanel.jsx:220–237):** RAF-guarded, fires
on mount + position + size commit. No drag-time updates — the drag
transform is composited via `style.transform`, so `getBoundingClientRect`
returns the *commit* rect, not the in-drag rect. On pointer-up the
state-set fires and the effect re-fires. Good.

---

## 7. Build pipeline — `npm run build` + `cargo build --release`

**Score: C+. Correct but undocumented as a single command.**

`package.json:11` defines `"build": "vite build"` only. `tauri.conf.json:8`
defines `"beforeBuildCommand": "npm run build"` so `cargo tauri build`
will run vite first — but the user uses `cargo build --release` directly
per `.claude/rules/desktop-tauri.md`.

**Issue:** `cargo build --release` does **NOT** invoke
`beforeBuildCommand`. That hook only runs under `cargo tauri build`.
So when the user runs `cargo build --release`, vite is NOT rebuilt;
cargo embeds whatever's currently in `dist/`.

This is exactly the failure described in `desktop-tauri.md` ("Skipping
the second step ships the previous binary's dist/"). The mitigation
is in the rules doc, not in the tooling.

**Recommendation:** Add a script:

```json
// package.json
"scripts": {
  "release": "vite build && cd src-tauri && cargo build --release"
}
```

So `npm run release` does both steps in one command. Wallpaper over
the rule, don't rely on the user to remember it. (P1)

**Incremental cargo rebuilds:** When only JS changes, `cargo` doesn't
rebuild any Rust crate; the embedded-dir step (`tauri::generate_context!`)
re-reads `frontendDist` (../dist) at compile time but if no Rust code
or `dist/` checksum changed, cargo skips the link step too. Vite's
`vite build` is ~7s (per the rule); the embed step adds ~3s. Total
incremental release rebuild for a JS-only change is ~10s, which is
fast. No issue here.

**CI absence:** There's no `.github/workflows` or any pre-commit
check for `desktop-tauri`. The Stop hook (regression-prevention.md
rule 5) would help — confirm `.claude/hooks/verify-before-done.sh`
runs `npm run build` for this subtree. (P2)

**Release binary size:** The `Cargo.toml` profile uses `opt-level = "z"`,
`lto = true`, `panic = "abort"`, `strip = true`. Aggressive size profile.
Good for a tray app. No issue.

**Log noise on startup:** main.rs prints 14 lines via `println!` /
`eprintln!` during normal startup (target monitor, WebKit settings,
shortcut register, chat-open / chat-close events). Logged to
`/tmp/jarvis-desktop.log` per the `[JARVIS]` prefix convention. Useful.
A debug-only `tracing::info!` would be cleaner long term but the cost
is real today and the benefit is small. (P3 — skip)

---

## 8. Window mgmt + global hotkey

**Score: A-. Sound.**

**Hotkey registration (main.rs:1489–1498):** Ctrl+Shift+Space via
`tauri-plugin-global-shortcut`. Registration happens inside `setup`,
after the window exists. The handler (1342–1349) routes back into the
existing `tray-toggle-chat` event. Single source of truth: React
decides open vs close.

The choice of Ctrl+**Shift**+Space (vs the description "Ctrl+Space" in
the prompt) is correct — Ctrl+Space conflicts with XFCE/IBus input-method
switcher per the comment at 1485–1487.

**Conflict handling:** If another app has Ctrl+Shift+Space registered,
`global_shortcut().register(sc)` returns Err and `eprintln!` logs it.
The tray still starts; the hotkey just doesn't work. Good failure
mode.

**Window lifecycle (main.rs:1417–1432):**
- Smallest-monitor-by-area heuristic for "laptop screen". Reasonable.
- Override via `JARVIS_DISPLAY=WIDTHxHEIGHT`. Good.
- `set_ignore_cursor_events(true)` + `show()` at startup. The window is
  always visible but always click-through unless the chat panel says
  otherwise.

**`xdotool_raise` (line 252):** Used in the tray "open chat" path
(line 1778) to bypass WM focus stealing prevention. Spawned, not awaited.
On systems without xdotool this no-ops. The function logs nothing —
add a one-time stderr warn if `xdotool` isn't found. (P3)

**Ctrl+H / Ctrl+Q keyboard shortcuts (App.jsx:265–266):** in-webview
shortcuts (only fire when the webview has focus). Ctrl+Q closes the
window via `window.close()` after disabling click-through. This is
distinct from "Quit JARVIS" (tray menu) which stops the systemd
services. Documented in your CLAUDE.md? Not currently. (P3)

---

## 9. Tauri 2.x security model

**Score: B. CSP is reasonably tight; capability surface is minimal.**

**CSP (tauri.conf.json:36):**

```
default-src 'self' ipc: https://tauri.localhost http://ipc.localhost;
connect-src 'self' ipc: https://tauri.localhost http://ipc.localhost
  http://127.0.0.1:8765 ws://127.0.0.1:8765
  http://127.0.0.1:8767 ws://127.0.0.1:8767;
script-src 'self' 'unsafe-inline';
style-src 'self' 'unsafe-inline';
img-src 'self' data: blob: asset:;
font-src 'self' data: https://fonts.gstatic.com
```

**Strengths:**
- `connect-src` enumerates exactly the four backends (bridge :8765,
  voice-client :8767), HTTP + WS each. No wildcard. Good.
- `default-src 'self'` blocks any third-party resource.
- `img-src` allows `data:` + `blob:` — needed for ChatPanel's inline
  SVG icons and any future avatar/screenshot blobs.

**Concerns:**

- **`script-src 'unsafe-inline'`** — needed for React's inline event
  handlers and `<style>` tags inside ChatPanel (line 630–634). Vite
  could be configured to output a hash-able bundle, but the cost is
  real and the threat model is local-only (no external content
  loaded). Acceptable. (P3)

- **`withGlobalTauri: true`** (tauri.conf.json:16) exposes
  `window.__TAURI__` to all webview code, which is fine because the
  webview only loads `'self'` per CSP. The bridge token injection
  at main.rs:1376–1383 also exposes `window.__JARVIS_LOCAL_API_TOKEN`,
  which is read by both `App.jsx:21` and `ChatPanel.jsx:244`. If the
  webview ever loaded third-party content (e.g. a links inside chat),
  token would leak. Currently safe but document the invariant.

- **Capability file (capabilities/default.json):** only `core:default`
  and `opener:default`. The `global-shortcut` plugin is wired via the
  plugin builder in Rust, not via a capability — Tauri 2 allows that
  for code-side registration but a capability would let the JS side
  also register shortcuts. Currently not needed. (P3)

- **`opener:default`** is the wide-open variant. The plugin uses
  `xdg-open` on Linux, which can open any URL. The only caller in
  main.rs is `open_in_browser` (line 1005) which receives a URL from
  `probe_jarvis_web` (returns a `127.0.0.1` URL with route appended),
  so the surface is controlled. Don't tighten until you need to.

**No `allowedJsApis` / `tauri.allowlist`** — Tauri 2 uses capabilities
instead. The current capability set is the right minimum.

---

## Severity-tagged actions

### P1 — fix soon

1. **Drop the orphan `tray-toggle-screen-share` emit.**
   `main.rs:1830` — remove the `w.emit("tray-toggle-screen-share", ())`
   call. The `/status` poll → `set_share_label` path covers the menu
   update; no JS listener exists for this event.

2. **Add `✓` markers to Speech and Tool submenus.**
   Mirror TTS pattern. Three changes:
   - Add `SpeechModelItems(Mutex<Vec<MenuItem<Wry>>>)` and
     `ToolModelItems(Mutex<Vec<MenuItem<Wry>>>)` state holders next
     to `TtsVoiceItems` (main.rs:47).
   - In setup, after building each submenu, stash the items.
   - In `switch_cli_model` (741) and `switch_speech_model` (829),
     loop the items and prefix the active one with `"✓  "`.
   - On startup, read `~/.jarvis/cli-model` and `~/.jarvis/voice-model`
     to pre-mark the saved choice (mirror lines 1620–1632).

3. **Add `npm run release` script.**
   `package.json:9` —
   ```
   "release": "vite build && cd src-tauri && cargo build --release"
   ```
   Single command, no rule-doc dependency.

### P2 — quality + drift prevention

4. **Split `main.rs` into modules.** Suggested layout:
   - `tray_icon.rs` — `tint_source`, `apply_sharing_ring`, `tray_image_for`,
     PNG decoding (~150 lines)
   - `keys.rs` — all `_keys_*`, `_repo_*` helpers + 4 keys IPC commands
     (~200 lines)
   - `web_probe.rs` — `probe_jarvis_web`, `try_spawn_web`, `handle_open_browser`,
     `show_web_not_running_window`, `find_bun_executable`, `find_project_root`
     (~400 lines)
   - `tray_menu.rs` — `MenuBuilder` chain + model switch helpers + IPC label
     setters (~600 lines)
   - `hotspot_poll.rs` — X11 polling thread (~80 lines)
   - `main.rs` — `tauri::Builder` setup only (~300 lines)

5. **Delete or use `get_primary_monitor_info`.** Currently registered
   in `invoke_handler!` (main.rs:2082) but never called from JS.

6. **Effect-deps cleanup in App.jsx:120.** Add `openChat` and `closeChat`
   to the deps array, or add an eslint-disable line with a comment
   explaining why.

7. **Comment the chat-open race in hotspot poller.** Add a comment near
   main.rs:2025 noting the ~50ms window where rect=(0,0,0,0) coexists
   with chat_open=true and how it self-corrects.

8. **Drop unused `silentMode` from hook public shape** OR surface it in
   a meaningful tray-menu state. Currently it collapses into `muted`
   at App.jsx:222, which is the only consumer.

9. **Log X11 errors every 100th tick.** main.rs:2049 silent failure on
   `XQueryPointer` returning 0.

10. **Section headers + separators in Speech / Tool submenus.** Group by
    provider (DeepSeek / Groq / Anthropic / OpenAI). 17 flat items each
    is hard to scan.

### P3 — nice to have

11. Add `tracing` for structured logs (replace `println!`/`eprintln!`).
12. Document the `Ctrl+H` / `Ctrl+Q` in-webview shortcuts in CLAUDE.md
    or a desktop-tauri user-facing readme.
13. Add an `xdotool` availability check on first call with a one-time
    stderr warn.

---

## Anti-recommendations (from .claude/rules + CLAUDE.md)

- **Do NOT re-add the voice reactor sphere** or any per-frame React state
  driven by audio. `audioLevel` stays `0` in `useVoiceClient.js:68`. The
  `lastTrayStateRef` at App.jsx:152 already dedupes tray-state pushes to
  prevent any equivalent per-frame IPC storm. Per `.claude/rules/desktop-tauri.md`
  ("Static visualization only") and `project_reactor_removed` memory.
- **Do NOT consolidate the tray icon update with `audioLevel`** — the IPC
  cost of `set_tray_state` at every audio frame would re-introduce the
  exact latency regression that motivated removing the sphere.
- **Do NOT skip `cargo build --release`** when shipping JS changes. The
  `npm run release` script proposed above bakes this in; don't replace
  it with `npm run build` only.
- **Do NOT delete `voice_session_within_60s`** despite its `#[allow(dead_code)]`
  status. CLAUDE.md operational rule explicitly references the 60-s
  session check; the function and its 5 unit tests are the durable
  guardrail for any future Pause/Restart tray entry.
- **Do NOT remove `src/cli/src/utils/claudeInChrome/`** even though
  it's unused by desktop-tauri — reserved for browser-extension work
  per CLAUDE.md.
- **Do NOT auto-propagate desktop-tauri changes to `src/os/desktop/`**
  (Misty Scone). They share patterns but are separate deployments.

---

## Closing assessment

The realtime-mode tray cleanup landed cleanly. The remaining drift:
one orphan event emit, one ✓-marker inconsistency between submenus,
and the file-size growth of `main.rs`. All P1 items are small (<50
lines of change combined). The hotspot poller and CSP are both solid
foundations and don't need touching.

Voice-agent contracts (`/status` field names, `tool_running` /
`agent_thinking` flags, `silent_mode`, `sharing_screen`) are correctly
plumbed through from useVoiceClient.js → App.jsx → main.rs without
duplication.

CHANGED: nothing — read-only review.
NOT CHANGED: all of `src/voice-agent/desktop-tauri/**` confirmed untouched.
VERIFY: no commands run; review is observational against the files
read above.
