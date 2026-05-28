//! Kiosk / owner-focus posture for the JARVIS desktop overlay.
//!
//! Module is structured so the WM interaction is hidden behind a small
//! adapter trait; pure logic (state transitions, snapshot composition)
//! is unit-testable without a running X11 / wmctrl. Tauri command
//! wrappers at the bottom translate window-flag side effects onto the
//! real overlay window.

use std::sync::{LazyLock, Mutex};

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
    // Window bounds snapshot — kiosk resizes the overlay to fill the
    // monitor under the cursor; on exit we restore the original position
    // + size so the chat-panel HUD geometry comes back unchanged.
    pub prev_pos_x: i32,
    pub prev_pos_y: i32,
    pub prev_size_w: u32,
    pub prev_size_h: u32,
}

pub static KIOSK_STATE: LazyLock<Mutex<Option<KioskSnapshot>>> = LazyLock::new(|| Mutex::new(None));

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

/// Returns Ok(true) for a fresh entry, Ok(false) for an idempotent re-entry.
/// The Tauri-window-flag changes (set_always_on_top etc.) are the caller's
/// responsibility — this function only owns the WM snapshot + minimize step.
pub fn enter_kiosk_impl<A: WmctrlAdapter>(
    adapter: &A,
    state: &mut Option<KioskSnapshot>,
    prev_always_on_top: bool,
    prev_click_through: bool,
    prev_pos: (i32, i32),
    prev_size: (u32, u32),
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
        prev_pos_x: prev_pos.0,
        prev_pos_y: prev_pos.1,
        prev_size_w: prev_size.0,
        prev_size_h: prev_size.1,
    });
    Ok(true)
}

fn is_jarvis_window(w: &WindowInfo) -> bool {
    w.wm_class.contains("J.A.R.V.I.S.")
        || w.wm_class.contains("jarvis")
        || w.wm_class.contains("Jarvis")
}

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

use tauri::{PhysicalPosition, PhysicalSize, WebviewWindow, Emitter};

/// Snapshot the overlay's current outer position + size in PHYSICAL pixels.
/// Returns (0,0,1920,1080) as a safe fallback if either Tauri call fails.
fn snapshot_window_bounds(window: &WebviewWindow) -> ((i32, i32), (u32, u32)) {
    let pos = window.outer_position().ok();
    let size = window.outer_size().ok();
    let (px, py) = pos.map(|p| (p.x, p.y)).unwrap_or((0, 0));
    let (sw, sh) = size.map(|s| (s.width, s.height)).unwrap_or((1920, 1080));
    ((px, py), (sw, sh))
}

/// Find the monitor the overlay is currently sitting on (or that the cursor
/// is on, as a fallback). Returns (x, y, w, h) in PHYSICAL pixels — the same
/// coordinate space `outer_position` / `outer_size` use.
fn current_monitor_bounds(window: &WebviewWindow) -> Option<(i32, i32, u32, u32)> {
    if let Ok(Some(m)) = window.current_monitor() {
        let pos = m.position();
        let size = m.size();
        return Some((pos.x, pos.y, size.width, size.height));
    }
    if let Ok(Some(m)) = window.primary_monitor() {
        let pos = m.position();
        let size = m.size();
        return Some((pos.x, pos.y, size.width, size.height));
    }
    None
}

