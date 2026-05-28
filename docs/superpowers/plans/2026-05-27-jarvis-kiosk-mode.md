# JARVIS kiosk mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a cinematic dark fullscreen "kiosk" / owner-focus posture to the existing Tauri desktop overlay, with WM-level minimize of every other window and three converging triggers (tray, voice, CLI/HTTP). Spec: `docs/superpowers/specs/2026-05-27-jarvis-kiosk-mode-design.md`.

**Architecture:** Single source of truth for kiosk state in Rust (`Mutex<Option<KioskSnapshot>>`). React mirrors via Tauri event. Three triggers (tray menu, voice tool, CLI/HTTP) converge on one Rust command pair (`enter_kiosk` / `exit_kiosk`). Bridge route broadcasts a WS message that App.jsx forwards to a Tauri invoke. wmctrl shell-out is hidden behind a small adapter trait so pure logic is unit-testable.

**Tech Stack:** Rust + Tauri v2 (desktop), React/JSX (overlay UI), Bun/TypeScript (bridge), Python 3.13 (voice-agent tool), POSIX shell (CLI wrapper). All X11; no Wayland.

---

## File map

**New:**

- `src/desktop-tauri/src-tauri/src/kiosk.rs` — Rust module: `KioskSnapshot`, `WmctrlAdapter` trait, `RealWmctrl` + `MockWmctrl`, `enter_kiosk_impl` / `exit_kiosk_impl` pure functions, `enter_kiosk` / `exit_kiosk` / `kiosk_state` Tauri commands.
- `src/desktop-tauri/src/components/KioskClock.jsx` — clock component (HH:MM 24h, 1 s interval).
- `src/desktop-tauri/src/components/KioskVoiceWaveform.jsx` — CSS-keyframe 8-bar waveform, idle/active states.
- `src/desktop-tauri/src/components/KioskTranscript.jsx` — recent chat lines, scroll-pinned bottom.
- `src/desktop-tauri/src/components/KioskHUD.jsx` — composition + Escape handler + text input strip.
- `src/voice-agent/tools/kiosk_tool.py` — self-registering `toggle_kiosk` tool.
- `src/voice-agent/tests/test_kiosk_tool.py` — unit tests for the tool.
- `bin/jarvis-kiosk` — curl wrapper around `POST /api/kiosk`.

**Modified:**

- `src/desktop-tauri/src-tauri/src/main.rs` — `mod kiosk`, register 3 commands in `invoke_handler!`, add "Focus mode" `CheckMenuItem` to tray, wire its event + sync from `kiosk-changed`.
- `src/desktop-tauri/src/App.jsx` — `kioskMode` state, `listen('kiosk-changed', …)`, WS handler for `{type:'kiosk', state}`, conditional render swap.
- `src/cli/src/bridge/server.ts` — one new route `POST /api/kiosk` modelled on `/api/mute`.
- `src/voice-agent/prompts/supervisor.md` — one paragraph in the routing section.

---

## Task 1: Rust — kiosk module skeleton + WmctrlAdapter trait

**Files:**
- Create: `src/desktop-tauri/src-tauri/src/kiosk.rs`
- Test: same file (inline `#[cfg(test)]` module)

- [ ] **Step 1.1: Write the failing tests at the bottom of the new module**

Create the file with this exact content (the impl bodies in the trait + `MockWmctrl` will satisfy the test in step 1.2; placeholder real impl returns NotFound for now):

```rust
//! Kiosk / owner-focus posture for the JARVIS desktop overlay.
//!
//! Module is structured so the WM interaction is hidden behind a small
//! adapter trait; pure logic (state transitions, snapshot composition)
//! is unit-testable without a running X11 / wmctrl. Tauri command
//! wrappers at the bottom translate window-flag side effects onto the
//! real overlay window.

use std::sync::Mutex;
use once_cell::sync::Lazy;

#[derive(Debug, Clone, PartialEq)]
pub struct WindowInfo {
    pub id: String,
    pub wm_class: String,
    pub title: String,
}

#[derive(Debug, PartialEq)]
pub enum WmctrlError {
    NotFound,
    CommandFailed(String),
}

pub trait WmctrlAdapter: Send + Sync {
    fn list_visible_windows(&self) -> Result<Vec<WindowInfo>, WmctrlError>;
    fn minimize(&self, window_id: &str) -> Result<(), WmctrlError>;
    fn unminimize(&self, window_id: &str) -> Result<(), WmctrlError>;
}

#[derive(Debug, Clone, Default)]
pub struct KioskSnapshot {
    pub minimized_ids: Vec<String>,
    pub prev_always_on_top: bool,
    pub prev_click_through: bool,
}

pub static KIOSK_STATE: Lazy<Mutex<Option<KioskSnapshot>>> = Lazy::new(|| Mutex::new(None));

// Real adapter — placeholder for now; real `wmctrl` shell-out lands in Task 4.
pub struct RealWmctrl;

impl WmctrlAdapter for RealWmctrl {
    fn list_visible_windows(&self) -> Result<Vec<WindowInfo>, WmctrlError> {
        Err(WmctrlError::NotFound)
    }
    fn minimize(&self, _: &str) -> Result<(), WmctrlError> {
        Err(WmctrlError::NotFound)
    }
    fn unminimize(&self, _: &str) -> Result<(), WmctrlError> {
        Err(WmctrlError::NotFound)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::RefCell;

    pub struct MockWmctrl {
        pub windows: RefCell<Vec<WindowInfo>>,
        pub minimized: RefCell<Vec<String>>,
        pub unminimized: RefCell<Vec<String>>,
        pub list_fails_with: RefCell<Option<WmctrlError>>,
    }

    impl MockWmctrl {
        pub fn new(windows: Vec<WindowInfo>) -> Self {
            Self {
                windows: RefCell::new(windows),
                minimized: RefCell::new(vec![]),
                unminimized: RefCell::new(vec![]),
                list_fails_with: RefCell::new(None),
            }
        }
    }

    // Send + Sync: tests are single-threaded so RefCell is fine here.
    unsafe impl Send for MockWmctrl {}
    unsafe impl Sync for MockWmctrl {}

    impl WmctrlAdapter for MockWmctrl {
        fn list_visible_windows(&self) -> Result<Vec<WindowInfo>, WmctrlError> {
            if let Some(err) = self.list_fails_with.borrow().as_ref() {
                return Err(match err {
                    WmctrlError::NotFound => WmctrlError::NotFound,
                    WmctrlError::CommandFailed(s) => WmctrlError::CommandFailed(s.clone()),
                });
            }
            Ok(self.windows.borrow().clone())
        }
        fn minimize(&self, id: &str) -> Result<(), WmctrlError> {
            self.minimized.borrow_mut().push(id.to_string());
            Ok(())
        }
        fn unminimize(&self, id: &str) -> Result<(), WmctrlError> {
            self.unminimized.borrow_mut().push(id.to_string());
            Ok(())
        }
    }

    #[test]
    fn mock_records_minimize_calls() {
        let mock = MockWmctrl::new(vec![
            WindowInfo { id: "0x1".into(), wm_class: "Firefox".into(), title: "tabs".into() },
        ]);
        assert_eq!(mock.list_visible_windows().unwrap().len(), 1);
        mock.minimize("0x1").unwrap();
        assert_eq!(mock.minimized.borrow().as_slice(), &["0x1".to_string()]);
    }
}
```

- [ ] **Step 1.2: Add `once_cell` to Cargo.toml if not already present**

Run: `grep -E "^once_cell\b" src/desktop-tauri/src-tauri/Cargo.toml`. If empty:

