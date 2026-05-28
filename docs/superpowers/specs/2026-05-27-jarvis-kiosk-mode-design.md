# JARVIS kiosk mode — owner focus posture

**Status:** spec, ready for implementation plan.
**Date:** 2026-05-27.
**Surface:** Tauri desktop overlay (existing) + supporting wiring in bridge + voice agent.

## Goal

Add a single-purpose "kiosk" / owner-focus posture to the existing Tauri desktop overlay. When on, every other X11 top-level window is minimized and the overlay flips to a cinematic dark full-screen HUD: clock, transcript, voice waveform, status indicator. Reversible. Triggered by tray menu, voice command, or CLI/HTTP. State lives in Rust (single source of truth); React mirrors via a Tauri event.

This is a posture, not a new app and not a new persistent state. It is for the owner (Ulrich) only — no auth, no sandboxing, no separate device.

## Why now

The overlay is designed to sit *over* the user's work as a transparent HUD. Sometimes the desired posture is the inverse — JARVIS *is* the screen, nothing else. Today the overlay can be opened via tray, but other apps stay visible and can steal focus. A small additive mode in the existing overlay closes that gap.

## Non-goals

- Public / guest / kid mode (sandboxed access for non-owners) — different problem, different design.
- Wall-display dashboard on a dedicated device.
- Demo loop / scripted showcase.
- Per-app focus presets (focus VSCode, focus Firefox, etc.) — user picked "JARVIS only".
- Notification or system-audio suppression — user picked "Tauri + WM" not "Tauri + WM + system mute".
- Multi-monitor handling — current overlay is single 1920x1080 at origin; same applies in kiosk.
- Persisting kiosk state across Tauri crashes.
- Auto-re-minimizing new windows opened during kiosk — always-on-top is enough.

## SCOPE (per `.claude/rules/regression-prevention.md` rule 1)

**In scope:**

- `src/desktop-tauri/src/components/KioskHUD.jsx` (new)
- `src/desktop-tauri/src/components/KioskVoiceWaveform.jsx` (new)
- `src/desktop-tauri/src/components/KioskClock.jsx` (new)
- `src/desktop-tauri/src/components/KioskTranscript.jsx` (new)
- `src/desktop-tauri/src/App.jsx` (modified — add `kioskMode` state, listener, conditional render)
- `src/desktop-tauri/src-tauri/src/kiosk.rs` (new — module with `enter_kiosk` / `exit_kiosk` / `kiosk_state` commands + snapshot struct + WM helpers)
- `src/desktop-tauri/src-tauri/src/main.rs` (modified — register kiosk commands, add "Focus mode" tray check item, wire event)
- `src/cli/src/bridge/server.ts` (modified — add `POST /api/kiosk` route; deliberate exception, see note below)
- `src/voice-agent/tools/kiosk_tool.py` (new — self-registering `toggle_kiosk` tool)
- `src/voice-agent/prompts/supervisor.md` (modified — one paragraph routing kiosk-related phrases to the tool)
- `bin/jarvis-kiosk` (new — thin curl wrapper)

**Out of scope:**

- Sanitizers (`src/voice-agent/sanitizers/`), confab detector, automod blocklist files, soul.md, `MEMORY.md`, `CLAUDE.md`, `.claude/rules/regression-prevention.md`. These are explicitly on the auto-mod HARD_BLOCKLIST and load-bearing; not touched.
- Frozen tray indicator (`tray_image_for`, `apply_sharing_ring`, the 7 voice-state colors, the poll rate, `icons/tray.png`) — per `.claude/rules/desktop-tauri.md`. Kiosk adds a *menu item* but does NOT touch the indicator icon.
- Voice agent pipeline (`pipeline/`, `resilience/`, `providers/`).
- `jarvis-voice-client.service` (LiveKit peer owning mic/speaker).
- Existing chat panel, voice chat panel, click-through hotspot poller.
- Web app (`src/web/`), Android (`src/android/`), CLI proper (only `src/cli/src/bridge/server.ts` route is touched, see below).

