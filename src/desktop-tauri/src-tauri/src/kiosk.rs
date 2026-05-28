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
    AppHandle, Emitter, Manager, PhysicalPosition, PhysicalSize, WebviewUrl, WebviewWindowBuilder,
};

// ─── WmctrlAdapter trait + types ───────────────────────────────────────────

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
    let class_match = w.wm_class.contains("J.A.R.V.I.S.")
        || w.wm_class.contains("jarvis")
        || w.wm_class.contains("Jarvis");
    let kiosk_title = w.title.contains("J.A.R.V.I.S. \u{2014} kiosk")
        || w.title == "kiosk";
    class_match || kiosk_title
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
    // Use PhysicalPosition / PhysicalSize EXPLICITLY so the framework
    // doesn't mis-interpret values as logical (which on HiDPI displays
    // would multiply by scale_factor and either overflow or underflow
    // the screen). monitor.position() / monitor.size() return physical
    // pixels per Tauri v2 docs, and the scale-factor guard above
    // ensures pos_x/y/w/h are physical even if a Tauri bug returns
    // logical from .size() — passing typed wrappers makes the contract
    // explicit at the boundary.
    let result = WebviewWindowBuilder::new(&app, "kiosk", WebviewUrl::App("index.html?route=kiosk".into()))
        .decorations(false)
        .transparent(false)
        .always_on_top(true)
        .focused(true)
        .skip_taskbar(true)
        .resizable(false)
        .title("J.A.R.V.I.S. \u{2014} kiosk")
        .position(PhysicalPosition::<i32>::new(pos_x, pos_y))
        .inner_size(PhysicalSize::<u32>::new(size_w, size_h))
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
        .args(["-r", "J.A.R.V.I.S. \u{2014} kiosk", "-b", "add,above"])
        .output();

    // 4. on_window_event handler — if the user kills the window directly
    // (kill -9, or some WM-driven close), clean up state.
    let app_for_close = app.clone();
    kiosk_window.on_window_event(move |event| {
        if matches!(event, tauri::WindowEvent::CloseRequested { .. }) {
            // The window is already closing — only clean up state/snapshot
            // and emit the off-event. Don't call exit_kiosk because it
            // would call w.close() again (double-close risk on GTK).
            let adapter = RealWmctrl;
            if let Ok(mut state) = KIOSK_STATE.lock() {
                let _ = exit_kiosk_impl(&adapter, &mut state);
            }
            let _ = app_for_close.emit("kiosk-changed", serde_json::json!({ "on": false }));
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