```bash
cd src/desktop-tauri/src-tauri
cargo add once_cell
```

Expected: `once_cell = "1.x"` appears under `[dependencies]`.

- [ ] **Step 1.3: Run the new test**

```bash
cd src/desktop-tauri/src-tauri
cargo test --lib kiosk
```

Expected: 1 passed (`mock_records_minimize_calls`). If compilation fails because `kiosk` isn't yet exported from the crate, add `pub mod kiosk;` near the top of `src/main.rs` — this is the next task's edit, do it now.

- [ ] **Step 1.4: Commit**

```bash
git add src/desktop-tauri/src-tauri/Cargo.toml src/desktop-tauri/src-tauri/src/kiosk.rs src/desktop-tauri/src-tauri/src/main.rs
git commit -m "kiosk(rust): scaffold module + WmctrlAdapter trait"
```

---

## Task 2: Rust — pure `enter_kiosk_impl` with idempotency

**Files:**
- Modify: `src/desktop-tauri/src-tauri/src/kiosk.rs`

- [ ] **Step 2.1: Add `enter_kiosk_impl` failing tests**

In the `#[cfg(test)] mod tests` block at the bottom of `kiosk.rs`, append:

```rust
    fn make_windows() -> Vec<WindowInfo> {
        vec![
            WindowInfo { id: "0x100".into(), wm_class: "J.A.R.V.I.S.".into(), title: "J.A.R.V.I.S.".into() },
            WindowInfo { id: "0x200".into(), wm_class: "Firefox".into(),     title: "tabs".into() },
            WindowInfo { id: "0x300".into(), wm_class: "Code".into(),        title: "vscode".into() },
        ]
    }

    #[test]
    fn enter_minimizes_non_jarvis_windows() {
        let mock = MockWmctrl::new(make_windows());
        let mut state: Option<KioskSnapshot> = None;
        let entered = enter_kiosk_impl(&mock, &mut state, /*prev_aot=*/false, /*prev_ct=*/true).unwrap();
        assert!(entered, "fresh entry should report entered=true");
        assert!(state.is_some());
        let snap = state.as_ref().unwrap();
        let mut minimized = mock.minimized.borrow().clone();
        minimized.sort();
        assert_eq!(minimized, vec!["0x200".to_string(), "0x300".to_string()],
                   "should minimize Firefox + Code but not JARVIS");
        let mut snap_ids = snap.minimized_ids.clone();
        snap_ids.sort();
        assert_eq!(snap_ids, vec!["0x200".to_string(), "0x300".to_string()]);
        assert_eq!(snap.prev_always_on_top, false);
        assert_eq!(snap.prev_click_through, true);
    }

    #[test]
    fn enter_when_already_on_is_idempotent() {
        let mock = MockWmctrl::new(make_windows());
        let mut state: Option<KioskSnapshot> = Some(KioskSnapshot {
            minimized_ids: vec!["0x999".into()],
            prev_always_on_top: true,
            prev_click_through: false,
        });
        let entered = enter_kiosk_impl(&mock, &mut state, /*prev_aot=*/true, /*prev_ct=*/false).unwrap();
        assert!(!entered, "re-entry should report entered=false");
        assert!(mock.minimized.borrow().is_empty(), "should not minimize anything new");
        assert_eq!(state.as_ref().unwrap().minimized_ids, vec!["0x999".to_string()],
                   "snapshot stays untouched");
    }
```

- [ ] **Step 2.2: Run tests to verify they fail**

```bash
cd src/desktop-tauri/src-tauri
cargo test --lib kiosk 2>&1 | head -40
```

Expected: compile error — `enter_kiosk_impl` is not yet defined.

- [ ] **Step 2.3: Add `enter_kiosk_impl` to `kiosk.rs`**

Add this BEFORE the `#[cfg(test)]` block:

```rust
/// Returns Ok(true) for a fresh entry, Ok(false) for an idempotent re-entry.
/// The Tauri-window-flag changes (set_always_on_top etc.) are the caller's
/// responsibility — this function only owns the WM snapshot + minimize step.
pub fn enter_kiosk_impl<A: WmctrlAdapter>(
    adapter: &A,
    state: &mut Option<KioskSnapshot>,
    prev_always_on_top: bool,
    prev_click_through: bool,
) -> Result<bool, String> {
    if state.is_some() {
        return Ok(false);
    }
    let windows = match adapter.list_visible_windows() {
        Ok(ws) => ws,
        Err(WmctrlError::NotFound) => Vec::new(),
        Err(WmctrlError::CommandFailed(e)) => {
            eprintln!("[kiosk] wmctrl list failed: {} — continuing without WM ops", e);
            Vec::new()
        }
    };
    let mut minimized_ids = Vec::new();
    for w in windows {
        if is_jarvis_window(&w) {
            continue;
        }
        match adapter.minimize(&w.id) {
            Ok(()) => minimized_ids.push(w.id),
            Err(e) => eprintln!("[kiosk] minimize {} failed: {:?}", w.id, e),
        }
    }
    *state = Some(KioskSnapshot {
        minimized_ids,
        prev_always_on_top,
        prev_click_through,
    });
    Ok(true)
}

fn is_jarvis_window(w: &WindowInfo) -> bool {
    w.wm_class.contains("J.A.R.V.I.S.") || w.title.contains("J.A.R.V.I.S.")
        || w.wm_class.contains("jarvis") || w.wm_class.contains("Jarvis")
}
```

- [ ] **Step 2.4: Run tests — all three should pass**

```bash
cd src/desktop-tauri/src-tauri
cargo test --lib kiosk
```

Expected: 3 passed.

- [ ] **Step 2.5: Commit**

```bash
git add src/desktop-tauri/src-tauri/src/kiosk.rs
git commit -m "kiosk(rust): enter_kiosk_impl + idempotency"
```

---

## Task 3: Rust — `exit_kiosk_impl` with restore

**Files:**
- Modify: `src/desktop-tauri/src-tauri/src/kiosk.rs`

- [ ] **Step 3.1: Add `exit_kiosk_impl` failing tests**

Append to the test module:

```rust
    #[test]
    fn exit_restores_minimized_windows() {
        let mock = MockWmctrl::new(make_windows());
        let mut state: Option<KioskSnapshot> = Some(KioskSnapshot {
            minimized_ids: vec!["0x200".into(), "0x300".into()],
            prev_always_on_top: false,
            prev_click_through: true,
        });
        let exited = exit_kiosk_impl(&mock, &mut state).unwrap();
        assert!(exited, "fresh exit should report exited=true");
        assert!(state.is_none());
        let mut unmin = mock.unminimized.borrow().clone();
        unmin.sort();
        assert_eq!(unmin, vec!["0x200".to_string(), "0x300".to_string()]);
    }

    #[test]
    fn exit_when_already_off_is_idempotent() {
        let mock = MockWmctrl::new(make_windows());
        let mut state: Option<KioskSnapshot> = None;
        let exited = exit_kiosk_impl(&mock, &mut state).unwrap();
        assert!(!exited);
        assert!(mock.unminimized.borrow().is_empty());
    }
```

- [ ] **Step 3.2: Run tests — they fail because `exit_kiosk_impl` undefined**

```bash
cd src/desktop-tauri/src-tauri
cargo test --lib kiosk 2>&1 | head -20
```

Expected: compile error.

- [ ] **Step 3.3: Add `exit_kiosk_impl` to `kiosk.rs`**

Insert above the `#[cfg(test)]` block:

```rust
/// Returns Ok(true) when transitioning off→off (no-op idempotent re-exit
/// returns Ok(false)). The Tauri-window-flag restoration is the caller's
/// responsibility; this function only owns un-minimize and clearing
/// state.
pub fn exit_kiosk_impl<A: WmctrlAdapter>(
    adapter: &A,
    state: &mut Option<KioskSnapshot>,
) -> Result<bool, String> {
    let snap = match state.take() {
        Some(s) => s,
        None => return Ok(false),
    };
    for id in &snap.minimized_ids {
        if let Err(e) = adapter.unminimize(id) {
            eprintln!("[kiosk] unminimize {} failed: {:?} — continuing", id, e);
        }
    }
    Ok(true)
}
```

- [ ] **Step 3.4: Run tests — all five pass**

```bash
cd src/desktop-tauri/src-tauri
cargo test --lib kiosk
```

Expected: 5 passed.

- [ ] **Step 3.5: Commit**

```bash
git add src/desktop-tauri/src-tauri/src/kiosk.rs
git commit -m "kiosk(rust): exit_kiosk_impl + restore"
```

---

## Task 4: Rust — `RealWmctrl` shell-out implementation + graceful missing test

**Files:**
- Modify: `src/desktop-tauri/src-tauri/src/kiosk.rs`

- [ ] **Step 4.1: Add a graceful-missing test**

Append to the test module:

```rust
    #[test]
    fn enter_graceful_when_wmctrl_missing() {
        let mock = MockWmctrl::new(vec![]);
        *mock.list_fails_with.borrow_mut() = Some(WmctrlError::NotFound);
        let mut state: Option<KioskSnapshot> = None;
        let entered = enter_kiosk_impl(&mock, &mut state, false, true).unwrap();
        assert!(entered, "enter should succeed even when wmctrl is unavailable");
        let snap = state.as_ref().unwrap();
        assert!(snap.minimized_ids.is_empty(),
                "no windows enumerated → no minimized_ids");
        assert_eq!(snap.prev_always_on_top, false);
        assert_eq!(snap.prev_click_through, true);
    }
```

- [ ] **Step 4.2: Run tests to confirm it passes** (the impl from Task 2 already handles NotFound):

```bash
cd src/desktop-tauri/src-tauri
cargo test --lib kiosk
```

Expected: 6 passed.

- [ ] **Step 4.3: Implement `RealWmctrl` shell-out**

Replace the placeholder `impl WmctrlAdapter for RealWmctrl { ... }` with:

```rust
impl WmctrlAdapter for RealWmctrl {
    fn list_visible_windows(&self) -> Result<Vec<WindowInfo>, WmctrlError> {
        use std::process::Command;
        let out = Command::new("wmctrl").args(["-lx"]).output();
        let out = match out {
            Ok(o) => o,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Err(WmctrlError::NotFound),
            Err(e) => return Err(WmctrlError::CommandFailed(e.to_string())),
        };
        if !out.status.success() {
            return Err(WmctrlError::CommandFailed(
                String::from_utf8_lossy(&out.stderr).into_owned(),
            ));
        }
        let text = String::from_utf8_lossy(&out.stdout);
        // wmctrl -lx output: "<id> <desktop> <wm_class> <host> <title>"
        // Whitespace-delimited; title is the rest of the line.
        let mut windows = Vec::new();
        for line in text.lines() {
            let mut parts = line.splitn(5, char::is_whitespace).filter(|s| !s.is_empty());
            let id    = parts.next().unwrap_or("").to_string();
            let _dt   = parts.next();
            let wmc   = parts.next().unwrap_or("").to_string();
            let _host = parts.next();
            let title = parts.next().unwrap_or("").to_string();
            if id.is_empty() { continue; }
            // Filter pseudo-windows (panels, taskbar): they show up with
            // desktop=-1 — but -lx doesn't expose desktop reliably across
            // wmctrl versions, so the safer filter is "skip empty wmclass".
            if wmc.is_empty() { continue; }
            windows.push(WindowInfo { id, wm_class: wmc, title });
        }
        Ok(windows)
    }

    fn minimize(&self, id: &str) -> Result<(), WmctrlError> {
        use std::process::Command;
        let out = Command::new("wmctrl").args(["-ir", id, "-b", "add,hidden"]).output()
            .map_err(|e| WmctrlError::CommandFailed(e.to_string()))?;
        if !out.status.success() {
            return Err(WmctrlError::CommandFailed(
                String::from_utf8_lossy(&out.stderr).into_owned(),
            ));
        }
        Ok(())
    }

    fn unminimize(&self, id: &str) -> Result<(), WmctrlError> {
        use std::process::Command;
        let out = Command::new("wmctrl").args(["-ir", id, "-b", "remove,hidden"]).output()
            .map_err(|e| WmctrlError::CommandFailed(e.to_string()))?;
        if !out.status.success() {
            return Err(WmctrlError::CommandFailed(
                String::from_utf8_lossy(&out.stderr).into_owned(),
            ));
        }
        Ok(())
    }
}
```

- [ ] **Step 4.4: Confirm crate still builds**

```bash
cd src/desktop-tauri/src-tauri
cargo build 2>&1 | tail -20
```

Expected: builds cleanly (might emit warnings, no errors).

- [ ] **Step 4.5: Re-run kiosk tests**

```bash
cargo test --lib kiosk
```

Expected: 6 passed.

- [ ] **Step 4.6: Commit**

```bash
git add src/desktop-tauri/src-tauri/src/kiosk.rs
git commit -m "kiosk(rust): RealWmctrl shell-out + graceful when missing"
```

---

## Task 5: Rust — Tauri command wrappers + `kiosk_state` getter

**Files:**
- Modify: `src/desktop-tauri/src-tauri/src/kiosk.rs`

- [ ] **Step 5.1: Append the Tauri command wrappers at the bottom of `kiosk.rs`** (before the `#[cfg(test)]` block):

```rust
// ───────────────────────────────────────────────────────────────────────────
// Tauri command wrappers
//
// These are thin: they read current window flags, delegate to the pure
// `enter_kiosk_impl` / `exit_kiosk_impl`, then apply window-flag side
// effects + emit the `kiosk-changed` event. Side effects are skipped on
// idempotent (re-entry / re-exit) returns so we don't flap the overlay
// unnecessarily — but the event is always re-emitted so the tray
// check-state can re-sync if it drifted.
// ───────────────────────────────────────────────────────────────────────────

use tauri::{Manager, WebviewWindow, Emitter};

#[tauri::command]
pub fn enter_kiosk(window: WebviewWindow) -> Result<(), String> {
    let adapter = RealWmctrl;
    let prev_aot = window.is_always_on_top().unwrap_or(false);
    let prev_ct  = false; // best-effort; Tauri v2 has no getter for this
    let mut state = KIOSK_STATE.lock().map_err(|e| e.to_string())?;
    let entered = enter_kiosk_impl(&adapter, &mut state, prev_aot, prev_ct)?;
    if entered {
        let _ = window.set_always_on_top(true);
        let _ = window.set_ignore_cursor_events(false);
        let _ = window.set_focus();
    }
    let _ = window.emit("kiosk-changed", true);
    Ok(())
}

#[tauri::command]
pub fn exit_kiosk(window: WebviewWindow) -> Result<(), String> {
    let adapter = RealWmctrl;
    let mut state = KIOSK_STATE.lock().map_err(|e| e.to_string())?;
    // Capture prev flags before take(); needed even though we don't restore
    // focus, because we want to restore the overlay's pre-kiosk always_on_top.
    let prev_aot = state.as_ref().map(|s| s.prev_always_on_top).unwrap_or(false);
    let prev_ct  = state.as_ref().map(|s| s.prev_click_through).unwrap_or(true);
    let exited = exit_kiosk_impl(&adapter, &mut state)?;
    if exited {
        let _ = window.set_always_on_top(prev_aot);
        let _ = window.set_ignore_cursor_events(prev_ct);
    }
    let _ = window.emit("kiosk-changed", false);
    Ok(())
}

#[tauri::command]
pub fn toggle_kiosk(window: WebviewWindow) -> Result<(), String> {
    let on = KIOSK_STATE.lock().map_err(|e| e.to_string())?.is_some();
    if on { exit_kiosk(window) } else { enter_kiosk(window) }
}

#[tauri::command]
pub fn kiosk_state() -> Result<bool, String> {
    Ok(KIOSK_STATE.lock().map_err(|e| e.to_string())?.is_some())
}
```