**`src/cli/` exception:** Per `CLAUDE.md`, `src/cli/` is normally off-limits when working on desktop/voice/web. The single bridge route added here (`POST /api/kiosk`) is the only path that connects voice + CLI to the desktop window. The exception is narrow (one route, ~15 LOC), additive (no changes to existing routes), and the project's bridge is the documented integration surface between the three components.

**Why "out" is out:** Sanitizers, confab detector, soul.md, frozen tray indicator — touching them would (a) violate explicit rules and (b) is unrelated to kiosk. The voice client and voice pipeline are unaffected because kiosk only re-arranges X11 windows and flips Tauri overlay flags; the voice path doesn't go through the overlay window. Web/Android/CLI surfaces aren't part of the user's scoped meaning of "kiosk mode" (chosen as desktop owner-focus posture).

## Architecture

```
                  TRIGGERS
   ┌────────────────────────────────────────┐
   │ tray menu      voice tool       CLI/HTTP
   │ "Focus mode"   toggle_kiosk     bin/jarvis-kiosk
   │      │              │                  │
   │      │              ▼                  ▼
   │      │         bridge :8765 ─────┐
   │      │         POST /api/kiosk   │
   │      │              │            │
   │      │              ▼            │
   │      │         WS broadcast ─────┘
   │      │         {type:"kiosk", state}
   │      ▼              ▼
   │  Tauri Rust: enter_kiosk / exit_kiosk
   └────────────────────────────────────────┘
                        │
                        ▼
   ┌────────────────────────────────────────┐
   │ Rust side (src-tauri/src/kiosk.rs)     │
   │  ├─ KIOSK_STATE: Mutex<Option<Snap>>   │
   │  ├─ wmctrl: snapshot + minimize others │
   │  ├─ window.set_always_on_top(true)     │
   │  ├─ window.set_ignore_cursor_events(false)
   │  ├─ window.set_focus()                 │
   │  └─ emit("kiosk-changed", on|off)      │
   └────────────────────────────────────────┘
                        │
                        ▼
   ┌────────────────────────────────────────┐
   │ React (App.jsx) listens kiosk-changed  │
   │   on → render <KioskHUD/>              │
   │       (hides ChatPanel + VoiceChatPanel)
   │   off → render existing tree           │
   └────────────────────────────────────────┘
```

Single source of truth: Rust. React mirrors. Three converging trigger paths land in one Rust command pair.

## Components

| File | Role | LOC budget |
|---|---|---|
| `KioskHUD.jsx` | Fullscreen dark React surface. Header (clock + status), main (transcript), footer (waveform + text input + label). Listens for Escape to exit. | ~120 |
| `KioskVoiceWaveform.jsx` | 8–10 fixed CSS-keyframe bars with staggered scale-Y animation. Two states (idle / active) driven by one boolean prop. Honors the "no per-frame React in voice UI" rule. | ~30 |
| `KioskClock.jsx` | `Date.now()` polled once per second via a single `setInterval`. HH:MM 24h. | ~15 |
| `KioskTranscript.jsx` | Filtered list of recent `chat_message` / `chat_response` items from `wsMessages`, scroll-pinned to bottom. | ~30 |
| `App.jsx` (mod) | New `kioskMode` state, listener for `kiosk-changed`, WS handler for `{type:"kiosk"}` → `invoke(enter|exit)`, conditional render swap. | +30 |
| `kiosk.rs` (new) | `KioskSnapshot` struct, `KIOSK_STATE` mutex, `enter_kiosk` / `exit_kiosk` / `kiosk_state` Tauri commands, `wmctrl` shell-out helpers, our-window-identification helper. | ~150 |
| `main.rs` (mod) | Register kiosk commands in `invoke_handler!`, add `CheckMenuItem` "Focus mode" to tray, wire its event, sync check state on `kiosk-changed`. | +40 |
| `server.ts` (mod) | One new route `POST /api/kiosk` behind `requireLocalAuth`; broadcasts WS msg to desktop clients. | +15 |
| `kiosk_tool.py` (new) | `toggle_kiosk(state: Literal["on","off","toggle"])` self-registering tool. HTTP POST to bridge. Structured tool error on failure. | ~50 |
| `supervisor.md` (mod) | One paragraph in the routing section mapping kiosk phrases to the tool. | +20 |
| `bin/jarvis-kiosk` (new) | Shell wrapper around `curl POST /api/kiosk`. Reads `~/.jarvis/local-api-token.env`. | ~20 |

