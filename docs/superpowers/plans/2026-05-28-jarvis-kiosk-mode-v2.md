# JARVIS kiosk mode v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the v1 single-window/mode-flag kiosk with a two-window architecture where kiosk is a freshly-spawned Tauri WebviewWindow fullscreen on an explicitly-selected monitor. The kiosk window's content (iteration 1) is a black background with a centered SVG arc reactor.

**Architecture:** The main transparent HUD overlay is never touched during enter/exit. Kiosk lifecycle is owned by a new `WebviewWindow "kiosk"` born on enter, destroyed on exit, with monitor index supplied by the trigger (tray submenu / voice tool / CLI). Click-through races are architecturally impossible because the main window's flags are never flipped.

**Tech Stack:** Rust + Tauri v2 + GTK + webkit2gtk (desktop), React/JSX + SVG/CSS keyframes (kiosk content), Bun/TypeScript (bridge), Python 3.13 (voice-agent tool). X11 only.

**Spec:** `docs/superpowers/specs/2026-05-28-jarvis-kiosk-mode-v2-design.md`.

---

## File map

**New:**

- `src/desktop-tauri/src-tauri/src/kiosk.rs` — v2 module replacing v1 (lifecycle commands, snapshot state, WmctrlAdapter trait)
- `src/desktop-tauri/src-tauri/src/tray_kiosk.rs` — submenu construction + dispatch + per-monitor CheckMenuItem state retention
- `src/desktop-tauri/src/components/KioskArcReactor.jsx` — SVG arc reactor with state-driven CSS keyframes
- `src/desktop-tauri/src/components/KioskHUD.jsx` — root component for `?route=kiosk` (replaces v1's transcript HUD)

**Modified:**

- `src/desktop-tauri/src-tauri/src/main.rs` — revert v1 inline kiosk wiring; hook up `tray_kiosk` module; register v2 Tauri commands
- `src/desktop-tauri/src/main.jsx` — add `?route=kiosk` → `<KioskHUD/>` branch
- `src/desktop-tauri/src/App.jsx` — drop v1 kioskMode state + listener + render branch; keep slim `{type:'kiosk'}` WS handler that routes to Tauri commands
- `src/cli/src/bridge/server.ts` — update `/api/kiosk` to require monitor when state=on
- `src/voice-agent/tools/kiosk_tool.py` — schema requires `monitor: int` when `state="on"`
- `src/voice-agent/tests/test_kiosk_tool.py` — extend tests for new schema
- `src/voice-agent/prompts/supervisor.md` — one paragraph routing update ("ask which monitor")
- `bin/jarvis-kiosk` — rewrite to require monitor index

**Deleted (v1 orphans):**

- `src/desktop-tauri/src/components/KioskClock.jsx`
- `src/desktop-tauri/src/components/KioskVoiceWaveform.jsx`
- `src/desktop-tauri/src/components/KioskTranscript.jsx`

If those v1 component files are not on the current branch, the deletion steps are no-ops — proceed.

---

## Task 0 — Preflight

**Files:** none

- [ ] **Step 0.1: Verify environment**

Run:
```bash
cd /home/ulrich/Documents/Projects/jarvis
git branch --show-current
ls src/desktop-tauri/node_modules > /dev/null && echo "node_modules ok" || echo "MISSING: npm install"
ls src/voice-agent/.venv/bin/python > /dev/null && echo "venv ok" || echo "MISSING: voice-agent venv"
which wmctrl > /dev/null && echo "wmctrl ok" || echo "MISSING: apt install wmctrl"
which cargo > /dev/null && echo "cargo ok" || echo "MISSING: cargo"
which bun > /dev/null && echo "bun ok" || echo "MISSING: bun"
```

Expected: all "ok" lines. If any "MISSING" — install before proceeding.

- [ ] **Step 0.2: Note the branch and the v1 state**

```bash
git log --oneline -5 docs/superpowers/specs/2026-05-28-jarvis-kiosk-mode-v2-design.md 2>&1 | head -3
ls src/desktop-tauri/src-tauri/src/kiosk.rs 2>&1
ls src/desktop-tauri/src/components/Kiosk*.jsx 2>&1
```

Output identifies whether v1 files exist on this branch. The plan handles both cases (rewrite or write-fresh).

---

## Task 1 — Rust: kiosk module skeleton (v2)

**Files:**
- Create or rewrite: `src/desktop-tauri/src-tauri/src/kiosk.rs`
- Modify: `src/desktop-tauri/src-tauri/src/main.rs` (ensure `pub mod kiosk;` exists; usually already present from v1)

- [ ] **Step 1.1: Write the new kiosk.rs**

Use the Write tool to overwrite (or create) `src/desktop-tauri/src-tauri/src/kiosk.rs` with this exact content:

```rust
//! Kiosk mode v2 — owner-focus posture as a separate Tauri WebviewWindow.
//!
//! Architecture: kiosk is a freshly-spawned WebviewWindow ("kiosk" label),
//! fullscreen on an EXPLICITLY-selected monitor. The main overlay window's
//! state is never touched. Lifecycle is enter / exit — there is no
//! "toggle" command in v2 because every trigger must supply a monitor index.
//!
//! See docs/superpowers/specs/2026-05-28-jarvis-kiosk-mode-v2-design.md.

use std::sync::{LazyLock, Mutex};

use tauri::{
    AppHandle, Emitter, Manager, PhysicalPosition, PhysicalSize, WebviewUrl,
    WebviewWindowBuilder,
};

// ─── WmctrlAdapter trait + types (carried forward from v1) ─────────────────

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

pub struct RealWmctrl;

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
        let mut windows = Vec::new();
        for line in text.lines() {
            let mut parts = line.splitn(5, char::is_whitespace).filter(|s| !s.is_empty());
            let id = parts.next().unwrap_or("").to_string();
            let _dt = parts.next();
            let wmc = parts.next().unwrap_or("").to_string();
            let _host = parts.next();
            let title = parts.next().unwrap_or("").to_string();
            if id.is_empty() || wmc.is_empty() {
                continue;
            }
            windows.push(WindowInfo { id, wm_class: wmc, title });
        }
        Ok(windows)
    }

    fn minimize(&self, id: &str) -> Result<(), WmctrlError> {
        use std::process::Command;
        let out = Command::new("wmctrl")
            .args(["-ir", id, "-b", "add,hidden"])
            .output()
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
        let out = Command::new("wmctrl")
            .args(["-ir", id, "-b", "remove,hidden"])
            .output()
            .map_err(|e| WmctrlError::CommandFailed(e.to_string()))?;
        if !out.status.success() {
            return Err(WmctrlError::CommandFailed(
                String::from_utf8_lossy(&out.stderr).into_owned(),
            ));
        }
        Ok(())
    }
}

// ─── KioskSnapshot + state ─────────────────────────────────────────────────

#[derive(Debug, Clone, Default)]
pub struct KioskSnapshot {
    /// IDs of windows we minimized on enter — restored on exit.
    pub minimized_ids: Vec<String>,
    /// Which monitor index the kiosk is occupying. For the `kiosk-changed`
    /// payload, the tray check-state sync, and observability.
    pub monitor_idx: usize,
}

pub static KIOSK_STATE: LazyLock<Mutex<Option<KioskSnapshot>>> =
    LazyLock::new(|| Mutex::new(None));

fn is_jarvis_window(w: &WindowInfo) -> bool {
    w.wm_class.contains("J.A.R.V.I.S.")
        || w.wm_class.contains("jarvis")
        || w.wm_class.contains("Jarvis")
}

// ─── Pure logic (testable; no Tauri window APIs) ───────────────────────────

/// Snapshot non-JARVIS visible windows, minimize them via `adapter`,
/// and store the snapshot. Returns Ok(()) on success.
/// Errors: returns Err if state is already Some (explicit refusal).
pub fn enter_kiosk_impl<A: WmctrlAdapter>(
    adapter: &A,
    state: &mut Option<KioskSnapshot>,
    monitor_idx: usize,
) -> Result<(), String> {
    if state.is_some() {
        return Err(format!(
            "kiosk already active (monitor {}); exit first",
            state.as_ref().unwrap().monitor_idx
        ));
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
    *state = Some(KioskSnapshot { minimized_ids, monitor_idx });
    Ok(())
}

/// Restore previously-minimized windows and clear state.
/// Returns Ok(true) on a fresh exit, Ok(false) on idempotent re-exit.
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

// ─── Tauri commands ────────────────────────────────────────────────────────

#[tauri::command]
pub fn enter_kiosk_on_monitor(app: AppHandle, monitor_idx: usize) -> Result<(), String> {
    // 1. Enumerate monitors via the main window. Refuse if invalid.
    let main = app
        .get_webview_window("main")
        .ok_or_else(|| "main window not found".to_string())?;
    let monitors = main
        .available_monitors()
        .map_err(|e| format!("available_monitors failed: {}", e))?;
    if monitors.is_empty() {
        return Err("no monitors detected".to_string());
    }
    let monitor = monitors.get(monitor_idx).ok_or_else(|| {
        format!(
            "monitor index {} out of range; {} monitors detected",
            monitor_idx,
            monitors.len()
        )
    })?;

    // Position / size in physical pixels. Tauri docs say these ARE physical,
    // but issue #14630 has cases where workArea returns logical. Guard via
    // scale factor: if size looks suspiciously small for a known display
    // (less than 1024px wide) and scale > 1.5, multiply by scale_factor.
    let raw_pos = monitor.position();
    let raw_size = monitor.size();
    let scale = monitor.scale_factor();
    let (pos_x, pos_y, size_w, size_h) =
        if raw_size.width < 1024 && scale > 1.5 {
            eprintln!(
                "[kiosk] scale-factor guard tripped: raw size {}x{}, scale {} — multiplying",
                raw_size.width, raw_size.height, scale
            );
            (
                (raw_pos.x as f64 * scale) as i32,
                (raw_pos.y as f64 * scale) as i32,
                (raw_size.width as f64 * scale) as u32,
                (raw_size.height as f64 * scale) as u32,
            )
        } else {
            (raw_pos.x, raw_pos.y, raw_size.width, raw_size.height)
        };

    // 2. Minimize other windows + record state.
    let adapter = RealWmctrl;
    {
        let mut state = KIOSK_STATE.lock().map_err(|e| e.to_string())?;
        enter_kiosk_impl(&adapter, &mut state, monitor_idx)?;
    }

    // 3. Spawn the kiosk window.
    let result = WebviewWindowBuilder::new(&app, "kiosk", WebviewUrl::App("index.html?route=kiosk".into()))
        .decorations(false)
        .transparent(false)
        .always_on_top(true)
        .focused(true)
        .skip_taskbar(true)
        .resizable(false)
        .position(pos_x as f64, pos_y as f64)
        .inner_size(size_w as f64, size_h as f64)
        .build();

    let kiosk_window = match result {
        Ok(w) => w,
        Err(e) => {
            // Rollback: restore minimized windows + clear state.
            let mut state = KIOSK_STATE.lock().map_err(|e| e.to_string())?;
            let _ = exit_kiosk_impl(&adapter, &mut state);
            return Err(format!("WebviewWindowBuilder failed: {}", e));
        }
    };

    // Belt-and-suspenders: explicit always_on_top via wmctrl on the new window.
    // Some compositors (XFCE) lose track of the WindowBuilder hint.
    let _ = std::process::Command::new("wmctrl")
        .args(["-r", "kiosk", "-b", "add,above"])
        .output();

    // 4. on_window_event handler — if the user kills the window directly
    // (kill -9, or some WM-driven close), clean up state.
    let app_for_close = app.clone();
    kiosk_window.on_window_event(move |event| {
        if matches!(event, tauri::WindowEvent::CloseRequested { .. }) {
            // The window will close; ensure state is also cleared so a
            // subsequent enter doesn't think we're already active.
            let _ = exit_kiosk(app_for_close.clone());
        }
    });

    // 5. Emit kiosk-changed for the tray check-state sync.
    let _ = app.emit("kiosk-changed", serde_json::json!({ "on": true, "monitor": monitor_idx }));
    Ok(())
}

#[tauri::command]
pub fn exit_kiosk(app: AppHandle) -> Result<(), String> {
    // 1. Close the kiosk window (if it exists). Tauri handles teardown.
    if let Some(w) = app.get_webview_window("kiosk") {
        let _ = w.close();
    }

    // 2. Restore minimized windows.
    let adapter = RealWmctrl;
    {
        let mut state = KIOSK_STATE.lock().map_err(|e| e.to_string())?;
        let _ = exit_kiosk_impl(&adapter, &mut state);
    }

    // 3. Emit kiosk-changed off.
    let _ = app.emit("kiosk-changed", serde_json::json!({ "on": false }));
    Ok(())
}

#[tauri::command]
pub fn kiosk_state() -> Result<Option<usize>, String> {
    let state = KIOSK_STATE.lock().map_err(|e| e.to_string())?;
    Ok(state.as_ref().map(|s| s.monitor_idx))
}

// ─── Unit tests (pure logic only — Tauri commands not unit-testable) ───────

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

    fn windows_fixture() -> Vec<WindowInfo> {
        vec![
            WindowInfo {
                id: "0x100".into(),
                wm_class: "jarvis-desktop.Jarvis-desktop".into(),
                title: "J.A.R.V.I.S.".into(),
            },
            WindowInfo {
                id: "0x200".into(),
                wm_class: "Firefox".into(),
                title: "tabs".into(),
            },
            WindowInfo {
                id: "0x300".into(),
                wm_class: "Code".into(),
                title: "vscode".into(),
            },
        ]
    }

    #[test]
    fn enter_minimizes_non_jarvis_windows() {
        let mock = MockWmctrl::new(windows_fixture());
        let mut state: Option<KioskSnapshot> = None;
        let result = enter_kiosk_impl(&mock, &mut state, 0);
        assert!(result.is_ok());
        let snap = state.as_ref().unwrap();
        let mut minimized = mock.minimized.borrow().clone();
        minimized.sort();
        assert_eq!(minimized, vec!["0x200".to_string(), "0x300".to_string()]);
        assert_eq!(snap.monitor_idx, 0);
        let mut snap_ids = snap.minimized_ids.clone();
        snap_ids.sort();
        assert_eq!(snap_ids, vec!["0x200".to_string(), "0x300".to_string()]);
    }

    #[test]
    fn enter_when_already_on_returns_err() {
        let mock = MockWmctrl::new(windows_fixture());
        let mut state: Option<KioskSnapshot> = Some(KioskSnapshot {
            minimized_ids: vec!["0x999".into()],
            monitor_idx: 1,
        });
        let result = enter_kiosk_impl(&mock, &mut state, 0);
        assert!(result.is_err());
        let err = result.unwrap_err();
        assert!(err.contains("already active"), "got: {}", err);
        assert!(mock.minimized.borrow().is_empty(), "no new minimize on rejection");
        assert_eq!(state.as_ref().unwrap().monitor_idx, 1, "state untouched");
    }

    #[test]
    fn exit_restores_minimized_windows() {
        let mock = MockWmctrl::new(windows_fixture());
        let mut state: Option<KioskSnapshot> = Some(KioskSnapshot {
            minimized_ids: vec!["0x200".into(), "0x300".into()],
            monitor_idx: 0,
        });
        let exited = exit_kiosk_impl(&mock, &mut state).unwrap();
        assert!(exited);
        assert!(state.is_none());
        let mut unmin = mock.unminimized.borrow().clone();
        unmin.sort();
        assert_eq!(unmin, vec!["0x200".to_string(), "0x300".to_string()]);
    }

    #[test]
    fn exit_when_already_off_is_idempotent() {
        let mock = MockWmctrl::new(windows_fixture());
        let mut state: Option<KioskSnapshot> = None;
        let exited = exit_kiosk_impl(&mock, &mut state).unwrap();
        assert!(!exited);
        assert!(mock.unminimized.borrow().is_empty());
    }

    #[test]
    fn enter_graceful_when_wmctrl_missing() {
        let mock = MockWmctrl::new(vec![]);
        *mock.list_fails_with.borrow_mut() = Some(WmctrlError::NotFound);
        let mut state: Option<KioskSnapshot> = None;
        let result = enter_kiosk_impl(&mock, &mut state, 2);
        assert!(result.is_ok());
        let snap = state.as_ref().unwrap();
        assert!(snap.minimized_ids.is_empty());
        assert_eq!(snap.monitor_idx, 2);
    }
}
```

- [ ] **Step 1.2: Ensure `pub mod kiosk;` exists in main.rs**

```bash
grep -n "^pub mod kiosk" src/desktop-tauri/src-tauri/src/main.rs
```

If missing, add `pub mod kiosk;` near the top of main.rs (right after the `use` block). Most likely already present from v1.

- [ ] **Step 1.3: Run unit tests**

```bash
cd src/desktop-tauri/src-tauri
cargo test --bin jarvis-desktop kiosk 2>&1 | tail -15
```

Expected: 5 passed (`enter_minimizes_non_jarvis_windows`, `enter_when_already_on_returns_err`, `exit_restores_minimized_windows`, `exit_when_already_off_is_idempotent`, `enter_graceful_when_wmctrl_missing`).

- [ ] **Step 1.4: Confirm full crate compiles**

```bash
cargo build 2>&1 | tail -15
```

Expected: builds. Warnings about unused `tauri::PhysicalPosition` / `PhysicalSize` if you removed them are OK to fix; the imports are used by the live commands.

- [ ] **Step 1.5: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/desktop-tauri/src-tauri/src/kiosk.rs src/desktop-tauri/src-tauri/src/main.rs
git commit -m "kiosk(v2): rewrite kiosk.rs — two-window architecture, explicit monitor"
```

---

## Task 2 — Rust: new `tray_kiosk.rs` module

**Files:**
- Create: `src/desktop-tauri/src-tauri/src/tray_kiosk.rs`

- [ ] **Step 2.1: Write the module**

Create `src/desktop-tauri/src-tauri/src/tray_kiosk.rs` with this exact content:

```rust
//! Kiosk-related tray submenu construction + dispatch.
//!
//! v2 separates tray-construction concerns from main.rs to keep both
//! files focused. Per-monitor MenuItems are retained in managed
//! AppState so set_checked() works later (Tauri Linux GTK + dynamic
//! submenu items requires this pattern — issues #11462 / #12649).
//!
//! See docs/superpowers/specs/2026-05-28-jarvis-kiosk-mode-v2-design.md.

use std::sync::Mutex;

use tauri::{
    menu::{CheckMenuItem, CheckMenuItemBuilder, MenuItem, MenuItemBuilder, PredefinedMenuItem, Submenu, SubmenuBuilder},
    AppHandle, Listener, Manager, Wry,
};

/// Per-monitor CheckMenuItems retained in managed state so we can flip
/// their checked state from the kiosk-changed event listener.
pub struct KioskMonitorItems(pub Mutex<Vec<CheckMenuItem<Wry>>>);

/// Build the "Focus mode (kiosk) ▸" submenu. Returns the submenu (to be
/// attached to the main tray MenuBuilder via .item(&submenu)).
///
/// IDs emitted:
///   - "kiosk_mon_<idx>" — per-monitor CheckMenuItems (one per detected screen)
///   - "kiosk_off"       — explicit exit MenuItem
///
/// Monitor enumeration runs against the main window — if it isn't yet
/// mapped (rare in setup), the submenu shows just "Exit focus mode" and
/// the user can refresh by reopening JARVIS.
pub fn build_kiosk_submenu(app: &AppHandle) -> tauri::Result<Submenu<Wry>> {
    let monitors: Vec<_> = app
        .get_webview_window("main")
        .and_then(|w| w.available_monitors().ok())
        .unwrap_or_default();

    let mut mon_items: Vec<CheckMenuItem<Wry>> = Vec::new();
    for (i, m) in monitors.iter().enumerate() {
        let pos = m.position();
        let size = m.size();
        let mon_name = m.name().map(|s| s.as_str()).unwrap_or("");
        let label = if mon_name.is_empty() {
            format!("Monitor {}: {}x{} at {},{}", i, size.width, size.height, pos.x, pos.y)
        } else {
            format!("{}: {}x{} at {},{}", mon_name, size.width, size.height, pos.x, pos.y)
        };
        let id = format!("kiosk_mon_{}", i);
        let item = CheckMenuItemBuilder::with_id(&id, &label).checked(false).build(app)?;
        mon_items.push(item);
    }

    let exit_item: MenuItem<Wry> = MenuItemBuilder::with_id("kiosk_off", "Exit focus mode").build(app)?;
    let sep = PredefinedMenuItem::separator(app)?;

    let mut sb = SubmenuBuilder::new(app, "Focus mode (kiosk) ▸");
    for it in &mon_items {
        sb = sb.item(it);
    }
    if !mon_items.is_empty() {
        sb = sb.item(&sep);
    }
    sb = sb.item(&exit_item);
    let submenu = sb.build()?;

    // Stash mon_items in managed AppState so set_checked() works from the
    // kiosk-changed listener.
    app.manage(KioskMonitorItems(Mutex::new(mon_items)));

    Ok(submenu)
}

/// Dispatch a tray menu click. Returns true if this id was a kiosk
/// event we handled; false otherwise.
pub fn handle_kiosk_menu_event(app: &AppHandle, id: &str) -> bool {
    if id == "kiosk_off" {
        if let Err(e) = crate::kiosk::exit_kiosk(app.clone()) {
            eprintln!("[JARVIS] kiosk_off failed: {}", e);
        }
        return true;
    }
    if let Some(idx_str) = id.strip_prefix("kiosk_mon_") {
        let Ok(idx) = idx_str.parse::<usize>() else {
            eprintln!("[JARVIS] kiosk_mon_<bad-idx>: {}", id);
            return true;
        };
        // Toggle: if currently on, exit; else enter on idx.
        let on = crate::kiosk::KIOSK_STATE
            .lock()
            .map(|s| s.is_some())
            .unwrap_or(false);
        if on {
            if let Err(e) = crate::kiosk::exit_kiosk(app.clone()) {
                eprintln!("[JARVIS] kiosk_mon toggle-off failed: {}", e);
            }
        } else if let Err(e) = crate::kiosk::enter_kiosk_on_monitor(app.clone(), idx) {
            eprintln!("[JARVIS] kiosk_mon_{} enter failed: {}", idx, e);
        }
        return true;
    }
    false
}

/// Install the kiosk-changed event listener that syncs CheckMenuItem
/// checked states. Call after the main window is created (in setup()).
pub fn install_kiosk_changed_listener(app: &AppHandle) {
    let Some(main_win) = app.get_webview_window("main") else { return };
    let app_for_listener = app.clone();
    main_win.listen("kiosk-changed", move |event| {
        // Payload is { on: bool, monitor?: usize }.
        let on_idx: Option<usize> = serde_json::from_str::<serde_json::Value>(event.payload())
            .ok()
            .and_then(|v| {
                let on = v.get("on")?.as_bool()?;
                if !on {
                    return Some(None);
                }
                let m = v.get("monitor")?.as_u64()? as usize;
                Some(Some(m))
            })
            .unwrap_or(None);

        if let Some(state) = app_for_listener.try_state::<KioskMonitorItems>() {
            if let Ok(items) = state.0.lock() {
                for (i, item) in items.iter().enumerate() {
                    let want = on_idx.map(|on| on == i).unwrap_or(false);
                    let _ = item.set_checked(want);
                }
            }
        }
    });
}
```

- [ ] **Step 2.2: Add the module to main.rs**

In `src/desktop-tauri/src-tauri/src/main.rs`, near the top alongside other `pub mod` declarations, add:

```rust
pub mod tray_kiosk;
```

If `pub mod kiosk;` already exists, put `pub mod tray_kiosk;` directly after it.

- [ ] **Step 2.3: Build to confirm tray_kiosk compiles**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/desktop-tauri/src-tauri
cargo build 2>&1 | tail -10
```

Expected: builds.

- [ ] **Step 2.4: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/desktop-tauri/src-tauri/src/tray_kiosk.rs src/desktop-tauri/src-tauri/src/main.rs
git commit -m "kiosk(v2): new tray_kiosk module — submenu construction + dispatch + state sync"
```

---

## Task 3 — Wire kiosk v2 into `main.rs`

**Files:**
- Modify: `src/desktop-tauri/src-tauri/src/main.rs`

This task replaces the v1 inline kiosk wiring with the v2 module hookup.

- [ ] **Step 3.1: Find existing v1 kiosk wiring**

```bash
cd /home/ulrich/Documents/Projects/jarvis
grep -n "focus_mode\|kiosk\|FocusModeItem" src/desktop-tauri/src-tauri/src/main.rs | head -30
```

Note the line numbers. Expect to find:
- A `struct FocusModeItem(Mutex<Option<CheckMenuItem<Wry>>>)` near other label structs (~line 60)
- `let focus_mode_item = CheckMenuItemBuilder::with_id("focus_mode", ...).build(app)?;`
- An inline `let focus_mode_submenu = SubmenuBuilder::new(...)...build()?;` block (~line 1528-1568 in v1)
- `.item(&focus_mode_submenu)` in the main MenuBuilder chain
- `app.manage(FocusModeItem(...))`
- `kiosk_win.listen("kiosk-changed", ...)` listener
- An `on_menu_event` `match` arm `"focus_mode" => ...`, `"focus_mode_off" => ...`, `other if other.starts_with("focus_mode_mon_") => ...`
- `kiosk::enter_kiosk, kiosk::exit_kiosk, kiosk::toggle_kiosk, kiosk::kiosk_state, kiosk::enter_kiosk_on_monitor` in `tauri::generate_handler!`

- [ ] **Step 3.2: Remove the `FocusModeItem` struct and managed state**

Delete the struct definition (it's been moved into tray_kiosk.rs as `KioskMonitorItems`). Delete the `.manage(FocusModeItem(...))` call.

```bash
# Search for FocusModeItem definitions/uses and remove them
grep -n "FocusModeItem" src/desktop-tauri/src-tauri/src/main.rs
```

Remove every line matching `FocusModeItem`. The places are:
- The `struct FocusModeItem(Mutex<Option<CheckMenuItem<Wry>>>);` line (top of file)
- The `app.manage(FocusModeItem(Mutex::new(None)))` call (inside setup)
- The `let fm: State<FocusModeItem> = app.state(); *fm.0.lock().unwrap() = Some(focus_mode_item.clone());` stash block (after MenuBuilder.build())

- [ ] **Step 3.3: Replace inline submenu construction with `tray_kiosk::build_kiosk_submenu`**

Find this block (around line 1528-1571 in v1):

```rust
let focus_mode_item = CheckMenuItemBuilder::with_id(
    "focus_mode", "Toggle (auto / cursor screen)"
).checked(false).build(app)?;

let monitors: Vec<_> = app.get_webview_window("main")
    .and_then(|w| w.available_monitors().ok())
    .unwrap_or_default();
let mut focus_mode_mon_items: Vec<MenuItem<Wry>> = Vec::new();
// ... (loop building per-monitor items) ...
let focus_mode_off_item = MenuItemBuilder::with_id(
    "focus_mode_off", "Exit focus mode"
).build(app)?;
let focus_mode_sep = PredefinedMenuItem::separator(app)?;
let mut fm_builder = SubmenuBuilder::new(app, "Focus mode (kiosk) ▸")
    .item(&focus_mode_item)
    .item(&focus_mode_sep);
for it in &focus_mode_mon_items {
    fm_builder = fm_builder.item(it);
}
let focus_mode_submenu = fm_builder
    .item(&focus_mode_sep)
    .item(&focus_mode_off_item)
    .build()?;
```

Replace the entire block with a single line:

```rust
let focus_mode_submenu = crate::tray_kiosk::build_kiosk_submenu(app)?;
```

Keep the existing `.item(&focus_mode_submenu)` in the main MenuBuilder chain — that line stays the same.

- [ ] **Step 3.4: Replace the v1 listener with `tray_kiosk::install_kiosk_changed_listener`**

Find this block (around line 1720-1736 in v1):

```rust
if let Some(kiosk_win) = app.get_webview_window("main") {
    let app_handle = app.handle().clone();
    kiosk_win.listen("kiosk-changed", move |event| {
        let on: bool = serde_json::from_str(event.payload()).unwrap_or(false);
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

Replace with one line (also inside `.setup()`, after `build_kiosk_submenu` was called):

```rust
crate::tray_kiosk::install_kiosk_changed_listener(app);
```

(`app` here refers to the `AppHandle` passed to the `.setup()` closure. If your closure variable is named differently, adjust.)

- [ ] **Step 3.5: Replace the `on_menu_event` v1 kiosk arms with a single delegation**

Find this block (~line 1978-2008 in v1):

```rust
"focus_mode" => {
    let Some(w) = app.get_webview_window("main") else { return };
    if let Err(e) = crate::kiosk::toggle_kiosk(w) {
        eprintln!("[JARVIS] focus_mode toggle failed: {}", e);
    }
}
"focus_mode_off" => {
    let Some(w) = app.get_webview_window("main") else { return };
    if let Err(e) = crate::kiosk::exit_kiosk(w) {
        eprintln!("[JARVIS] focus_mode_off failed: {}", e);
    }
}
other if other.starts_with("focus_mode_mon_") => {
    // ... (entire 17-line block) ...
}
```

Replace with two delegated arms:

```rust
other if other.starts_with("kiosk_") => {
    crate::tray_kiosk::handle_kiosk_menu_event(app, other);
}
```

Note: `kiosk_off` and `kiosk_mon_<idx>` both start with `kiosk_`, so one prefix arm covers both.

- [ ] **Step 3.6: Update `tauri::generate_handler!` registrations**

Find the existing kiosk handler registrations:

```rust
kiosk::enter_kiosk,
kiosk::enter_kiosk_on_monitor,
kiosk::exit_kiosk,
kiosk::toggle_kiosk,
kiosk::kiosk_state,
```

Replace with just three (v2 has no `enter_kiosk` no-arg variant and no `toggle_kiosk`):

```rust
kiosk::enter_kiosk_on_monitor,
kiosk::exit_kiosk,
kiosk::kiosk_state,
```

- [ ] **Step 3.7: Build + verify no v1 references remain**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/desktop-tauri/src-tauri
cargo build 2>&1 | tail -15
grep -n "focus_mode\|FocusModeItem\|toggle_kiosk\|kiosk::enter_kiosk\b" src/main.rs | head -10
```

Expected: clean build, grep returns no `focus_mode_*` or `FocusModeItem` references. The only kiosk references should be the three command registrations + the kiosk-menu prefix arm + the build_kiosk_submenu + install_kiosk_changed_listener calls.

- [ ] **Step 3.8: Run tests**

```bash
cargo test --bin jarvis-desktop kiosk 2>&1 | tail -10
```

Expected: still 5 passed.

- [ ] **Step 3.9: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/desktop-tauri/src-tauri/src/main.rs
git commit -m "kiosk(v2): main.rs — drop v1 inline wiring, delegate to tray_kiosk module"
```

---

## Task 4 — React: `KioskArcReactor` component

**Files:**
- Create: `src/desktop-tauri/src/components/KioskArcReactor.jsx`

- [ ] **Step 4.1: Write the component**

Create `src/desktop-tauri/src/components/KioskArcReactor.jsx` with this content:

```jsx
import React from 'react'

// Pure SVG + CSS-keyframe arc reactor. No per-frame React (the
// reactor sphere rule from CLAUDE.md applies — animation is entirely
// CSS-driven, state prop only swaps the active className).
//
// State values:
//   "offline"   — dim, no animation (overlay's voice client not connected)
//   "idle"      — gentle pulse, slow rotate
//   "listening" — faster pulse, brighter glow
//   "speaking"  — fastest pulse
//   "thinking"  — rotation only, no pulse
//
// Colour: #1FD5F9 matches LiveKit AgentAudioVisualizerAura default.
const COLOR = '#1FD5F9'

export default function KioskArcReactor({ state = 'idle', size = 320 }) {
  const cls = `kiosk-arc kiosk-arc--${state}`
  return (
    <div className={cls} style={{ width: size, height: size }}>
      <svg viewBox="-50 -50 100 100" className="kiosk-arc-svg" xmlns="http://www.w3.org/2000/svg">
        {/* Center dot */}
        <circle cx="0" cy="0" r="3" fill={COLOR} className="kiosk-arc-center" />

        {/* Inner ring */}
        <circle cx="0" cy="0" r="8" fill="none" stroke={COLOR} strokeWidth="0.8"
                className="kiosk-arc-inner" />

        {/* Dotted ring */}
        <circle cx="0" cy="0" r="14" fill="none" stroke={COLOR} strokeWidth="0.7"
                strokeDasharray="1.3 2.5" className="kiosk-arc-dotted" />

        {/* Outer broken arc segments — three groups, one full circle worth */}
        <g className="kiosk-arc-outer">
          <circle cx="0" cy="0" r="22" fill="none" stroke={COLOR} strokeWidth="1.4"
                  strokeDasharray="30 16" strokeDashoffset="0" />
        </g>

        {/* Faint outermost ring for depth */}
        <circle cx="0" cy="0" r="32" fill="none" stroke={COLOR} strokeWidth="0.3"
                opacity="0.4" className="kiosk-arc-edge" />
      </svg>
      <style>{`
        .kiosk-arc {
          display: flex; align-items: center; justify-content: center;
          color: ${COLOR};
          filter: drop-shadow(0 0 6px ${COLOR}55);
        }
        .kiosk-arc-svg { width: 100%; height: 100%; overflow: visible; }
        .kiosk-arc-svg * { transform-origin: 0 0; transform-box: fill-box; }

        /* Default animations applied per ring */
        .kiosk-arc-inner { transform-origin: center; transform-box: view-box; }
        .kiosk-arc-dotted { transform-origin: center; transform-box: view-box; }
        .kiosk-arc-outer { transform-origin: center; transform-box: view-box; }
        .kiosk-arc-edge { transform-origin: center; transform-box: view-box; }

        @keyframes kiosk-arc-pulse {
          0%, 100% { opacity: 0.55; }
          50%      { opacity: 1.0; }
        }
        @keyframes kiosk-arc-rotate {
          from { transform: rotate(0deg); }
          to   { transform: rotate(360deg); }
        }
        @keyframes kiosk-arc-rotate-ccw {
          from { transform: rotate(0deg); }
          to   { transform: rotate(-360deg); }
        }

        /* Idle: slow pulse on the inner rings, slow rotate on outer */
        .kiosk-arc--idle .kiosk-arc-center,
        .kiosk-arc--idle .kiosk-arc-inner,
        .kiosk-arc--idle .kiosk-arc-dotted {
          animation: kiosk-arc-pulse 4s ease-in-out infinite;
        }
        .kiosk-arc--idle .kiosk-arc-outer {
          animation: kiosk-arc-rotate 30s linear infinite;
        }
        .kiosk-arc--idle .kiosk-arc-dotted {
          animation: kiosk-arc-pulse 4s ease-in-out infinite,
                     kiosk-arc-rotate-ccw 45s linear infinite;
        }

        /* Listening: faster pulse, brighter */
        .kiosk-arc--listening {
          filter: drop-shadow(0 0 10px ${COLOR}88);
        }
        .kiosk-arc--listening .kiosk-arc-center,
        .kiosk-arc--listening .kiosk-arc-inner,
        .kiosk-arc--listening .kiosk-arc-dotted {
          animation: kiosk-arc-pulse 1.5s ease-in-out infinite;
        }
        .kiosk-arc--listening .kiosk-arc-outer {
          animation: kiosk-arc-rotate 15s linear infinite;
        }
        .kiosk-arc--listening .kiosk-arc-dotted {
          animation: kiosk-arc-pulse 1.5s ease-in-out infinite,
                     kiosk-arc-rotate-ccw 25s linear infinite;
        }

        /* Speaking: fastest pulse */
        .kiosk-arc--speaking {
          filter: drop-shadow(0 0 14px ${COLOR}aa);
        }
        .kiosk-arc--speaking .kiosk-arc-center,
        .kiosk-arc--speaking .kiosk-arc-inner,
        .kiosk-arc--speaking .kiosk-arc-dotted {
          animation: kiosk-arc-pulse 0.8s ease-in-out infinite;
        }
        .kiosk-arc--speaking .kiosk-arc-outer {
          animation: kiosk-arc-rotate 10s linear infinite;
        }
        .kiosk-arc--speaking .kiosk-arc-dotted {
          animation: kiosk-arc-pulse 0.8s ease-in-out infinite,
                     kiosk-arc-rotate-ccw 18s linear infinite;
        }

        /* Thinking: rotation only, no pulse */
        .kiosk-arc--thinking .kiosk-arc-outer {
          animation: kiosk-arc-rotate 6s linear infinite;
        }
        .kiosk-arc--thinking .kiosk-arc-dotted {
          animation: kiosk-arc-rotate-ccw 9s linear infinite;
        }

        /* Offline: dim, no animation */
        .kiosk-arc--offline {
          filter: none;
          opacity: 0.35;
        }
      `}</style>
    </div>
  )
}
```

- [ ] **Step 4.2: Verify it builds**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/desktop-tauri
npm run build 2>&1 | tail -8
```

Expected: vite builds cleanly (the component isn't used yet — that's the next task — but syntax should be valid).

- [ ] **Step 4.3: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/desktop-tauri/src/components/KioskArcReactor.jsx
git commit -m "kiosk(v2): KioskArcReactor — pure SVG + CSS-keyframe state-driven animations"
```

---

## Task 5 — React: `KioskHUD` root (rewrite)

**Files:**
- Create or rewrite: `src/desktop-tauri/src/components/KioskHUD.jsx`

- [ ] **Step 5.1: Write KioskHUD**

Overwrite `src/desktop-tauri/src/components/KioskHUD.jsx` with this content (the v1 KioskHUD with transcript / waveform / clock is replaced — iteration 1 is intentionally minimal):

```jsx
import React, { useEffect, useState } from 'react'
import { invoke } from '@tauri-apps/api/core'
import KioskArcReactor from './KioskArcReactor.jsx'

// Root component for ?route=kiosk. Black fullscreen background with the
// arc reactor centered. State derived from a 500ms poll of
// http://127.0.0.1:8767/status (the same source the tray indicator uses).
//
// Iteration 1 is intentionally minimal. Future iterations may add:
//   - live transcript fade
//   - touch-tile grid for common voice actions
//   - audio-reactive Aura visualizer (LiveKit)
const STATUS_URL = 'http://127.0.0.1:8767/status'
const POLL_MS = 500

function deriveState(s) {
  if (!s || s.connected === false) return 'offline'
  if (s.speaking)     return 'speaking'
  if (s.voiceActive)  return 'listening'
  if (s.processing)   return 'thinking'
  if (s.booting)      return 'thinking'
  return 'idle'
}

export default function KioskHUD() {
  const [state, setState] = useState('idle')

  // Poll voice-client status. setInterval cleaned up on unmount.
  useEffect(() => {
    let cancelled = false
    async function tick() {
      try {
        const r = await fetch(STATUS_URL)
        const data = await r.json()
        if (!cancelled) setState(deriveState({ ...data, connected: true }))
      } catch {
        if (!cancelled) setState('offline')
      }
    }
    tick()
    const id = setInterval(tick, POLL_MS)
    return () => { cancelled = true; clearInterval(id) }
  }, [])

  // ESC key exits kiosk — belt-and-suspenders in case voice / tray / CLI fail.
  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'Escape') invoke('exit_kiosk').catch(console.error)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  return (
    <div className="kiosk-hud-root">
      <KioskArcReactor state={state} size={340} />
      <style>{`
        .kiosk-hud-root {
          position: fixed; inset: 0;
          background: #000;
          display: flex;
          align-items: center;
          justify-content: center;
          z-index: 9999;
          overflow: hidden;
          /* Hide cursor in kiosk for cinematic feel — set to 'default' if you want it visible. */
          cursor: none;
        }
      `}</style>
    </div>
  )
}
```

- [ ] **Step 5.2: Verify build**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/desktop-tauri
npm run build 2>&1 | tail -8
```

Expected: builds.

- [ ] **Step 5.3: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/desktop-tauri/src/components/KioskHUD.jsx
git commit -m "kiosk(v2): KioskHUD — black bg + centered arc reactor + ESC handler + status poll"
```

---

## Task 6 — React: route the `?route=kiosk` URL

**Files:**
- Modify: `src/desktop-tauri/src/main.jsx`

- [ ] **Step 6.1: Read current main.jsx**

```bash
cat /home/ulrich/Documents/Projects/jarvis/src/desktop-tauri/src/main.jsx
```

Expected to be a tiny file (~10 lines) that mounts `<App/>` on the root div. If there's already a `?route=keys` branch in `App.jsx` (not main.jsx) — that's the precedent: App.jsx itself routes inside its function body.

Look in App.jsx for:

```jsx
if (typeof window !== 'undefined' &&
    window.location.search.includes('route=keys')) {
  return <KeysSettings />
}
```

The kiosk route follows the same pattern *in App.jsx*, NOT main.jsx. Apologies if the Task header confused — the canonical edit point is App.jsx.

- [ ] **Step 6.2: Add the kiosk route branch in App.jsx**

Open `src/desktop-tauri/src/App.jsx`. Find the existing `route=keys` branch at the top of `App()`. Add a kiosk import at the top of the file (next to other component imports):

```jsx
import KioskHUD     from './components/KioskHUD.jsx'
```

Then add the kiosk route branch immediately after the `route=keys` branch:

```jsx
  if (typeof window !== 'undefined' &&
      window.location.search.includes('route=kiosk')) {
    return <KioskHUD />
  }
```

- [ ] **Step 6.3: Verify build**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/desktop-tauri
npm run build 2>&1 | tail -5
```

Expected: builds. KioskHUD is now reachable when the URL has `?route=kiosk`.

- [ ] **Step 6.4: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/desktop-tauri/src/App.jsx
git commit -m "kiosk(v2): App.jsx — add ?route=kiosk → <KioskHUD/> branch"
```

---

## Task 7 — App.jsx: revert v1 kiosk wiring (keep WS routing slot)

**Files:**
- Modify: `src/desktop-tauri/src/App.jsx`

This task drops the v1 kioskMode state, the v1 Tauri kiosk-changed listener, and the v1 conditional render branch. It KEEPS a slim `{type:'kiosk'}` WS handler.

- [ ] **Step 7.1: Locate v1 kiosk code in App.jsx**

```bash
grep -n "kiosk\|KioskHUD" /home/ulrich/Documents/Projects/jarvis/src/desktop-tauri/src/App.jsx
```

Expected hits:
- `import KioskHUD ...` (added in Task 6 — KEEP)
- `const [kioskMode, setKioskMode] = useState(false)` (DROP)
- The `useEffect` that calls `listen('kiosk-changed', ...)` (DROP)
- The WS handler `if (m.type === 'kiosk') ...` branch (REWRITE for v2)
- `if (kioskMode) { return <KioskHUD ... /> }` conditional (DROP — Task 6's route=kiosk replaces it)
- The route=kiosk branch from Task 6 (KEEP)

- [ ] **Step 7.2: Drop `kioskMode` state**

Find:
```jsx
const [kioskMode, setKioskMode] = useState(false)
```

Delete that line.

- [ ] **Step 7.3: Drop the kiosk-changed Tauri event listener**

Find this block (around v1 line 215):

```jsx
useEffect(() => {
  const un = listen('kiosk-changed', (e) => {
    const next = e.payload === true || e.payload === 'true'
    setKioskMode(next)
    if (!next) {
      setClickThrough(true)
      setLayer(false)
      reportPanelBounds({ x: 0, y: 0, w: 0, h: 0 })
    }
  })
  return () => { un.then(f => f()) }
}, [])
```

Delete the entire block. In v2 the kiosk-changed event is consumed by Rust (`tray_kiosk::install_kiosk_changed_listener`) and the main overlay doesn't need to react to it.

- [ ] **Step 7.4: Rewrite the WS `{type:'kiosk'}` handler**

Find:

```jsx
if (m.type === 'kiosk') {
  const cmd = m.state === 'on'  ? 'enter_kiosk' :
              m.state === 'off' ? 'exit_kiosk'  :
                                  'toggle_kiosk'
  invoke(cmd).catch(console.error)
}
```

Replace with:

```jsx
if (m.type === 'kiosk') {
  if (m.state === 'on' && typeof m.monitor === 'number') {
    invoke('enter_kiosk_on_monitor', { monitorIdx: m.monitor }).catch(console.error)
  } else if (m.state === 'off') {
    invoke('exit_kiosk').catch(console.error)
  } else {
    console.error('[kiosk] invalid WS msg', m)
  }
}
```

Note Tauri v2's command-arg convention: Rust snake_case `monitor_idx` ↔ JS camelCase `monitorIdx`.

- [ ] **Step 7.5: Drop the v1 conditional render**

Find:

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

Delete the entire block. Task 6's `route=kiosk` branch handles kiosk now; this conditional was for the v1 single-window approach where kiosk overlaid the same window.

- [ ] **Step 7.6: Verify build**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/desktop-tauri
npm run build 2>&1 | tail -5
```

Expected: builds.

- [ ] **Step 7.7: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/desktop-tauri/src/App.jsx
git commit -m "kiosk(v2): App.jsx — drop v1 single-window wiring, keep slim WS→Tauri router"
```

---

## Task 8 — Delete v1 orphan React components

**Files:**
- Delete: `src/desktop-tauri/src/components/KioskClock.jsx`
- Delete: `src/desktop-tauri/src/components/KioskVoiceWaveform.jsx`
- Delete: `src/desktop-tauri/src/components/KioskTranscript.jsx`

- [ ] **Step 8.1: Check which exist and delete**

```bash
cd /home/ulrich/Documents/Projects/jarvis
for f in KioskClock.jsx KioskVoiceWaveform.jsx KioskTranscript.jsx; do
  if [ -f "src/desktop-tauri/src/components/$f" ]; then
    git rm "src/desktop-tauri/src/components/$f"
  fi
done
```

If none exist, the loop is a no-op. That's fine — proceed.

- [ ] **Step 8.2: Verify nothing imports the deleted files**

```bash
grep -rn "KioskClock\|KioskVoiceWaveform\|KioskTranscript" src/desktop-tauri/src/ 2>&1 | head -5
```

Expected: empty output. If anything still imports them, those imports must be removed too (most likely from the v1 KioskHUD.jsx, which Task 5 already rewrote).

- [ ] **Step 8.3: Verify build**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/desktop-tauri
npm run build 2>&1 | tail -5
```

Expected: builds.

- [ ] **Step 8.4: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git commit -m "kiosk(v2): delete v1 orphan components (Clock, VoiceWaveform, Transcript)"
```

If `git status` shows nothing to commit (because none of those files existed in the first place), skip the commit and move on.

---

## Task 9 — Bridge `/api/kiosk` schema update

**Files:**
- Modify: `src/cli/src/bridge/server.ts`

- [ ] **Step 9.1: Find the existing /api/kiosk route**

```bash
grep -n "/api/kiosk\|type:.*kiosk" /home/ulrich/Documents/Projects/jarvis/src/cli/src/bridge/server.ts
```

Expected: route handler exists from v1 around line 489-500. Looks like:

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

- [ ] **Step 9.2: Replace the handler**

Use Edit to replace it with:

```typescript
if (url.pathname === '/api/kiosk' && req.method === 'POST') {
  let body: any
  try { body = await req.json() } catch {
    return Response.json({ error: 'invalid JSON' }, { status: 400 })
  }
  const state = typeof body?.state === 'string' ? body.state : ''
  if (!['on', 'off'].includes(state)) {
    return Response.json({ error: 'state must be on|off (no toggle in v2)' }, { status: 400 })
  }
  if (state === 'on') {
    const monitor = body?.monitor
    if (typeof monitor !== 'number' || !Number.isInteger(monitor) || monitor < 0) {
      return Response.json({ error: 'state=on requires monitor (non-negative integer)' }, { status: 400 })
    }
    broadcast({ type: 'kiosk', state: 'on', monitor })
    return Response.json({ ok: true, state: 'on', monitor })
  }
  // state === 'off'
  broadcast({ type: 'kiosk', state: 'off' })
  return Response.json({ ok: true, state: 'off' })
}
```

- [ ] **Step 9.3: Update the endpoint inventory comment**

At the top of server.ts (around line 10-20), find the comment block listing endpoints. Find:

```
//   POST   /api/kiosk                    → { ok: true, state }
```

Replace with:

```
//   POST   /api/kiosk { state:"on", monitor:<int> } → { ok, state, monitor }
//   POST   /api/kiosk { state:"off" }              → { ok, state }
```

- [ ] **Step 9.4: Smoke-build the bridge**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/cli
bun build src/bridge/server.ts --target=bun --outfile=/dev/null 2>&1 | tail -5
```

Expected: clean build.

- [ ] **Step 9.5: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/cli/src/bridge/server.ts
git commit -m "kiosk(v2)(bridge): /api/kiosk requires monitor when state=on; drop toggle"
```

---

## Task 10 — Voice agent: `kiosk_tool.py` v2 schema (TDD)

**Files:**
- Modify: `src/voice-agent/tools/kiosk_tool.py`
- Modify or create: `src/voice-agent/tests/test_kiosk_tool.py`

- [ ] **Step 10.1: Update tests first (TDD)**

Overwrite `src/voice-agent/tests/test_kiosk_tool.py` with:

```python
"""Tests for the v2 toggle_kiosk voice tool.

Verifies payload shape, monitor-required-when-on validation, bridge-down
handling, and refusal of v1 'toggle' state.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_state_on_posts_state_and_monitor():
    from tools.kiosk_tool import _handle_toggle_kiosk
    mock_resp = AsyncMock()
    mock_resp.status_code = 200
    mock_resp.json = lambda: {"ok": True, "state": "on", "monitor": 1}
    mock_resp.raise_for_status = lambda: None
    with patch("tools.kiosk_tool._post_to_bridge", new=AsyncMock(return_value=mock_resp)) as mock_post:
        result = await _handle_toggle_kiosk({"state": "on", "monitor": 1})
        assert "1" in result or "on" in result.lower()
        mock_post.assert_awaited_once()
        sent = mock_post.await_args.args[0]
        assert sent == {"state": "on", "monitor": 1}


@pytest.mark.asyncio
async def test_state_on_without_monitor_returns_error():
    from tools.kiosk_tool import _handle_toggle_kiosk
    result = await _handle_toggle_kiosk({"state": "on"})
    parsed = json.loads(result)
    assert "error" in parsed
    assert "monitor" in parsed["error"].lower() or "which screen" in parsed["error"].lower()


@pytest.mark.asyncio
async def test_state_off_posts_off():
    from tools.kiosk_tool import _handle_toggle_kiosk
    mock_resp = AsyncMock()
    mock_resp.json = lambda: {"ok": True, "state": "off"}
    mock_resp.raise_for_status = lambda: None
    with patch("tools.kiosk_tool._post_to_bridge", new=AsyncMock(return_value=mock_resp)) as mock_post:
        result = await _handle_toggle_kiosk({"state": "off"})
        assert "off" in result.lower()
        sent = mock_post.await_args.args[0]
        assert sent == {"state": "off"}


@pytest.mark.asyncio
async def test_invalid_state_returns_error():
    from tools.kiosk_tool import _handle_toggle_kiosk
    result = await _handle_toggle_kiosk({"state": "toggle"})
    parsed = json.loads(result)
    assert "error" in parsed


@pytest.mark.asyncio
async def test_monitor_non_integer_returns_error():
    from tools.kiosk_tool import _handle_toggle_kiosk
    result = await _handle_toggle_kiosk({"state": "on", "monitor": "main"})
    parsed = json.loads(result)
    assert "error" in parsed


@pytest.mark.asyncio
async def test_bridge_down_returns_error():
    from tools.kiosk_tool import _handle_toggle_kiosk
    with patch("tools.kiosk_tool._post_to_bridge", new=AsyncMock(side_effect=ConnectionError("bridge down"))):
        result = await _handle_toggle_kiosk({"state": "on", "monitor": 0})
        parsed = json.loads(result)
        assert "error" in parsed
```

- [ ] **Step 10.2: Run tests to verify they fail against the v1 tool**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -m pytest tests/test_kiosk_tool.py -v 2>&1 | tail -20
```

Expected: most tests fail (the v1 tool accepts `state=toggle`, doesn't validate monitor, etc.).

- [ ] **Step 10.3: Rewrite kiosk_tool.py**

Overwrite `src/voice-agent/tools/kiosk_tool.py` with:

```python
"""toggle_kiosk (v2) — flip the desktop into / out of cinematic focus mode.

v2 schema requires `monitor: int` when `state="on"`. There is no "toggle"
state in v2 — every trigger must explicitly say which monitor or "off".

The actual UI + WM minimize happens in the Tauri desktop overlay; this
tool only POSTs the intent to the bridge.
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
    return {"Authorization": f"Bearer {tok}"} if tok else {}


async def _post_to_bridge(payload: Dict[str, Any]) -> httpx.Response:
    """Indirection: the unit tests patch this symbol to avoid real HTTP."""
    async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
        return await client.post(
            f"{_BRIDGE_URL}/api/kiosk",
            json=payload,
            headers=_auth_headers(),
        )


async def _handle_toggle_kiosk(args: Dict[str, Any]) -> str:
    state = (args or {}).get("state", "")
    if state not in ("on", "off"):
        return tool_error(
            f"toggle_kiosk: state must be 'on' or 'off' (no toggle in v2); got {state!r}"
        )

    payload: Dict[str, Any] = {"state": state}
    if state == "on":
        monitor = (args or {}).get("monitor")
        if not isinstance(monitor, int) or isinstance(monitor, bool) or monitor < 0:
            return tool_error(
                "toggle_kiosk: state=on requires monitor (non-negative integer). "
                "Ask the user which screen / monitor index they want before retrying."
            )
        payload["monitor"] = monitor

    try:
        resp = await _post_to_bridge(payload)
        if hasattr(resp, "raise_for_status"):
            resp.raise_for_status()
    except ConnectionError as e:
        return tool_error(f"toggle_kiosk: could not reach desktop bridge — {e}")
    except httpx.HTTPError as e:
        return tool_error(f"toggle_kiosk: bridge returned error — {e}")

    if state == "on":
        return f"kiosk on (monitor {payload['monitor']})"
    return "kiosk off"


_SCHEMA = {
    "name": "toggle_kiosk",
    "description": (
        "Enter / exit cinematic full-screen focus mode (kiosk) on the desktop "
        "overlay. In kiosk mode every other window is minimized and JARVIS fills "
        "the chosen screen with the arc-reactor HUD. v2 schema requires explicit "
        "monitor selection when state=on — there is no 'toggle' state. If the "
        "user says 'focus mode' without naming a screen, ASK which monitor "
        "before calling this tool."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "state": {
                "type": "string",
                "enum": ["on", "off"],
                "description": "on = enter kiosk (requires monitor); off = exit kiosk",
            },
            "monitor": {
                "type": "integer",
                "minimum": 0,
                "description": (
                    "Monitor index (0-based). Required when state=on. Name-based "
                    "resolution (e.g. 'main', 'laptop') is not supported in iteration 1; "
                    "ask the user for a number."
                ),
            },
        },
        "required": ["state"],
    },
}

registry.register(
    name="toggle_kiosk",
    schema=_SCHEMA,
    handler=_handle_toggle_kiosk,
    toolset="desktop",
    check_fn=None,
    is_async=True,
)
```

- [ ] **Step 10.4: Run tests — all six pass**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -m pytest tests/test_kiosk_tool.py -v 2>&1 | tail -15
```

Expected: 6 passed.

- [ ] **Step 10.5: Run full suite for regression check**

```bash
.venv/bin/python -m pytest tests/ 2>&1 | tail -8
```

Expected: full suite passes (~2655 + 6 - 4 = ~2657 if you're starting from v1; the four v1 tests are replaced by the six v2 tests). If you see pre-existing failures unrelated to kiosk, note them but don't fix them.

- [ ] **Step 10.6: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/voice-agent/tools/kiosk_tool.py src/voice-agent/tests/test_kiosk_tool.py
git commit -m "kiosk(v2)(voice): toggle_kiosk requires monitor when state=on; drop toggle state"
```

---

## Task 11 — Supervisor.md routing paragraph update

**Files:**
- Modify: `src/voice-agent/prompts/supervisor.md`

- [ ] **Step 11.1: Find existing v1 paragraph**

```bash
grep -n "Focus mode\|toggle_kiosk\|kiosk" /home/ulrich/Documents/Projects/jarvis/src/voice-agent/prompts/supervisor.md | head -5
```

Expected: at least one paragraph mentioning `toggle_kiosk` from v1.

- [ ] **Step 11.2: Replace the paragraph**

Use Edit to replace the existing v1 paragraph with:

```markdown
**Focus mode (kiosk).** Phrases like "go full screen", "enter focus mode", "kiosk mode", "fullscreen JARVIS", "tune everything else out" → identify which monitor the user wants, then call `toggle_kiosk(state="on", monitor=<idx>)`. If the user did NOT name a screen, ASK which monitor (by number — iteration 1 doesn't resolve names like "main" / "laptop"). Phrases like "exit focus", "go back to normal", "show me the desktop" → call `toggle_kiosk(state="off")`. There is NO toggle state in v2: every kiosk command is explicit. Don't restate the action — the visual change is the confirmation.
```

If no v1 paragraph exists (fresh branch with no v1 history), insert this paragraph next to the other per-tool routing paragraphs (e.g., near the `computer_use` or `browser_task` routing notes).

- [ ] **Step 11.3: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/voice-agent/prompts/supervisor.md
git commit -m "kiosk(v2)(voice): supervisor.md — ask which monitor; no toggle state"
```

---

## Task 12 — CLI wrapper rewrite

**Files:**
- Modify or create: `bin/jarvis-kiosk`

- [ ] **Step 12.1: Rewrite bin/jarvis-kiosk**

Overwrite `bin/jarvis-kiosk` with:

```sh
#!/bin/sh
# JARVIS kiosk v2 — enter on an explicit monitor or exit.
#
# Usage:
#   jarvis-kiosk <monitor-index>    enter on monitor <index> (non-negative integer)
#   jarvis-kiosk off                exit kiosk
#
# Examples:
#   jarvis-kiosk 0       # enter on monitor 0 (typically primary)
#   jarvis-kiosk 1       # enter on monitor 1 (secondary)
#   jarvis-kiosk off
#
# Tip: see detected monitors with `xrandr --listmonitors`.
set -e

ARG="${1:-}"

print_usage() {
  echo "usage: $(basename "$0") <monitor-index>|off" >&2
  echo "  enter on monitor N: $(basename "$0") N    (N = non-negative integer)" >&2
  echo "  exit kiosk:         $(basename "$0") off" >&2
  echo "" >&2
  echo "Detected monitors (xrandr):" >&2
  xrandr --listmonitors 2>/dev/null | sed -n 's/^/  /p' >&2
}

if [ -z "$ARG" ]; then
  print_usage
  exit 2
fi

# Decode arg into JSON payload.
case "$ARG" in
  off)
    PAYLOAD='{"state":"off"}'
    ;;
  *[!0-9]*)
    echo "error: monitor index must be a non-negative integer (got: $ARG)" >&2
    print_usage
    exit 2
    ;;
  *)
    PAYLOAD="{\"state\":\"on\",\"monitor\":$ARG}"
    ;;
esac

# Auth: optional bearer token from ~/.jarvis/local-api-token.env.
TOKEN=""
if [ -r "$HOME/.jarvis/local-api-token.env" ]; then
  TOKEN="$(sed -n 's/^JARVIS_LOCAL_API_TOKEN=//p' "$HOME/.jarvis/local-api-token.env" | head -1)"
fi
AUTH=""
if [ -n "$TOKEN" ]; then
  AUTH="-H Authorization: Bearer $TOKEN"
fi

exec curl -fsS -X POST "http://127.0.0.1:8765/api/kiosk" \
  -H "Content-Type: application/json" \
  $AUTH \
  -d "$PAYLOAD"
```

Make it executable:

```bash
chmod +x /home/ulrich/Documents/Projects/jarvis/bin/jarvis-kiosk
```

- [ ] **Step 12.2: Smoke-test argument validation (no live bridge needed)**

```bash
/home/ulrich/Documents/Projects/jarvis/bin/jarvis-kiosk           2>&1 | head -3
/home/ulrich/Documents/Projects/jarvis/bin/jarvis-kiosk bogus     2>&1 | head -3
/home/ulrich/Documents/Projects/jarvis/bin/jarvis-kiosk -1        2>&1 | head -3
```

Expected: each prints usage / error and exits non-zero. The integer case `jarvis-kiosk 0` would try to POST to the bridge — only test that one when the bridge is live (Task 13).

- [ ] **Step 12.3: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add bin/jarvis-kiosk
git commit -m "kiosk(v2)(cli): bin/jarvis-kiosk — required monitor index; usage shows xrandr list"
```

---

## Task 13 — Verification: builds + unit tests

**Files:** none (verification only)

- [ ] **Step 13.1: Rust unit tests**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/desktop-tauri/src-tauri
cargo test --bin jarvis-desktop kiosk 2>&1 | tail -10
```

Expected: 5 passed (`enter_minimizes_non_jarvis_windows`, `enter_when_already_on_returns_err`, `exit_restores_minimized_windows`, `exit_when_already_off_is_idempotent`, `enter_graceful_when_wmctrl_missing`).

- [ ] **Step 13.2: Voice-agent tests**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -m pytest tests/test_kiosk_tool.py -v 2>&1 | tail -10
.venv/bin/python -m pytest tests/ 2>&1 | tail -5
```

Expected: 6 kiosk_tool tests pass; full suite passes (modulo pre-existing unrelated failures).

- [ ] **Step 13.3: Vite (desktop frontend) build**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/desktop-tauri
npm run build 2>&1 | tail -8
```

Expected: vite builds cleanly.

- [ ] **Step 13.4: Cargo release build (per `.claude/rules/desktop-tauri.md`)**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/desktop-tauri/src-tauri
cargo build --release 2>&1 | tail -6
```

Expected: release binary at `target/release/jarvis-desktop`. First build ~5 min; incremental ~30-60 s.

- [ ] **Step 13.5: Bridge typecheck**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/cli
bun build src/bridge/server.ts --target=bun --outfile=/dev/null 2>&1 | tail -5
```

Expected: builds cleanly.

- [ ] **Step 13.6: CLI smoke check**

```bash
/home/ulrich/Documents/Projects/jarvis/bin/jarvis-kiosk bogus 2>&1 | head -2
```

Expected: usage line + exit 2.

No commit for this task — verification only.

---

## Task 14 — Manual E2E walkthrough

**Files:** none (manual checklist)

Per the spec's testing section. Do NOT commit anything from this task; it's verification.

- [ ] **Step 14.1: Pre-state**

Verify JARVIS overlay is running with the production (pre-kiosk-v2) binary, OR start a fresh dev instance:

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/desktop-tauri
npm run tauri dev 2>&1 | head -50 &
sleep 8
```

Confirm the main HUD overlay is transparent + click-through. Click an underlying app (Firefox, terminal); verify click lands on the app.

- [ ] **Step 14.2: Tray submenu shows monitor entries**

Right-click the JARVIS tray icon → hover "Focus mode (kiosk) ▸" → verify the submenu shows one entry per detected screen + "Exit focus mode". Entry labels should look like `Monitor 0: 1920x1080 at 0,0` (or with monitor names if X11/EDID provides them).

- [ ] **Step 14.3: Enter on monitor 0**

Click the first monitor entry. Verify:
- A fresh black window covers monitor 0 entirely
- A centered cyan arc reactor is visible, gently pulsing (idle state)
- Other apps are minimized
- The tray submenu now shows a check mark on the chosen monitor's entry

- [ ] **Step 14.4: Exit via tray "Exit focus mode"**

Right-click tray → "Focus mode (kiosk) ▸" → "Exit focus mode". Verify:
- Kiosk window disappears
- Minimized windows restore
- Tray check mark clears
- **CRITICAL:** Click anywhere on the main overlay's footprint (the transparent HUD region). Click should pass through to the underlying app. This is the regression check — v1 broke this.

- [ ] **Step 14.5: Enter on monitor 1**

Repeat 14.3 for the second monitor. Confirm kiosk fills the right screen (not monitor 0).

- [ ] **Step 14.6: ESC inside kiosk exits**

Enter kiosk on any monitor. Click inside the dark surface to focus it. Press `Escape`. Verify exit (same as 14.4 — windows restore, click-through restored).

- [ ] **Step 14.7: Tray monitor entry as toggle**

Enter on monitor 0 via tray. Then click the same entry again. Verify exit (toggle behavior).

- [ ] **Step 14.8: CLI**

```bash
/home/ulrich/Documents/Projects/jarvis/bin/jarvis-kiosk 0    # enter on monitor 0
sleep 2
/home/ulrich/Documents/Projects/jarvis/bin/jarvis-kiosk off  # exit
```

Verify same enter/exit visually. Verify the tray check toggles in sync.

- [ ] **Step 14.9: Voice "Jarvis, focus mode" without monitor → ask**

Speak: `"Jarvis, focus mode"`. JARVIS should NOT enter; instead supervisor asks something like "which screen?" / "which monitor?". Verify JARVIS does not invoke the tool blindly.

- [ ] **Step 14.10: Voice "Jarvis, focus mode on monitor zero"**

Speak: `"Jarvis, focus mode on monitor zero"`. Verify enter on monitor 0. Then `"Jarvis, exit focus mode"` → verify exit.

- [ ] **Step 14.11: wmctrl-missing graceful**

```bash
sudo mv "$(which wmctrl)" /tmp/wmctrl.bak
/home/ulrich/Documents/Projects/jarvis/bin/jarvis-kiosk 0
```

Verify the kiosk window still appears (overlay-only behavior); other apps don't minimize; the log line `wmctrl list failed` or `wmctrl not found` appears in the desktop stderr. Exit kiosk; verify no crash. Then:

```bash
sudo mv /tmp/wmctrl.bak "$(which curl | xargs dirname)/wmctrl"
```

- [ ] **Step 14.12: Keys window isolation**

Click tray → "Manage API Keys…". Then enter kiosk via tray. Verify the keys window is unaffected by kiosk (still visible / still interactive on its own monitor — it's a separate Tauri window).

- [ ] **Step 14.13: Document the run**

Capture the outcome (pass / partial / fail per step) in the PR description that lands this branch.

---

## Implementation guardrails (sanity checks before merge)

Re-read these against the spec at `docs/superpowers/specs/2026-05-28-jarvis-kiosk-mode-v2-design.md`:

- [ ] **Main overlay is never touched on enter/exit.** No `set_position`, `set_size`, `set_always_on_top`, `set_ignore_cursor_events` on the main window in the kiosk v2 code path. Confirmed in Task 1's `enter_kiosk_on_monitor` and `exit_kiosk` — only the kiosk window is created/destroyed.
- [ ] **Per-monitor MenuItems retained in AppState.** Task 2's `KioskMonitorItems(Mutex<Vec<CheckMenuItem<Wry>>>)`. Items must outlive setup() — the Vec inside `AppState` ensures this.
- [ ] **No toggle state.** No `toggle_kiosk` Tauri command. CLI rejects no-arg. Voice tool rejects `state="toggle"`. Bridge rejects `state="toggle"`.
- [ ] **HiDPI scale-factor guard wired.** Task 1's `if size.width < 1024 && scale > 1.5 { ... }` heuristic.
- [ ] **on_window_event(CloseRequested) clean-up.** Task 1 wires this so kill-from-WM still clears KIOSK_STATE.
- [ ] **Frozen tray indicator untouched.** No diffs to `tray_image_for`, `apply_sharing_ring`, `icons/tray.png`, the colour palette, the poll rate.
- [ ] **Sanitizers / confab detector / automod blocklist files / soul.md / MEMORY.md / CLAUDE.md untouched.**
- [ ] **App.jsx is a slim WS router for kiosk.** Tasks 7's `{type:'kiosk'}` handler is ~6 lines.

## Final code-review request

After all 14 tasks land, request a final code review across the full v2 implementation. Suggested prompt:

> Review the full kiosk v2 implementation. Branch: `<current-branch>`. Spec: `docs/superpowers/specs/2026-05-28-jarvis-kiosk-mode-v2-design.md`. Plan: `docs/superpowers/plans/2026-05-28-jarvis-kiosk-mode-v2.md`. Focus on:
> - Whether the "main overlay never touched" invariant actually holds in the diff
> - Concurrency / lifecycle correctness around `KIOSK_STATE` + the `on_window_event` close-cleanup
> - Whether the dropped v1 surfaces are fully gone (no orphan references)
> - Whether per-monitor MenuItem retention is correctly wired (items must be alive in AppState for `set_checked` to work)