#[tauri::command]
pub fn enter_kiosk(window: WebviewWindow) -> Result<(), String> {
    let adapter = RealWmctrl;
    let prev_aot = window.is_always_on_top().unwrap_or(false);
    let prev_ct  = true;  // default to true to match overlay's normal posture;
                          // Tauri v2 has no getter for the current value, but
                          // the overlay defaults to click-through enabled.
    let (prev_pos, prev_size) = snapshot_window_bounds(&window);

    let mut state = KIOSK_STATE.lock().map_err(|e| e.to_string())?;
    let entered = enter_kiosk_impl(
        &adapter, &mut state, prev_aot, prev_ct, prev_pos, prev_size,
    )?;
    if entered {
        // Resize + reposition to fill the monitor under the overlay so
        // kiosk actually covers the screen the user is looking at. Without
        // this the overlay stays at its prior bounds (typically 1920x1080
        // at origin) which is the wrong monitor on multi-display setups.
        if let Some((mx, my, mw, mh)) = current_monitor_bounds(&window) {
            let _ = window.set_position(PhysicalPosition::new(mx, my));
            let _ = window.set_size(PhysicalSize::new(mw, mh));
        }
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
    // Capture prev fields before take(); we restore overlay flags + bounds.
    let prev_aot = state.as_ref().map(|s| s.prev_always_on_top).unwrap_or(false);
    let prev_ct  = state.as_ref().map(|s| s.prev_click_through).unwrap_or(true);
    let prev_pos = state.as_ref().map(|s| (s.prev_pos_x, s.prev_pos_y)).unwrap_or((0, 0));
    let prev_size = state.as_ref().map(|s| (s.prev_size_w, s.prev_size_h)).unwrap_or((1920, 1080));
    let exited = exit_kiosk_impl(&adapter, &mut state)?;
    if exited {
        let _ = window.set_position(PhysicalPosition::new(prev_pos.0, prev_pos.1));
        let _ = window.set_size(PhysicalSize::new(prev_size.0, prev_size.1));
        let _ = window.set_always_on_top(prev_aot);
        let _ = window.set_ignore_cursor_events(prev_ct);
    }
    let _ = window.emit("kiosk-changed", false);
    Ok(())
}

#[tauri::command]
pub fn toggle_kiosk(window: WebviewWindow) -> Result<(), String> {
    // Read state under a short-lived guard then release before calling
    // the inner command (which re-acquires). Both enter_kiosk and
    // exit_kiosk are idempotent (`enter_kiosk_impl` / `exit_kiosk_impl`
    // return Ok(false) on no-op re-entry/re-exit), so a concurrent state
    // flip between the read and the call at worst produces one
    // redundant transition — never a deadlock, never wrong state.
    let on = {
        let guard = KIOSK_STATE.lock().map_err(|e| e.to_string())?;
        guard.is_some()
    };
    if on { exit_kiosk(window) } else { enter_kiosk(window) }
}

#[tauri::command]
pub fn kiosk_state() -> Result<bool, String> {
    Ok(KIOSK_STATE.lock().map_err(|e| e.to_string())?.is_some())
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
        let entered = enter_kiosk_impl(&mock, &mut state, /*prev_aot=*/false, /*prev_ct=*/true, (0,0), (1920,1080)).unwrap();
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
            prev_pos_x: 100, prev_pos_y: 200,
            prev_size_w: 800, prev_size_h: 600,
        });
        let entered = enter_kiosk_impl(&mock, &mut state, /*prev_aot=*/true, /*prev_ct=*/false, (0,0), (1920,1080)).unwrap();
        assert!(!entered, "re-entry should report entered=false");
        assert!(mock.minimized.borrow().is_empty(), "should not minimize anything new");
        assert_eq!(state.as_ref().unwrap().minimized_ids, vec!["0x999".to_string()],
                   "snapshot stays untouched");
    }

    #[test]
    fn exit_restores_minimized_windows() {
        let mock = MockWmctrl::new(make_windows());
        let mut state: Option<KioskSnapshot> = Some(KioskSnapshot {
            minimized_ids: vec!["0x200".into(), "0x300".into()],
            prev_always_on_top: false,
            prev_click_through: true,
            prev_pos_x: 0, prev_pos_y: 0,
            prev_size_w: 1920, prev_size_h: 1080,
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

    #[test]
    fn enter_graceful_when_wmctrl_missing() {
        let mock = MockWmctrl::new(vec![]);
        *mock.list_fails_with.borrow_mut() = Some(WmctrlError::NotFound);
        let mut state: Option<KioskSnapshot> = None;
        let entered = enter_kiosk_impl(&mock, &mut state, false, true, (0,0), (1920,1080)).unwrap();
        assert!(entered, "enter should succeed even when wmctrl is unavailable");
        let snap = state.as_ref().unwrap();
        assert!(snap.minimized_ids.is_empty(),
                "no windows enumerated → no minimized_ids");
        assert_eq!(snap.prev_always_on_top, false);
        assert_eq!(snap.prev_click_through, true);
    }
}