Total: ~7 new files (~420 LOC), 4 modified files (~85 LOC added). Each unit single-purpose.

## Data flow

### Enter sequence

1. Trigger fires. Rust `enter_kiosk(window)` is invoked.
2. Lock `KIOSK_STATE`. If already `Some(_)`: emit `kiosk-changed=true`, return Ok (idempotent re-entry).
3. Snapshot:
   - Enumerate top-level visible X11 windows via `wmctrl -lG`.
   - Skip windows whose WM_CLASS matches `J.A.R.V.I.S.` or whose ID equals our own (acquired via Tauri window handle).
   - Skip windows already in `_NET_WM_STATE_HIDDEN` (already minimized — leave them as user left them).
   - Record current Tauri overlay flags: `always_on_top`, `ignore_cursor_events` (a.k.a. click-through). Focus is intentionally NOT snapshotted — on exit we let X11's normal focus management land on whichever window the user clicks into; trying to restore a specific prior focus is fragile and X11 wouldn't honor it reliably.
4. For each remaining (non-JARVIS, currently-visible) window ID, run `wmctrl -ir <id> -b add,hidden`. Collect the IDs that succeeded.
5. Flip Tauri overlay:
   - `window.set_always_on_top(true)`
   - `window.set_ignore_cursor_events(false)` (kiosk must be interactive)
   - `window.set_focus()`
6. Store `Some(KioskSnapshot { minimized_ids, prev_always_on_top, prev_click_through })`.
7. Emit `kiosk-changed=true`.
8. Update tray "Focus mode" check item to checked.
9. Return Ok.

### Exit sequence

1. Trigger fires. Rust `exit_kiosk(window)` is invoked.
2. Lock `KIOSK_STATE`. If `None`: emit `kiosk-changed=false`, return Ok (idempotent re-exit).
3. Take the snapshot.
4. Restore Tauri overlay flags from snapshot:
   - `window.set_always_on_top(prev_always_on_top)`
   - `window.set_ignore_cursor_events(prev_click_through)`
5. For each minimized ID: `wmctrl -ir <id> -b remove,hidden`. Log warnings on individual failures, continue.
6. Emit `kiosk-changed=false`.
7. Update tray "Focus mode" check item to unchecked.
8. Return Ok.

### React side after `kiosk-changed`

```javascript
// in App.jsx
useEffect(() => {
  const un = listen('kiosk-changed', e => setKioskMode(!!e.payload))
  return () => { un.then(f => f()) }
}, [])

// later
if (kioskMode) {
  return <KioskHUD wsMessages={wsMessages} speech={speech} voiceMuted={voiceMuted} />
}
// else: existing tree (ChatPanel, VoiceChatPanel, etc.)
```

Same `wsMessages`, `speech`, `voiceMuted` props pass through; no new WS subscription, no new state machine.

### Three subtleties

1. **Idempotent on both ends.** Re-entry while on, or re-exit while off, is a no-op that still re-emits the event so the tray check state can re-sync.
2. **`route=keys` short-circuits.** The keys settings window is the same Tauri bundle with `?route=keys`; `App.jsx` returns early for that URL and never attaches kiosk listeners. The tray menu's "Focus mode" item only operates on the main window (the keys window is opened separately and isn't sent the event).
3. **Voice path is untouched.** Mic + TTS run in `jarvis-voice-client.service` (LiveKit peer on `:8767`). Voice agent runs in `jarvis-voice-agent.service`. Kiosk only re-arranges X11 windows + flips Tauri overlay flags. Voice keeps working identically in kiosk.

## State machine

```
states: off, on
transitions:
  off → on     (atomic, see enter sequence above)
  on  → off    (atomic, see exit sequence above)
  on  → on     (idempotent no-op; re-emit event)
  off → off    (idempotent no-op; re-emit event)
```

