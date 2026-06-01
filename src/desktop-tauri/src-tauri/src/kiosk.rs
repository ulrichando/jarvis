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

#[derive(Debug, Clone, Default, PartialEq)]
pub struct WindowInfo {
    pub id: String,
    pub wm_class: String,
    pub title: String,
    /// Window position + size in physical pixels (X11 root coords).
    /// Populated from `wmctrl -lG`. Zero-defaults when unavailable so
    /// callers that don't filter by monitor still work.
    pub x: i32,
    pub y: i32,
    pub w: i32,
    pub h: i32,
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
        // -lGx: id, desktop, x, y, w, h, wm_class, machine, title
        // The G flag adds geometry — required for monitor filtering so a
        // kiosk on one monitor doesn't minimize windows on other monitors.
        let out = Command::new("wmctrl").args(["-lGx"]).output();
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
            // splitn(9, ws) so title (col 8) keeps embedded spaces.
            let cols: Vec<&str> = line.splitn(9, char::is_whitespace).filter(|s| !s.is_empty()).collect();
            if cols.len() < 9 {
                continue;
            }
            let id = cols[0].to_string();
            let x = cols[2].parse::<i32>().unwrap_or(0);
            let y = cols[3].parse::<i32>().unwrap_or(0);
            let w = cols[4].parse::<i32>().unwrap_or(0);
            let h = cols[5].parse::<i32>().unwrap_or(0);
            let wmc = cols[6].to_string();
            let title = cols[8].to_string();
            if id.is_empty() || wmc.is_empty() {
                continue;
            }
            windows.push(WindowInfo { id, wm_class: wmc, title, x, y, w, h });
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
/// Returns true if `w`'s center point lies inside `bounds` (mx, my, mw, mh).
/// Bounds-with-None means "no filter" — used by tests + back-compat paths.
fn window_on_monitor(w: &WindowInfo, bounds: Option<(i32, i32, i32, i32)>) -> bool {
    let Some((mx, my, mw, mh)) = bounds else { return true };
    let cx = w.x + w.w / 2;
    let cy = w.y + w.h / 2;
    cx >= mx && cx < mx + mw && cy >= my && cy < my + mh
}

pub fn enter_kiosk_impl<A: WmctrlAdapter>(
    adapter: &A,
    state: &mut Option<KioskSnapshot>,
    monitor_idx: usize,
    monitor_bounds: Option<(i32, i32, i32, i32)>,
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
        // Multi-monitor: only minimize windows on the kiosk's own monitor.
        // Windows on other monitors stay put so the user can keep using them.
        if !window_on_monitor(&w, monitor_bounds) {
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

// ─── Token helper (used by enter_kiosk_on_monitor + get_bridge_token) ─────

fn read_bridge_token() -> String {
    let e = std::env::var("JARVIS_LOCAL_API_TOKEN").unwrap_or_default();
    if !e.is_empty() { return e; }
    let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_string());
    let p = std::path::PathBuf::from(home).join(".jarvis").join("local-api-token.env");
    std::fs::read_to_string(&p).ok()
        .and_then(|c| c.lines().find_map(|l|
            l.strip_prefix("JARVIS_LOCAL_API_TOKEN=")
                .map(|s| s.trim().trim_matches('"').to_string())
        ))
        .unwrap_or_default()
}

#[tauri::command]
pub fn get_bridge_token() -> String {
    read_bridge_token()
}

/// Spawn (or focus) the chat as its OWN decorated, non-transparent
/// WebviewWindow. Splitting the ChatPanel out of the main transparent
/// overlay sidesteps the WebKitGTK transparent-compositor ghost-frame
/// bug (tauri#12800/#13157/#14924) — the research-recommended enterprise
/// fix.
#[tauri::command]
pub fn open_chat_window(app: AppHandle) -> Result<(), String> {
    if let Some(w) = app.get_webview_window("chat") {
        let _ = w.show();
        let _ = w.set_focus();
        return Ok(());
    }
    // Inject the bridge token so the chat webview can auth /api/* the
    // same way main/kiosk do.
    let bridge_token = read_bridge_token();
    let token_js = serde_json::to_string(&bridge_token)
        .unwrap_or_else(|_| "\"\"".to_string());
    let init = format!("window.__JARVIS_LOCAL_API_TOKEN = {};", token_js);

    WebviewWindowBuilder::new(&app, "chat", WebviewUrl::App("index.html?route=chat".into()))
        .title("J.A.R.V.I.S. \u{2014} Chat")
        .decorations(false)   // VS Code-style: we draw our own title bar
        .transparent(false)
        .resizable(true)
        .skip_taskbar(false)
        .inner_size(620.0, 760.0)
        .min_inner_size(420.0, 520.0)
        .focused(true)
        .initialization_script(&init)
        .build()
        .map(|_| ())
        .map_err(|e| format!("chat window build: {}", e))
}

#[tauri::command]
pub fn close_chat_window(app: AppHandle) -> Result<(), String> {
    if let Some(w) = app.get_webview_window("chat") {
        let _ = w.close();
    }
    Ok(())
}

/// Mint a LiveKit token by calling the bridge server-side. Bypasses CORS
/// (kiosk webview origin is `tauri://localhost`, bridge is
/// `http://127.0.0.1:8765` — the OPTIONS preflight returns 401 with no
/// CORS headers, so the browser aborts the actual POST). Doing the
/// request from Rust avoids the browser CORS check entirely.
///
/// Returns the bridge's JSON response body verbatim so the JS side
/// just JSON.parses it.
#[tauri::command]
pub fn mint_livekit_token(identity: String, room: String) -> Result<String, String> {
    use std::io::{Read, Write};
    use std::net::TcpStream;
    use std::time::Duration;

    let token = read_bridge_token();
    if token.is_empty() {
        return Err("bridge token unavailable".into());
    }
    // Compose JSON body via serde_json so user-supplied identity/room
    // can't break out of the string and inject extra fields.
    let body = serde_json::json!({ "identity": identity, "room": room }).to_string();
    let req = format!(
        "POST /api/livekit/token HTTP/1.1\r\n\
         Host: 127.0.0.1:8765\r\n\
         Content-Type: application/json\r\n\
         Authorization: Bearer {tok}\r\n\
         Content-Length: {len}\r\n\
         Connection: close\r\n\
         \r\n{body}",
        tok = token,
        len = body.len(),
        body = body,
    );
    let mut stream = TcpStream::connect_timeout(
        &"127.0.0.1:8765".parse().unwrap(),
        Duration::from_secs(3),
    )
    .map_err(|e| format!("connect bridge: {}", e))?;
    stream
        .set_read_timeout(Some(Duration::from_secs(5)))
        .ok();
    stream
        .write_all(req.as_bytes())
        .map_err(|e| format!("write: {}", e))?;
    let mut resp = Vec::new();
    stream
        .read_to_end(&mut resp)
        .map_err(|e| format!("read: {}", e))?;
    let resp_str = String::from_utf8_lossy(&resp);
    // Parse status line
    let mut lines = resp_str.lines();
    let status_line = lines.next().unwrap_or("");
    // "HTTP/1.1 200 OK"
    let status_code: u16 = status_line
        .split_whitespace()
        .nth(1)
        .and_then(|s| s.parse().ok())
        .unwrap_or(0);
    if status_code != 200 {
        return Err(format!("bridge returned {}: {}", status_code, status_line));
    }
    // Body is after the first blank line
    let body_start = resp_str
        .find("\r\n\r\n")
        .ok_or("no body delimiter in response")?;
    Ok(resp_str[body_start + 4..].to_string())
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
        enter_kiosk_impl(
            &adapter,
            &mut state,
            monitor_idx,
            Some((pos_x, pos_y, size_w as i32, size_h as i32)),
        )?;
    }

    // 3. Spawn the kiosk window.
    // Note: WebviewWindowBuilder::position / inner_size in this Tauri
    // version accept raw (f64, f64) which Tauri interprets as LOGICAL
    // pixels. On a HiDPI display that means our physical-pixel values
    // would be multiplied by scale_factor → window oversized. Use the
    // builder with a 0,0 placeholder and then set the TYPED PhysicalPosition
    // / PhysicalSize via the post-build setters (which DO accept the
    // typed enum and won't be misinterpreted).
    // Inject the token via both init script + post-build eval AS WELL AS
    // exposing it through the get_bridge_token IPC command. The JSX
    // prefers IPC (timing-bulletproof); the globals are defense-in-depth
    // for any code path that reads window.__JARVIS_LOCAL_API_TOKEN.
    let bridge_token = read_bridge_token();
    let token_js = serde_json::to_string(&bridge_token)
        .unwrap_or_else(|_| "\"\"".to_string());
    let init = format!("window.__JARVIS_LOCAL_API_TOKEN = {};", token_js);

    let result = WebviewWindowBuilder::new(&app, "kiosk", WebviewUrl::App("index.html?route=kiosk".into()))
        .decorations(false)
        .transparent(false)
        .always_on_top(true)
        .focused(true)
        .skip_taskbar(true)
        .resizable(false)
        .title("J.A.R.V.I.S. \u{2014} kiosk")
        .position(0.0, 0.0)
        .inner_size(100.0, 100.0)
        .initialization_script(&init)
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

    // Backup token injection: initialization_script runs pre-page-load
    // (which is the correct timing) but if it fails to attach for any
    // reason we also eval the assignment after build. KioskHUD's token
    // mint runs inside a useEffect on mount; one of these two paths
    // lands before the fetch. Empty string is harmless: bridge will
    // reject and the existing `lk:err(fetch)` overlay shows the user.
    let _ = kiosk_window.eval(&init);
    eprintln!(
        "[kiosk] injected JARVIS_LOCAL_API_TOKEN ({} chars) into kiosk webview",
        bridge_token.len()
    );

    // Set position so the WM knows which monitor this window lives on.
    // Then size. We do NOT call Tauri's set_fullscreen here — on X11 the
    // builder spawned the window at (0,0) (=eDP-1 / laptop on a typical
    // laptop+monitor setup), and set_position/set_fullscreen are both async
    // through GTK→X11→WM. The WM was racing the move and fullscreening on
    // the original monitor, which produced the "click external → kiosk on
    // laptop" swap. Instead we move+fullscreen via wmctrl after a short
    // delay, on a background thread so the main loop isn't blocked.
    let _ = kiosk_window.set_position(PhysicalPosition::<i32>::new(pos_x, pos_y));
    let _ = kiosk_window.set_size(PhysicalSize::<u32>::new(size_w, size_h));

    eprintln!(
        "[kiosk] target monitor_idx={} pos=({},{}) size={}x{} scale={}",
        monitor_idx, pos_x, pos_y, size_w, size_h, scale
    );

    let title = "J.A.R.V.I.S. \u{2014} kiosk".to_string();
    std::thread::spawn(move || {
        // Wait for the window to be mapped by the WM. Empirically ~80ms is
        // enough on XFCE; we belt-and-suspenders with a second move+
        // fullscreen pass after another short delay in case the first
        // raced an unfinished map.
        std::thread::sleep(std::time::Duration::from_millis(90));
        let move_arg = format!("0,{},{},{},{}", pos_x, pos_y, size_w, size_h);
        let _ = std::process::Command::new("wmctrl")
            .args(["-r", &title, "-e", &move_arg])
            .output();
        std::thread::sleep(std::time::Duration::from_millis(60));
        let _ = std::process::Command::new("wmctrl")
            .args(["-r", &title, "-b", "add,fullscreen,above"])
            .output();
        // Second pass — some WMs reset position on fullscreen toggle.
        std::thread::sleep(std::time::Duration::from_millis(80));
        let _ = std::process::Command::new("wmctrl")
            .args(["-r", &title, "-e", &move_arg])
            .output();
    });

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
                ..Default::default()
            },
            WindowInfo {
                id: "0x200".into(),
                wm_class: "Firefox".into(),
                title: "tabs".into(),
                ..Default::default()
            },
            WindowInfo {
                id: "0x300".into(),
                wm_class: "Code".into(),
                title: "vscode".into(),
                ..Default::default()
            },
        ]
    }

    #[test]
    fn enter_minimizes_non_jarvis_windows() {
        let mock = MockWmctrl::new(windows_fixture());
        let mut state: Option<KioskSnapshot> = None;
        let result = enter_kiosk_impl(&mock, &mut state, 0, None);
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
        let result = enter_kiosk_impl(&mock, &mut state, 0, None);
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
        let result = enter_kiosk_impl(&mock, &mut state, 2, None);
        assert!(result.is_ok());
        let snap = state.as_ref().unwrap();
        assert!(snap.minimized_ids.is_empty());
        assert_eq!(snap.monitor_idx, 2);
    }
}
