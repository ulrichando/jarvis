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