Implementation: `Mutex<Option<KioskSnapshot>>` in Rust (`Some` = on, `None` = off). All transitions occur inside the locked region. Concurrent triggers serialize on the mutex; final state matches the last trigger.

## Window manager interaction

JARVIS is X11-only (no Wayland — verified in CLAUDE.md). `xdotool` is already used by the voice agent for keystrokes; `wmctrl` is the right tool for batch window-state operations and is widely packaged (`apt install wmctrl`). The kiosk module shells out via `std::process::Command` from Rust.

**Graceful degradation when `wmctrl` is missing:**

- Kiosk still enters: overlay flips, becomes always-on-top, fills the screen.
- Log line: `WARN: wmctrl not found; other windows not minimized`.
- No crash, no rollback, no error to the trigger.

The README for `src/desktop-tauri/` gets a small note about the dependency.

**Identifying our own window:** match WM_CLASS containing `J.A.R.V.I.S.` (set by Tauri from `productName` in `tauri.conf.json`). Fallback: match window title `J.A.R.V.I.S.`. Both are paranoid-checked before any minimization.

## Voice command wiring

**New tool** `src/voice-agent/tools/kiosk_tool.py`:

- Self-registers via `registry.register(...)` like every other tool (per CLAUDE.md's tool-discovery section: `_adapter.py::load_all_livekit_tools`).
- Name: `toggle_kiosk`.
- Argument: `state: Literal["on", "off", "toggle"]`, default `"toggle"`.
- Body: `httpx.AsyncClient` POSTs `{"state": state}` to `http://127.0.0.1:8765/api/kiosk` with the local API token header (`JARVIS_REQUIRE_LOCAL_AUTH=1` is the rule on this machine).
- Returns: `"kiosk on"` / `"kiosk off"` / `"kiosk toggled"` on success.
- Errors: bridge down / 401 / 502 → returns a structured `tool_error` per the existing helper. Confab detector accepts structured tool errors as evidence.

**Supervisor prompt addition** in `src/voice-agent/prompts/supervisor.md` (one paragraph in the routing section):

> **Focus mode (kiosk).** Phrases like "go full screen", "enter focus mode", "kiosk mode", "tune everything else out", "show me JARVIS only" → call `toggle_kiosk('on')`. Phrases like "exit focus", "go back to normal", "show me the desktop" while focus mode is on → call `toggle_kiosk('off')`. Ambiguous → `toggle_kiosk('toggle')`. This is a concrete nameable action — a tool call, not a chat reply (per the STAY-IN-SUPERVISOR rule, action commands get tools). The visual change is the confirmation; do not restate.

## CLI and HTTP wiring

**Bridge route** in `src/cli/src/bridge/server.ts`:

```typescript
app.post('/api/kiosk', requireLocalAuth, async (req, res) => {
  const state = req.body?.state
  if (!['on', 'off', 'toggle'].includes(state)) {
    return res.status(400).json({ error: 'state must be on|off|toggle' })
  }
  broadcast({ type: 'kiosk', state })
  res.json({ ok: true })
})
```

Uses the existing `requireLocalAuth` middleware (added 2026-05-16 per global review §P0-1). The `broadcast(msg)` helper at `src/cli/src/bridge/server.ts:148` is already used for `voice_muted`, `status`, etc. — same path, same shape.

**App.jsx WS handler** (added inside the existing `useEffect` that iterates `wsMessages`):

```javascript
if (m.type === 'kiosk') {
  const cmd = m.state === 'on'  ? 'enter_kiosk'  :
              m.state === 'off' ? 'exit_kiosk'   :
                                  'toggle_kiosk'
  invoke(cmd).catch(console.error)
}
```

**CLI wrapper** `bin/jarvis-kiosk`:

```sh
#!/bin/sh
set -e
STATE="${1:-toggle}"
TOKEN=$(sed -n 's/^JARVIS_LOCAL_API_TOKEN=//p' "$HOME/.jarvis/local-api-token.env" 2>/dev/null || true)
if [ -n "$TOKEN" ]; then
  AUTH_HEADER="Authorization: Bearer $TOKEN"
else
  AUTH_HEADER="X-No-Auth: 1"
fi
exec curl -fsS -X POST "http://127.0.0.1:8765/api/kiosk" \
  -H "Content-Type: application/json" \
  -H "$AUTH_HEADER" \
  -d "{\"state\":\"$STATE\"}"
```

## React layout

```
┌──────────────────────────────────────── 100vw ─┐
│ kiosk-header                                   │   ← clock + status dot
│   HH:MM                                  ●    │
│                                                │
│ kiosk-main                                     │   ← transcript
│   > how's the deploy looking?                  │
│   yes? all green.                              │
│                                                │
│ kiosk-footer                                   │   ← waveform + text input + label
│   ▁▃▅▇█▇▅▃▁  (CSS-keyframe bars)              │
│   [type to JARVIS...                       ]   │
│   listening                          JARVIS    │
└────────────────────────────────────────────────┘
```

CSS grid: `grid-template-rows: auto 1fr auto`. Background `#000`. Transcript text in muted off-white. Waveform bars in white. Status dot uses the same palette as the tray indicator (offline=red, muted=gray, talking=blue, listening=cyan, booting=purple, thinking=amber, idle=green) read from the same `speech.*` props — no duplicated state.

Escape key handler: `onKeyDown` at the root listens for `Escape` → `invoke('exit_kiosk')`. That's the in-HUD escape hatch.

No on-screen exit button. Four exit paths (tray, voice, CLI, Escape) is plenty redundancy; cinematic cleanliness wins.

## Error handling

| Failure | Behavior |
|---|---|
| `wmctrl` binary missing | Enter still succeeds; overlay flips; other windows are not minimized; logged WARN |
| `wmctrl` minimize fails on a specific window | Continue with the rest; log WARN per failure |
| `wmctrl` restore-on-exit fails for some windows | Finish exit anyway; log WARN; user can Super+D or restore manually |
| Tauri `set_always_on_top` fails | Log; return error from `enter_kiosk`; React stays in non-kiosk state (no event emitted) |
| Bridge `/api/kiosk` 401 | Tool returns structured error; supervisor reports "I couldn't reach the desktop" |
| Bridge down | Tool returns structured connection error; same as above |
| WS broadcast reaches no desktop client | Bridge returns 200; nothing happens. Voice tool returns "no listener" status |
| Tauri crashes mid-kiosk | User's windows stay minimized until manually restored; on next start kiosk is off (no persisted snapshot) |
| User opens a new app while in kiosk | App appears, but JARVIS overlay (always-on-top) stays in front |
| Re-entry while already on | Idempotent no-op + re-emit `kiosk-changed=true` (tray re-syncs) |
| Multi-monitor | Undefined behavior (out of scope); current overlay is 0,0 1920x1080 |
| Trigger from `route=keys` window | Short-circuited in App.jsx; listener never attached |

All failures are LOG + CONTINUE in Rust. The kiosk module never panics.

## Testing

**Rust unit tests** (`src/desktop-tauri/src-tauri/src/kiosk.rs`):

- `test_enter_idempotent` — call enter twice; assert state is `Some` after both; assert event fires twice.
- `test_exit_idempotent` — call exit when off; assert state stays `None`; event fires.
- `test_enter_exit_restores_flags` — mock wmctrl adapter; snapshot prev flags; enter; exit; assert flags restored.
- `test_wmctrl_missing_graceful` — mock adapter returns NotFound; enter still returns Ok; snapshot has empty minimized_ids.

To make this testable, the wmctrl shell-out is behind a small `WmctrlAdapter` trait with a `RealWmctrl` impl (used in production) and `MockWmctrl` (used in tests).

**Voice agent tests** (`src/voice-agent/tests/test_kiosk_tool.py`):

- `test_toggle_kiosk_payload` — patch `httpx.AsyncClient.post`; assert URL, JSON body, Authorization header.
- `test_toggle_kiosk_bridge_down` — patch raises `httpx.ConnectError`; tool returns structured error.
- `test_toggle_kiosk_invalid_state` — `state="bogus"` raises pydantic/Literal validation before HTTP call.

**Bridge tests** (`src/cli/src/bridge/tests/test_kiosk_route.ts` or wherever the bridge tests live):

- `POST /api/kiosk` with `{state:"on"}` and valid auth → 200 + WS broadcast.
- `POST /api/kiosk` with `{state:"bogus"}` → 400.
- `POST /api/kiosk` without auth (with `JARVIS_REQUIRE_LOCAL_AUTH=1`) → 401.

**Manual E2E** (recorded in PR description, run before merge per `.claude/rules/regression-prevention.md` rule 5):

1. Start desktop (`cd src/desktop-tauri && npm run tauri dev`). Open Firefox, a terminal, VSCode.
2. Click tray → "Focus mode". Verify: all other windows minimize, overlay flips dark, transcript visible, clock shown, status dot reads listening/idle.
3. Speak "Jarvis, exit focus mode". Verify: overlay reverts to transparent, tray check clears, windows restore.
4. `bin/jarvis-kiosk on`. Verify same as tray. `bin/jarvis-kiosk off` same as exit.
5. Re-trigger while already on (tray + voice + CLI in any combo) — no flicker; state stays correct.
6. In kiosk: press `Escape` in the HUD. Verify exit.
7. `sudo mv $(which wmctrl) /tmp/wmctrl.bak`. Enter kiosk → overlay still flips dark; log shows `wmctrl not found`. Exit kiosk — no crash. Restore wmctrl.
8. Open the keys window via tray ("Manage API Keys…"); confirm it does NOT enter kiosk and is unaffected by `bin/jarvis-kiosk on`.

Per `.claude/rules/regression-prevention.md` rule 5:
- Voice-agent edit → `cd src/voice-agent && .venv/bin/python -m pytest tests/` must pass.
- Desktop-tauri edit → `npm run build` (for syntax/import errors) and `cargo build --release` (to embed dist in binary) for release; `npm run tauri dev` for dev verification.
- Bridge edit → bridge tree's test command.

## Risks

1. **`wmctrl` portability.** Most Linux desktops have it, but not guaranteed. Mitigation: graceful degradation already in design. README install hint.
2. **WM state corruption on crash mid-transition.** Mitigation: idempotent + LOG/CONTINUE; user can recover manually with Super+D. Persisting snapshot to disk is YAGNI.
3. **Tray check state drift** if `kiosk-changed` events drop. Mitigation: Rust is source of truth; `kiosk_state` getter command is available so the tray can re-read on each menu open.
4. **Frozen tray indicator constraint** (per `.claude/rules/desktop-tauri.md`). Kiosk ADDS a new menu *item* but does NOT touch the tray indicator icon, colors, ring, or poll rate. Verified in SCOPE.
5. **STAY-IN-SUPERVISOR rule.** Voice path uses a real registered tool, not chat-only handling. Confab detector sees a structured tool result on success and structured tool error on failure. Both satisfy the lookback rule (confab strict mode unchanged).
6. **`src/cli/` exception risk.** Touching `src/cli/src/bridge/server.ts` violates the CLAUDE.md "ask before modifying" rule for CLI. Justified by the bridge being the documented integration surface, the change being additive (one new route), and the user having explicitly approved proceeding without further questions in this session. Surfaced here for audit.
7. **New windows during kiosk** appear over the desktop but UNDER the always-on-top overlay. UX is "the new app pokes through the bottom of JARVIS." Acceptable for a focus mode; documented.

## Out-of-scope follow-ups (deferred, not promised)

- Multi-monitor: pick which screen JARVIS occupies; tile or hide others.
- Notification suppression (`notify-send` / dunst pause), system-audio mute.
- Per-app focus presets ("kiosk + VSCode visible").
- Auto-enter on a schedule (e.g., daily 9–11am focus block) via the existing between-turn scheduler.
- Persisting kiosk state across Tauri restarts.
- Wayland support (whole project is X11-only today).

## Open questions

None. Every choice locked.
