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
}

pub static KIOSK_STATE: LazyLock<Mutex<Option<KioskSnapshot>>> = LazyLock::new(|| Mutex::new(None));

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
}