- [ ] **Step 5.2: Confirm build**

```bash
cd src/desktop-tauri/src-tauri
cargo build 2>&1 | tail -10
```

Expected: builds (the commands aren't yet registered in main.rs but that's the next task; the compile happens because they're just regular Rust functions with the macro).

- [ ] **Step 5.3: Run tests again to confirm nothing regressed**

```bash
cargo test --lib kiosk
```

Expected: 6 passed.

- [ ] **Step 5.4: Commit**

```bash
git add src/desktop-tauri/src-tauri/src/kiosk.rs
git commit -m "kiosk(rust): Tauri command wrappers (enter/exit/toggle/state)"
```

---

## Task 6: Rust — wire kiosk into `main.rs` (invoke_handler + tray menu)

**Files:**
- Modify: `src/desktop-tauri/src-tauri/src/main.rs`

- [ ] **Step 6.1: Confirm `pub mod kiosk;` is at the top of main.rs**

(Added during Task 1 step 1.3.) If missing, add it now near the other module declarations.

Also extend the existing menu-imports line to bring in the check-item types:

```rust
// Before
use tauri::{
    menu::{MenuBuilder, MenuItem, MenuItemBuilder, PredefinedMenuItem, SubmenuBuilder},
    // ...
};
// After
use tauri::{
    menu::{
        CheckMenuItem, CheckMenuItemBuilder,
        MenuBuilder, MenuItem, MenuItemBuilder, PredefinedMenuItem, SubmenuBuilder,
    },
    // ...
};
```

The `Wry` type used in the new `FocusModeItem` state struct is already imported (see existing label structs).

- [ ] **Step 6.2: Locate the `invoke_handler` macro call in main.rs**

```bash
grep -n "invoke_handler\\!\\|generate_handler\\!" src/desktop-tauri/src-tauri/src/main.rs
```

You should find a `tauri::generate_handler![...]` call. Add the four kiosk commands to its argument list:

```rust
tauri::generate_handler![
    // ...existing entries...
    kiosk::enter_kiosk,
    kiosk::exit_kiosk,
    kiosk::toggle_kiosk,
    kiosk::kiosk_state,
]
```

- [ ] **Step 6.3: Add the "Focus mode" `CheckMenuItem` to the tray menu**

Locate the tray menu construction near line ~1500 (`let voice_chat_item = MenuItemBuilder::with_id(...)` etc.). After `share_item` and before `sep1`, add:

```rust
            // Owner focus mode (kiosk). Toggleable check item; mirrors the
            // KIOSK_STATE singleton on the Rust side, which is the source
            // of truth for whether kiosk is on. Updated reactively when
            // kiosk-changed fires (from voice or CLI triggers).
            let focus_mode_item = tauri::menu::CheckMenuItemBuilder::with_id(
                "focus_mode", "Focus mode (kiosk)"
            ).checked(false).build(app)?;
```

And add `focus_mode_item` to the `MenuBuilder` chain near the other items (right after `share_item`):

```rust
                .item(&share_item)
                .item(&focus_mode_item)   // NEW
                .item(&sep1)
```

- [ ] **Step 6.4: Wire the menu event in `on_menu_event`**

Locate the `match event.id().as_ref() { ... }` block (~line 1707). Add a new arm anywhere before the closing `}`:

```rust
                        "focus_mode" => {
                            // Tray click is a toggle intent. Read current state from Rust
                            // (single source of truth) and dispatch the inverse command.
                            // The kiosk-changed event will sync the check mark below.
                            let Some(w) = app.get_webview_window("main") else { return };
                            let on = crate::kiosk::KIOSK_STATE.lock().map(|s| s.is_some()).unwrap_or(false);
                            let result = if on {
                                crate::kiosk::exit_kiosk(w)
                            } else {
                                crate::kiosk::enter_kiosk(w)
                            };
                            if let Err(e) = result {
                                eprintln!("[JARVIS] focus_mode toggle failed: {}", e);
                            }
                        }
```

- [ ] **Step 6.5: Sync the CheckMenuItem from the `kiosk-changed` event**

Tauri v2's `CheckMenuItem` has `.set_checked(bool)`. The simplest plumbing is to clone `focus_mode_item` into an `Arc<Mutex<...>>` state container (similar to the existing `ProviderLabel` pattern) and update it from a `window.listen("kiosk-changed", ...)` handler in the same `.setup` closure.

Add a state struct near the other label structs (top of `main.rs`, ~line 33):

```rust
struct FocusModeItem(Mutex<Option<tauri::menu::CheckMenuItem<Wry>>>);
```

Register it during setup (in the same block where `app.manage(...)` calls happen):

```rust
            app.manage(FocusModeItem(Mutex::new(Some(focus_mode_item.clone()))));
```

After the tray is built, register a window listener:

```rust
            if let Some(w) = app.get_webview_window("main") {
                let app_handle = app.handle().clone();
                w.listen("kiosk-changed", move |event| {
                    let on: bool = event.payload().parse().unwrap_or(false);
                    if let Some(state) = app_handle.try_state::<FocusModeItem>() {
                        if let Ok(guard) = state.0.lock() {
                            if let Some(item) = guard.as_ref() {
                                let _ = item.set_checked(on);
                            }
                        }
                    }
                });
            }
```

(If `event.payload()` returns a string `"true"` / `"false"` in this Tauri version, the `parse::<bool>()` handles both shapes; if it returns JSON `true`/`false` directly, this still parses correctly.)

- [ ] **Step 6.6: Build and verify**

```bash
cd src/desktop-tauri
npm run build               # vite ~7s; catches syntax errors first
cd src-tauri
cargo build 2>&1 | tail -20
```

Expected: builds cleanly. Warnings OK.

- [ ] **Step 6.7: Commit**

```bash
git add src/desktop-tauri/src-tauri/src/main.rs
git commit -m "kiosk(desktop): register commands + Focus mode tray item"
```

---

## Task 7: React — `KioskClock` component

**Files:**
- Create: `src/desktop-tauri/src/components/KioskClock.jsx`

- [ ] **Step 7.1: Create the file**

```jsx
import React, { useState, useEffect } from 'react'

// HH:MM 24h. One setInterval per mounted instance; cleaned on unmount.
// Not per-frame React (1 s cadence is fine for a clock display).
export default function KioskClock() {
  const [now, setNow] = useState(() => new Date())
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(id)
  }, [])
  const hh = String(now.getHours()).padStart(2, '0')
  const mm = String(now.getMinutes()).padStart(2, '0')
  return <span className="kiosk-clock">{hh}:{mm}</span>
}
```

- [ ] **Step 7.2: Confirm the build picks it up**

```bash
cd src/desktop-tauri
npm run build 2>&1 | tail -10
```

Expected: build succeeds (the component isn't yet used; that comes in Task 10).

- [ ] **Step 7.3: Commit**

```bash
git add src/desktop-tauri/src/components/KioskClock.jsx
git commit -m "kiosk(desktop): KioskClock component"
```

---

## Task 8: React — `KioskVoiceWaveform` component (CSS-keyframe bars)

**Files:**
- Create: `src/desktop-tauri/src/components/KioskVoiceWaveform.jsx`

- [ ] **Step 8.1: Create the file**

```jsx
import React from 'react'

// 8 fixed bars, CSS-keyframe animation. Two states:
//   - active=true  → bars animate (scaleY oscillation, staggered)
//   - active=false → bars rest at a small static height
// Per the regression-prevention rule from CLAUDE.md: NO per-frame React
// state in voice UI. This component renders once when `active` changes.
//
// The animation is entirely in CSS keyframes; the `active` prop only
// flips a className.
const BARS = 8

export default function KioskVoiceWaveform({ active }) {
  return (
    <div className={`kiosk-wave ${active ? 'kiosk-wave-active' : ''}`}>
      {Array.from({ length: BARS }).map((_, i) => (
        <span
          key={i}
          className="kiosk-wave-bar"
          style={{ animationDelay: `${i * 80}ms` }}
        />
      ))}
      <style>{`
        .kiosk-wave {
          display: flex;
          align-items: center;
          justify-content: center;
          gap: 4px;
          height: 40px;
        }
        .kiosk-wave-bar {
          display: inline-block;
          width: 3px;
          height: 6px;
          background: rgba(255,255,255,0.55);
          border-radius: 2px;
          transform-origin: center;
        }
        .kiosk-wave-active .kiosk-wave-bar {
          animation: kiosk-wave-osc 700ms ease-in-out infinite;
        }
        @keyframes kiosk-wave-osc {
          0%, 100% { transform: scaleY(1);   opacity: 0.55; }
          50%      { transform: scaleY(5.5); opacity: 1; }
        }
      `}</style>
    </div>
  )
}
```

- [ ] **Step 8.2: Confirm build**

```bash
cd src/desktop-tauri
npm run build 2>&1 | tail -10
```

Expected: build succeeds.

- [ ] **Step 8.3: Commit**

```bash
git add src/desktop-tauri/src/components/KioskVoiceWaveform.jsx
git commit -m "kiosk(desktop): KioskVoiceWaveform (CSS-keyframe, no per-frame React)"
```

---

## Task 9: React — `KioskTranscript` component

**Files:**
- Create: `src/desktop-tauri/src/components/KioskTranscript.jsx`

- [ ] **Step 9.1: Create the file**

```jsx
import React, { useRef, useEffect } from 'react'

// Recent chat lines, scroll-pinned to bottom.
// Reads the same wsMessages stream that the existing ChatPanel consumes,
// filtering for chat_message (user typed) + chat_response (assistant) +
// any user_message echoes from the bridge.
const MAX_LINES = 12

function pickLines(wsMessages) {
  const out = []
  for (const m of wsMessages) {
    if (m.type === 'chat_response' && typeof m.text === 'string') {
      out.push({ who: 'jarvis', text: m.text })
    } else if (m.type === 'user_message' && typeof m.text === 'string') {
      out.push({ who: 'user', text: m.text })
    } else if (m.type === 'query' && typeof m.text === 'string') {
      out.push({ who: 'user', text: m.text })
    }
  }
  return out.slice(-MAX_LINES)
}

export default function KioskTranscript({ wsMessages }) {
  const ref = useRef(null)
  const lines = pickLines(wsMessages || [])

  useEffect(() => {
    if (ref.current) ref.current.scrollTop = ref.current.scrollHeight
  }, [lines.length])

  return (
    <div className="kiosk-transcript" ref={ref}>
      {lines.map((l, i) => (
        <div key={i} className={`kiosk-line kiosk-line-${l.who}`}>
          {l.who === 'user' ? '>' : ''} {l.text}
        </div>
      ))}
      <style>{`
        .kiosk-transcript {
          overflow-y: auto;
          padding: 24px 64px;
          color: rgba(255,255,255,0.85);
          font: 18px/1.55 ui-monospace, monospace;
        }
        .kiosk-line { margin: 8px 0; white-space: pre-wrap; word-wrap: break-word; }
        .kiosk-line-user   { color: rgba(255,255,255,0.95); }
        .kiosk-line-jarvis { color: rgba(255,255,255,0.70); }
      `}</style>
    </div>
  )
}
```

- [ ] **Step 9.2: Build**

```bash
cd src/desktop-tauri
npm run build 2>&1 | tail -10
```

Expected: succeeds.

- [ ] **Step 9.3: Commit**

```bash
git add src/desktop-tauri/src/components/KioskTranscript.jsx
git commit -m "kiosk(desktop): KioskTranscript component"
```

---

## Task 10: React — `KioskHUD` composition + Escape handler + text input

**Files:**
- Create: `src/desktop-tauri/src/components/KioskHUD.jsx`

- [ ] **Step 10.1: Create the file**

```jsx
import React, { useEffect, useRef, useState } from 'react'
import { invoke } from '@tauri-apps/api/core'
import KioskClock from './KioskClock.jsx'
import KioskVoiceWaveform from './KioskVoiceWaveform.jsx'
import KioskTranscript from './KioskTranscript.jsx'

// Status-dot palette mirrors the tray indicator. Source-of-truth lives
// in App.jsx's tray-state effect; here we re-derive from the same
// `speech.*` props so we don't duplicate logic but DON'T poll twice.
function dotColor({ connected, muted, silentMode, speaking, voiceActive, booting, processing }) {
  if (!connected)              return '#ff4d4f'  // offline = red
  if (muted || silentMode)     return '#888'     // muted   = gray
  if (speaking)                return '#3b82f6'  // talking = blue
  if (voiceActive)             return '#06b6d4'  // listening = cyan
  if (booting)                 return '#a855f7'  // booting = purple
  if (processing)              return '#f59e0b'  // thinking = amber
  return '#22c55e'                                // idle    = green
}

function stateLabel({ connected, muted, silentMode, speaking, voiceActive, booting, processing }) {
  if (!connected)              return 'offline'
  if (muted || silentMode)     return 'muted'
  if (speaking)                return 'speaking'
  if (voiceActive)             return 'listening'
  if (booting)                 return 'booting'
  if (processing)              return 'thinking'
  return 'idle'
}

export default function KioskHUD({ wsMessages, speech, voiceMuted, wsSendMessage }) {
  const [text, setText] = useState('')
  const rootRef = useRef(null)

  // Escape exits kiosk — belt-and-suspenders in case tray/voice/CLI are unavailable.
  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'Escape') {
        invoke('exit_kiosk').catch(console.error)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  // Focus the input on mount so typing-first is one keystroke away.
  useEffect(() => {
    rootRef.current?.querySelector('input')?.focus()
  }, [])

  const status = {
    connected: speech.connected,
    muted: voiceMuted,
    silentMode: speech.silentMode,
    speaking: speech.speaking,
    voiceActive: speech.voiceActive,
    booting: speech.booting,
    processing: speech.processing,
  }
  const sharing = !!speech.sharingScreen

  const onSubmit = (e) => {
    e.preventDefault()
    const t = text.trim()
    if (!t) return
    if (typeof wsSendMessage === 'function') {
      wsSendMessage({ type: 'query', text: t })
    }
    setText('')
  }

  return (
    <div className="kiosk-root" ref={rootRef}>
      <header className="kiosk-header">
        <KioskClock />
        <span className="kiosk-status" title={stateLabel(status)}>
          {sharing && <span className="kiosk-sharing-ring" />}
          <span className="kiosk-dot" style={{ background: dotColor(status) }} />
        </span>
      </header>

      <main className="kiosk-main">
        <KioskTranscript wsMessages={wsMessages} />
      </main>

      <footer className="kiosk-footer">
        <KioskVoiceWaveform active={status.speaking || status.voiceActive} />
        <form onSubmit={onSubmit} className="kiosk-input-wrap">
          <input
            className="kiosk-input"
            placeholder="type to JARVIS..."
            value={text}
            onChange={(e) => setText(e.target.value)}
            autoComplete="off"
            spellCheck={false}
          />
        </form>
        <div className="kiosk-footer-row">
          <span className="kiosk-state-label">{stateLabel(status)}</span>
          <span className="kiosk-brand">JARVIS</span>
        </div>
      </footer>

      <style>{`
        .kiosk-root {
          position: fixed; inset: 0;
          background: #000;
          color: #fff;
          z-index: 9999;
          display: grid;
          grid-template-rows: auto 1fr auto;
          font-family: ui-monospace, monospace;
        }
        .kiosk-header {
          display: flex; justify-content: space-between; align-items: center;
          padding: 18px 32px;
          color: rgba(255,255,255,0.65);
          font-size: 14px;
          letter-spacing: 0.1em;
        }
        .kiosk-status {
          position: relative;
          display: inline-flex; align-items: center; justify-content: center;
          width: 18px; height: 18px;
        }
        .kiosk-dot {
          width: 10px; height: 10px; border-radius: 50%;
          box-shadow: 0 0 8px currentColor;
        }
        .kiosk-sharing-ring {
          position: absolute; inset: 0; border-radius: 50%;
          border: 2px solid #d946ef;  /* magenta — matches tray ring */
        }
        .kiosk-main { display: flex; flex-direction: column; justify-content: flex-end; overflow: hidden; }
        .kiosk-footer { padding: 18px 32px 28px; }
        .kiosk-input-wrap { display: flex; justify-content: center; margin-top: 12px; }
        .kiosk-input {
          width: min(640px, 80%);
          background: transparent;
          border: none;
          border-bottom: 1px solid rgba(255,255,255,0.2);
          color: #fff;
          padding: 6px 4px;
          font: 16px ui-monospace, monospace;
          outline: none;
        }
        .kiosk-input::placeholder { color: rgba(255,255,255,0.3); }
        .kiosk-footer-row {
          display: flex; justify-content: space-between; align-items: baseline;
          margin-top: 18px;
          color: rgba(255,255,255,0.55);
          font-size: 13px;
          letter-spacing: 0.18em;
          text-transform: lowercase;
        }
        .kiosk-brand { color: rgba(255,255,255,0.85); letter-spacing: 0.3em; }
      `}</style>
    </div>
  )
}
```

- [ ] **Step 10.2: Build**

```bash
cd src/desktop-tauri
npm run build 2>&1 | tail -10
```

Expected: succeeds.

- [ ] **Step 10.3: Commit**

```bash
git add src/desktop-tauri/src/components/KioskHUD.jsx
git commit -m "kiosk(desktop): KioskHUD composition + Escape handler"
```

---

## Task 11: React — wire `App.jsx` (state + listener + WS + render swap)

**Files:**
- Modify: `src/desktop-tauri/src/App.jsx`

- [ ] **Step 11.1: Import KioskHUD**

At the top of App.jsx, with the other component imports:

```jsx
import KioskHUD     from './components/KioskHUD.jsx'
```

- [ ] **Step 11.2: Add `kioskMode` state**

After the existing `useState` calls in `App()` (near `const [chatOpen, setChatOpen] = useState(false)` etc.), add:

```jsx
  const [kioskMode, setKioskMode] = useState(false)
```

- [ ] **Step 11.3: Listen for `kiosk-changed` Tauri events**

Add this `useEffect` next to the existing tray-event listener `useEffect`:

```jsx
  // Rust is the source of truth for kiosk on/off. Mirror via event.
  useEffect(() => {
    const un = listen('kiosk-changed', (e) => {
      const next = e.payload === true || e.payload === 'true'
      setKioskMode(next)
    })
    return () => { un.then(f => f()) }
  }, [])
```

- [ ] **Step 11.4: Handle the `{type:'kiosk'}` WS message**

In the existing WS-message handler `useEffect` (around line 107, where it iterates `wsMessages`), add a branch inside the `for` loop:

```jsx
      if (m.type === 'kiosk') {
        const cmd = m.state === 'on'  ? 'enter_kiosk' :
                    m.state === 'off' ? 'exit_kiosk'  :
                                        'toggle_kiosk'
        invoke(cmd).catch(console.error)
      }
```

- [ ] **Step 11.5: Conditional render**

The current `App.jsx` returns JSX with `<ChatPanel/>` + `<VoiceChatPanel/>` etc. Find the top-level return (where the overlay JSX begins). Add a kiosk short-circuit just BEFORE the existing return:

```jsx
  if (kioskMode) {
    return (
      <KioskHUD
        wsMessages={wsMessages}
        speech={speech}
        voiceMuted={voiceMuted}
        wsSendMessage={wsSendMessage}
      />
    )
  }
```

The existing return follows unchanged.

- [ ] **Step 11.6: Build + dev-smoke**

```bash
cd src/desktop-tauri
npm run build 2>&1 | tail -10
```

Expected: succeeds.

- [ ] **Step 11.7: Commit**

```bash
git add src/desktop-tauri/src/App.jsx
git commit -m "kiosk(desktop): App.jsx wires kiosk state + listener + WS handler"
```

---

## Task 12: Bridge — `POST /api/kiosk` route

**Files:**
- Modify: `src/cli/src/bridge/server.ts`

- [ ] **Step 12.1: Add the route**

In `server.ts`, locate the `/api/mute` route (~line 483) and add this BLOCK just after it:

```typescript
    if (url.pathname === '/api/kiosk' && req.method === 'POST') {
      let body: any
      try { body = await req.json() } catch {
        return Response.json({ error: 'invalid JSON' }, { status: 400 })
      }
      const state = typeof body?.state === 'string' ? body.state : ''
      if (!['on', 'off', 'toggle'].includes(state)) {
        return Response.json({ error: 'state must be on|off|toggle' }, { status: 400 })
      }
      broadcast({ type: 'kiosk', state })
      return Response.json({ ok: true, state })
    }
```

- [ ] **Step 12.2: Update the endpoint inventory comment at the top of the file**

Add `'POST /api/kiosk → { ok: true, state }'` to the comment block listing endpoints (~line 14-19).

- [ ] **Step 12.3: Smoke-test the bridge route**

The bridge has no formal pytest harness wired by default; the simplest verification is to start the desktop (which starts the bridge as a subprocess) and `curl` the route. Defer the actual run to the manual-E2E task; for now, just confirm the file builds syntactically:

```bash
cd src/cli
bun check src/bridge/server.ts 2>&1 | tail -5 || true
```

(If `bun check` doesn't exist, `bun build src/bridge/server.ts --target=bun --outfile=/dev/null` works.)

- [ ] **Step 12.4: Commit**

```bash
git add src/cli/src/bridge/server.ts
git commit -m "kiosk(bridge): POST /api/kiosk broadcasts WS msg"
```

---

## Task 13: Voice-agent — `kiosk_tool.py` (TDD)

**Files:**
- Create: `src/voice-agent/tools/kiosk_tool.py`
- Create: `src/voice-agent/tests/test_kiosk_tool.py`

- [ ] **Step 13.1: Write the failing tests**

Create `src/voice-agent/tests/test_kiosk_tool.py`:

```python
"""Tests for the toggle_kiosk voice tool.

Verifies payload shape, bridge-down handling, and input validation.
HTTP layer is mocked — these tests don't reach a real bridge.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_toggle_kiosk_posts_state_on():
    from tools.kiosk_tool import _handle_toggle_kiosk
    mock_resp = AsyncMock()
    mock_resp.status_code = 200
    mock_resp.json = lambda: {"ok": True, "state": "on"}
    mock_resp.raise_for_status = lambda: None
    with patch("tools.kiosk_tool._post_to_bridge", new=AsyncMock(return_value=mock_resp)) as mock_post:
        result = await _handle_toggle_kiosk({"state": "on"})
        assert "on" in result.lower()
        mock_post.assert_awaited_once()
        sent = mock_post.await_args.args[0]
        assert sent == {"state": "on"}


@pytest.mark.asyncio
async def test_toggle_kiosk_default_toggle():
    from tools.kiosk_tool import _handle_toggle_kiosk
    mock_resp = AsyncMock()
    mock_resp.json = lambda: {"ok": True, "state": "toggle"}
    mock_resp.raise_for_status = lambda: None
    with patch("tools.kiosk_tool._post_to_bridge", new=AsyncMock(return_value=mock_resp)) as mock_post:
        await _handle_toggle_kiosk({})
        sent = mock_post.await_args.args[0]
        assert sent == {"state": "toggle"}


@pytest.mark.asyncio
async def test_toggle_kiosk_invalid_state_returns_error():
    from tools.kiosk_tool import _handle_toggle_kiosk
    result = await _handle_toggle_kiosk({"state": "bogus"})
    # tool_error returns a JSON-shaped error string.
    parsed = json.loads(result)
    assert "error" in parsed
    assert "state" in parsed["error"].lower()


@pytest.mark.asyncio
async def test_toggle_kiosk_bridge_down_returns_error():
    from tools.kiosk_tool import _handle_toggle_kiosk
    with patch("tools.kiosk_tool._post_to_bridge", new=AsyncMock(side_effect=ConnectionError("bridge down"))):
        result = await _handle_toggle_kiosk({"state": "on"})
        parsed = json.loads(result)
        assert "error" in parsed
        assert "bridge" in parsed["error"].lower() or "reach" in parsed["error"].lower()
```

- [ ] **Step 13.2: Run tests — they fail (tool doesn't exist)**

```bash
cd src/voice-agent
.venv/bin/python -m pytest tests/test_kiosk_tool.py -v 2>&1 | tail -20
```

Expected: collection/import error: `tools.kiosk_tool` not found.

- [ ] **Step 13.3: Implement `kiosk_tool.py`**

Create `src/voice-agent/tools/kiosk_tool.py`:

```python
"""toggle_kiosk — flip the desktop overlay into / out of cinematic
focus mode (kiosk).

The actual UI + WM minimize work happens in the Tauri desktop overlay;
this tool only POSTs the intent to the bridge, which broadcasts a WS
message that the overlay forwards to its Rust kiosk commands.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict

import httpx

from .registry import registry, tool_error

_BRIDGE_URL = os.environ.get("JARVIS_BRIDGE_URL", "http://127.0.0.1:8765")
_TIMEOUT_S = 5.0


def _auth_headers() -> Dict[str, str]:
    tok = os.environ.get("JARVIS_LOCAL_API_TOKEN", "").strip()
    if tok:
        return {"Authorization": f"Bearer {tok}"}
    return {}


async def _post_to_bridge(payload: Dict[str, Any]) -> httpx.Response:
    """Indirection: the unit tests patch this symbol to avoid real HTTP."""
    async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
        return await client.post(
            f"{_BRIDGE_URL}/api/kiosk",
            json=payload,
            headers=_auth_headers(),
        )


async def _handle_toggle_kiosk(args: Dict[str, Any]) -> str:
    state = (args or {}).get("state", "toggle")
    if state not in ("on", "off", "toggle"):
        return tool_error(f"toggle_kiosk: state must be on|off|toggle, got {state!r}")
    try:
        resp = await _post_to_bridge({"state": state})
        # raise_for_status may not exist on the AsyncMock test double,
        # but in prod httpx.Response always has it.
        if hasattr(resp, "raise_for_status"):
            resp.raise_for_status()
    except ConnectionError as e:
        return tool_error(f"toggle_kiosk: could not reach desktop bridge — {e}")
    except httpx.HTTPError as e:
        return tool_error(f"toggle_kiosk: bridge returned error — {e}")
    return f"kiosk {state}"


_SCHEMA = {
    "name": "toggle_kiosk",
    "description": (
        "Enter / exit the cinematic full-screen focus mode (kiosk) on the desktop "
        "overlay. In kiosk mode every other window is minimized and JARVIS fills "
        "the screen. Reversible. Use 'on' / 'off' for explicit intent; 'toggle' to "
        "flip whichever state is current."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "state": {
                "type": "string",
                "enum": ["on", "off", "toggle"],
                "description": "on = enter kiosk; off = exit kiosk; toggle = flip current state",
            },
        },
        "required": [],
    },
}

registry.register(
    name="toggle_kiosk",
    schema=_SCHEMA,
    handler=_handle_toggle_kiosk,
    toolset="desktop",
    check_fn=None,   # always available
    is_async=True,
)
```

- [ ] **Step 13.4: Run tests — all four pass**

```bash
cd src/voice-agent
.venv/bin/python -m pytest tests/test_kiosk_tool.py -v
```

Expected: 4 passed.

- [ ] **Step 13.5: Run the full voice-agent suite to confirm no regressions**

```bash
.venv/bin/python -m pytest tests/ 2>&1 | tail -10
```

Expected: all tests pass (count was ~800 before; new tests bring it to ~804). If anything is RED, do NOT proceed — debug first.

- [ ] **Step 13.6: Commit**

```bash
git add src/voice-agent/tools/kiosk_tool.py src/voice-agent/tests/test_kiosk_tool.py
git commit -m "kiosk(voice): toggle_kiosk tool + tests"
```

---

## Task 14: Voice-agent — supervisor.md routing paragraph

**Files:**
- Modify: `src/voice-agent/prompts/supervisor.md`

- [ ] **Step 14.1: Find the routing section**

```bash
grep -n "STAY-IN-SUPERVISOR\\|routing\\|Tools are for" src/voice-agent/prompts/supervisor.md | head -5
```

Open the file and locate the section near the per-tool routing notes (somewhere after the STAY-IN-SUPERVISOR rule).

- [ ] **Step 14.2: Insert this paragraph in the routing notes section**

```markdown
**Focus mode (kiosk).** Phrases like "go full screen", "enter focus mode", "kiosk mode", "tune everything else out", "show me JARVIS only" → call `toggle_kiosk('on')`. Phrases like "exit focus", "go back to normal", "show me the desktop" while focus mode is on → call `toggle_kiosk('off')`. Ambiguous between the two → `toggle_kiosk('toggle')`. This is a concrete nameable action — a tool call, not a chat reply. Do not restate what you just did; the visual change is the confirmation.
```

- [ ] **Step 14.3: Confirm the prompts test suite still passes (if one exists)**

```bash
cd src/voice-agent
.venv/bin/python -m pytest tests/ -k "prompt" 2>&1 | tail -10 || true
```

(If no prompt-specific tests, this is informational only.)

- [ ] **Step 14.4: Commit**

```bash
git add src/voice-agent/prompts/supervisor.md
git commit -m "kiosk(voice): supervisor.md routes focus phrases to toggle_kiosk"
```

---

## Task 15: CLI wrapper — `bin/jarvis-kiosk`

**Files:**
- Create: `bin/jarvis-kiosk`

- [ ] **Step 15.1: Create the script**

```bash
cat > bin/jarvis-kiosk <<'EOF'
#!/bin/sh
# Toggle / set the JARVIS desktop kiosk mode by POSTing to the bridge.
# Usage:  jarvis-kiosk           # toggle
#         jarvis-kiosk on        # force on
#         jarvis-kiosk off       # force off
set -e
STATE="${1:-toggle}"

case "$STATE" in
  on|off|toggle) ;;
  *) echo "usage: $(basename "$0") [on|off|toggle]" >&2; exit 2;;
esac

TOKEN=""
if [ -r "$HOME/.jarvis/local-api-token.env" ]; then
  TOKEN="$(sed -n 's/^JARVIS_LOCAL_API_TOKEN=//p' "$HOME/.jarvis/local-api-token.env" | head -1)"
fi

CURL_AUTH=""
if [ -n "$TOKEN" ]; then
  CURL_AUTH="-H Authorization: Bearer $TOKEN"
fi

exec curl -fsS -X POST "http://127.0.0.1:8765/api/kiosk" \
  -H "Content-Type: application/json" \
  $CURL_AUTH \
  -d "{\"state\":\"$STATE\"}"
EOF
chmod +x bin/jarvis-kiosk
```

- [ ] **Step 15.2: Smoke-test the script's argument validation (no live bridge needed)**

```bash
bin/jarvis-kiosk bogus 2>&1 | head -2
```

Expected: prints `usage: jarvis-kiosk [on|off|toggle]` and exits non-zero.

- [ ] **Step 15.3: Commit**

```bash
git add bin/jarvis-kiosk
git commit -m "kiosk(cli): bin/jarvis-kiosk curl wrapper"
```

---

## Task 16: Verification suite — Rust tests + voice-agent pytest + Tauri builds

**Files:** none (verification only)

- [ ] **Step 16.1: Rust kiosk tests**

```bash
cd src/desktop-tauri/src-tauri
cargo test --lib kiosk
```

Expected: 6 passed.

- [ ] **Step 16.2: Full voice-agent test suite**

```bash
cd src/voice-agent
.venv/bin/python -m pytest tests/ 2>&1 | tail -10
```

Expected: full suite passes (~804 tests).

- [ ] **Step 16.3: Tauri vite build**

```bash
cd src/desktop-tauri
npm run build 2>&1 | tail -10
```

Expected: vite finishes without errors.

- [ ] **Step 16.4: Tauri Rust release build** (per `.claude/rules/desktop-tauri.md`)

```bash
cd src/desktop-tauri/src-tauri
cargo build --release 2>&1 | tail -15
```

Expected: release binary builds (slow first time — ~5-10 min); subsequent incremental builds are fast.

- [ ] **Step 16.5: Confirm `wmctrl` is installed for the live test in Task 17**

```bash
which wmctrl || echo "MISSING: apt install wmctrl"
```

If missing: `sudo apt install -y wmctrl`. (Optional — the kiosk feature degrades gracefully without it, but for the manual E2E we want the full behavior.)

---

## Task 17: Manual E2E walkthrough

**Files:** none (manual checklist)

Per the spec's testing section. Do NOT commit anything from this task; it's verification.

- [ ] **Step 17.1: Start the desktop in dev mode**

```bash
cd src/desktop-tauri
npm run tauri dev
```

Wait for the overlay to appear. Open Firefox, a terminal, and VSCode (or any three other apps).

- [ ] **Step 17.2: Tray trigger**

Right-click the tray icon → "Focus mode (kiosk)". Verify:
- All other windows minimize.
- Overlay flips to dark fullscreen.
- Clock shows in top-left, status dot in top-right.
- Tray menu item shows a checkmark.

Right-click tray → "Focus mode (kiosk)" again. Verify:
- All other windows un-minimize and return to roughly their previous positions.
- Overlay reverts to transparent.
- Tray menu checkmark clears.

- [ ] **Step 17.3: Voice trigger**

Restart voice-agent if needed (`systemctl --user restart jarvis-voice-agent.service` — first check `turn_telemetry.db` for activity within 60 s, per CLAUDE.md). Then with mic live, say: **"Jarvis, focus mode."** Verify:
- JARVIS routes to `toggle_kiosk('on')` (visible in voice-agent log).
- Same visual transition as the tray trigger.
- Tray menu checkmark updates to checked.

Say: **"Jarvis, exit focus mode."** Verify:
- `toggle_kiosk('off')` called.
- Windows restore, overlay reverts, checkmark clears.

- [ ] **Step 17.4: CLI trigger**

```bash
bin/jarvis-kiosk on
```

Verify: same transition. Then:

```bash
bin/jarvis-kiosk off
```

Verify: reverts.

- [ ] **Step 17.5: Idempotency**

Trigger ON twice (e.g., tray then CLI). Verify no flicker, state stays on. Trigger OFF twice. Same.

- [ ] **Step 17.6: Escape exit**

Trigger ON. Click into the dark overlay. Press Escape. Verify: exits.

- [ ] **Step 17.7: wmctrl-missing graceful degradation**

```bash
sudo mv "$(which wmctrl)" /tmp/wmctrl.bak
bin/jarvis-kiosk on
```

Verify: overlay still flips to dark fullscreen; other windows do NOT minimize (no wmctrl); voice-agent log line `wmctrl list failed` or `wmctrl not found`. Exit kiosk — no crash. Then restore:

```bash
sudo mv /tmp/wmctrl.bak "$(which curl | xargs dirname)/wmctrl"
```

- [ ] **Step 17.8: Keys window is unaffected**

While the main overlay is in kiosk, right-click tray → "Manage API Keys…". Verify the keys window opens normally and does NOT enter kiosk.

- [ ] **Step 17.9: Document the run**

Capture the timing or any anomalies in the PR description. If everything passed: mark kiosk feature ready for merge.

---

## Implementation guardrails (sanity checks against the spec)

Before claiming done, re-read these against the spec at `docs/superpowers/specs/2026-05-27-jarvis-kiosk-mode-design.md`:

- [ ] `KIOSK_STATE` is the single Rust source of truth (Mutex<Option<KioskSnapshot>>). ✓ Task 1
- [ ] Three triggers (tray, voice via WS, CLI via HTTP) converge on `enter_kiosk` / `exit_kiosk`. ✓ Tasks 6 + 12 + 13 + 15
- [ ] React listens for `kiosk-changed`; never directly drives state. ✓ Task 11
- [ ] No per-frame React in voice UI (waveform is CSS keyframes). ✓ Task 8
- [ ] Frozen tray indicator is NOT touched (icon, ring, colors, poll rate). ✓ Task 6 adds an *item*, not indicator changes
- [ ] Sanitizers, confab detector, automod blocklist, soul.md, MEMORY.md, CLAUDE.md untouched.
- [ ] Voice tool path produces a structured `tool_result` (success) or `tool_error` (failure) — confab detector accepts both. ✓ Task 13
- [ ] Escape key in HUD invokes `exit_kiosk` (belt-and-suspenders). ✓ Task 10
- [ ] No on-screen exit button (per design — 4 exits is plenty). ✓ Task 10
- [ ] wmctrl-missing graceful degradation works. ✓ Tasks 4 + 17.7

---

## Done

Spec covered, tests green, builds clean, manual checklist walked. Open a PR off the current branch with a clear "kiosk: owner focus posture" title and the spec + plan paths in the body.
