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
        Ok(parse_wmctrl_lgx(&text))
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

/// Parse `wmctrl -lGx` output into `WindowInfo`s.
///
/// Columns: id, desktop, x, y, w, h, wm_class, machine, title. wmctrl
/// right-aligns the numeric columns and pads with MULTIPLE spaces, so the
/// previous `splitn(9, char::is_whitespace).filter(non-empty)` was wrong:
/// `splitn` counts the empty strings between padding spaces toward its
/// 9-piece budget, so a padded line collapsed to ~5 fields and got dropped
/// by the `len() < 9` guard — silently hiding the kiosk window (and every
/// other window) from `find_kiosk_xid` and the minimize logic. We instead
/// peel the first 8 whitespace-delimited fields and keep the remainder as
/// the title (which may contain spaces, e.g. "J.A.R.V.I.S. — kiosk").
fn parse_wmctrl_lgx(text: &str) -> Vec<WindowInfo> {
    let mut windows = Vec::new();
    for line in text.lines() {
        let mut rest = line;
        let mut fields: [&str; 8] = [""; 8];
        let mut complete = true;
        for f in fields.iter_mut() {
            rest = rest.trim_start();
            match rest.find(char::is_whitespace) {
                Some(i) => {
                    *f = &rest[..i];
                    rest = &rest[i..];
                }
                None => {
                    complete = false;
                    break;
                }
            }
        }
        if !complete {
            continue; // fewer than 8 fields + a title → not a window row
        }
        let id = fields[0].to_string();
        let x = fields[2].parse::<i32>().unwrap_or(0);
        let y = fields[3].parse::<i32>().unwrap_or(0);
        let w = fields[4].parse::<i32>().unwrap_or(0);
        let h = fields[5].parse::<i32>().unwrap_or(0);
        let wmc = fields[6].to_string();
        // fields[1] = desktop, fields[7] = machine — unused.
        let title = rest.trim().to_string();
        if id.is_empty() || wmc.is_empty() {
            continue;
        }
        windows.push(WindowInfo { id, wm_class: wmc, title, x, y, w, h });
    }
    windows
}

/// Parse a wmctrl/X11 window id (e.g. "0x06400003") into a numeric XID.
/// Tolerates surrounding whitespace and a missing "0x" prefix.
fn parse_xid(s: &str) -> Option<u64> {
    let s = s.trim();
    let hex = s
        .strip_prefix("0x")
        .or_else(|| s.strip_prefix("0X"))
        .unwrap_or(s);
    u64::from_str_radix(hex, 16).ok()
}

/// Pick the monitor whose top-left origin best matches `(target_x, target_y)`.
/// `monitors` entries are `(x, y, w, h)` in RandR/Xinerama index order — the
/// SAME index space that `_NET_WM_FULLSCREEN_MONITORS` expects. Prefers an
/// exact origin match; otherwise falls back to the closest origin by Manhattan
/// distance (guards against logical-vs-physical rounding between Tauri's
/// reported position and the RandR geometry). Returns the index into
/// `monitors`, or None if the slice is empty.
fn pick_monitor_index(
    monitors: &[(i32, i32, i32, i32)],
    target_x: i32,
    target_y: i32,
) -> Option<usize> {
    if monitors.is_empty() {
        return None;
    }
    if let Some(i) = monitors
        .iter()
        .position(|&(x, y, _, _)| x == target_x && y == target_y)
    {
        return Some(i);
    }
    monitors
        .iter()
        .enumerate()
        .min_by_key(|(_, &(x, y, _, _))| {
            // Cast to i64 before subtracting so distant origins can't overflow i32.
            (x as i64 - target_x as i64).abs() + (y as i64 - target_y as i64).abs()
        })
        .map(|(i, _)| i)
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

// ─── Kiosk window placement (X11 EWMH) ─────────────────────────────────────

/// Find the freshly-spawned kiosk window's X11 id via the wmctrl adapter.
/// Matches the kiosk title specifically so we never pick up the main overlay
/// ("J.A.R.V.I.S.") or the chat window ("J.A.R.V.I.S. — Chat").
#[cfg(target_os = "linux")]
fn find_kiosk_xid<A: WmctrlAdapter>(adapter: &A) -> Option<u64> {
    let windows = adapter.list_visible_windows().ok()?;
    windows
        .iter()
        .find(|w| w.title.contains("J.A.R.V.I.S. \u{2014} kiosk") || w.title == "kiosk")
        .and_then(|w| parse_xid(&w.id))
}

/// Does `window`'s `property` (an array of Atoms) contain `needle`?
/// Used to confirm the WM advertises `_NET_WM_FULLSCREEN_MONITORS` in
/// `_NET_SUPPORTED` before we rely on it.
#[cfg(target_os = "linux")]
fn x11_atom_in_property(
    xlib: &x11_dl::xlib::Xlib,
    display: *mut x11_dl::xlib::Display,
    window: std::os::raw::c_ulong,
    property: std::os::raw::c_ulong,
    needle: std::os::raw::c_ulong,
) -> bool {
    use std::os::raw::{c_int, c_uchar, c_ulong};
    use std::ptr;

    let mut actual_type: c_ulong = 0;
    let mut actual_format: c_int = 0;
    let mut nitems: c_ulong = 0;
    let mut bytes_after: c_ulong = 0;
    let mut prop: *mut c_uchar = ptr::null_mut();
    // Success == 0. Request up to 4096 longs (plenty for _NET_SUPPORTED).
    let status = unsafe {
        (xlib.XGetWindowProperty)(
            display,
            window,
            property,
            0,
            4096,
            x11_dl::xlib::False,
            x11_dl::xlib::AnyPropertyType as c_ulong,
            &mut actual_type,
            &mut actual_format,
            &mut nitems,
            &mut bytes_after,
            &mut prop,
        )
    };
    if status != 0 || prop.is_null() || actual_format != 32 {
        if !prop.is_null() {
            unsafe { (xlib.XFree)(prop as *mut _) };
        }
        return false;
    }
    let atoms = unsafe { std::slice::from_raw_parts(prop as *const c_ulong, nitems as usize) };
    let found = atoms.iter().any(|&a| a == needle);
    unsafe { (xlib.XFree)(prop as *mut _) };
    found
}

/// Fullscreen the window `xid` on the monitor whose origin matches
/// `(target_x, target_y)`, using the EWMH `_NET_WM_FULLSCREEN_MONITORS`
/// primitive. This is race-free: the WM spans the EXACT RandR monitor
/// regardless of where the window currently sits or what `_NET_WORKAREA`
/// says — which is why it fixes the "select external → kiosk on laptop"
/// bug that move-then-fullscreen could not. On a stacked multi-monitor
/// layout xfwm4 refuses to move a window onto a monitor that sits outside
/// the work area, then fullscreens it on the laptop. Verified live on
/// xfwm4 + stacked dual-monitor 2026-05-29.
///
/// Returns Ok(monitor_index_used) or Err (caller falls back to wmctrl).
#[cfg(target_os = "linux")]
fn x11_fullscreen_on_monitor(xid: u64, target_x: i32, target_y: i32) -> Result<i32, String> {
    use std::os::raw::{c_int, c_long, c_ulong};
    use std::ptr;

    let xlib = x11_dl::xlib::Xlib::open().map_err(|e| format!("xlib open: {:?}", e))?;
    let xrandr = x11_dl::xrandr::Xrandr::open().map_err(|e| format!("xrandr open: {:?}", e))?;

    let display = unsafe { (xlib.XOpenDisplay)(ptr::null()) };
    if display.is_null() {
        return Err("XOpenDisplay returned null".into());
    }

    // All display-using work happens in this inner closure so we can close the
    // display exactly once afterwards, on every (Ok/Err) path.
    let inner = || -> Result<i32, String> {
        let root = unsafe { (xlib.XDefaultRootWindow)(display) };

        // 1. Enumerate RandR monitors. Their index order is the index space
        //    that `_NET_WM_FULLSCREEN_MONITORS` and `xrandr --listmonitors`
        //    both use.
        let mut n: c_int = 0;
        let mons_ptr = unsafe { (xrandr.XRRGetMonitors)(display, root, x11_dl::xlib::True, &mut n) };
        if mons_ptr.is_null() || n <= 0 {
            return Err("XRRGetMonitors returned none".into());
        }
        let rects: Vec<(i32, i32, i32, i32)> = {
            let mons = unsafe { std::slice::from_raw_parts(mons_ptr, n as usize) };
            let v = mons
                .iter()
                .map(|m| (m.x as i32, m.y as i32, m.width as i32, m.height as i32))
                .collect();
            unsafe { (xrandr.XRRFreeMonitors)(mons_ptr) };
            v
        };
        let idx = pick_monitor_index(&rects, target_x, target_y)
            .ok_or_else(|| "no monitor matched target origin".to_string())?
            as c_long;

        // 2. Intern the atoms we need.
        let intern = |name: &str| -> c_ulong {
            let c = std::ffi::CString::new(name).unwrap();
            unsafe { (xlib.XInternAtom)(display, c.as_ptr(), x11_dl::xlib::False) }
        };
        let net_supported = intern("_NET_SUPPORTED");
        let fs_monitors = intern("_NET_WM_FULLSCREEN_MONITORS");
        let net_wm_state = intern("_NET_WM_STATE");
        let state_fullscreen = intern("_NET_WM_STATE_FULLSCREEN");
        let state_above = intern("_NET_WM_STATE_ABOVE");

        // 3. Bail to the wmctrl fallback if the WM doesn't advertise the hint
        //    (sending it would be a silent no-op).
        if !x11_atom_in_property(&xlib, display, root, net_supported, fs_monitors) {
            return Err("_NET_WM_FULLSCREEN_MONITORS not in _NET_SUPPORTED".into());
        }

        // 4. Send a format-32 ClientMessage to the root window (so the WM,
        //    not the client, applies it).
        let send = |message_type: c_ulong, longs: [c_long; 5]| {
            let mut data = x11_dl::xlib::ClientMessageData::new();
            for (i, v) in longs.iter().enumerate() {
                data.set_long(i, *v);
            }
            let mut ev = x11_dl::xlib::XEvent {
                client_message: x11_dl::xlib::XClientMessageEvent {
                    type_: x11_dl::xlib::ClientMessage,
                    serial: 0,
                    send_event: x11_dl::xlib::True,
                    display,
                    window: xid as c_ulong,
                    message_type,
                    format: 32,
                    data,
                },
            };
            unsafe {
                (xlib.XSendEvent)(
                    display,
                    root,
                    x11_dl::xlib::False,
                    x11_dl::xlib::SubstructureRedirectMask | x11_dl::xlib::SubstructureNotifyMask,
                    &mut ev,
                );
                (xlib.XFlush)(display);
            }
        };

        // Pin the fullscreen monitor span: l[0..4] = top/bottom/left/right
        // monitor index (same monitor on all four = single-monitor span),
        // l[4] = source indication (1 = application).
        send(fs_monitors, [idx, idx, idx, idx, 1]);
        std::thread::sleep(std::time::Duration::from_millis(40));
        // _NET_WM_STATE: l[0] = _NET_WM_STATE_ADD(1), l[1] = property,
        // l[2] = second property (0), l[3] = source indication (1).
        send(net_wm_state, [1, state_fullscreen as c_long, 0, 1, 0]);
        send(net_wm_state, [1, state_above as c_long, 0, 1, 0]);
        unsafe { (xlib.XFlush)(display) };

        Ok(idx as i32)
    };

    let result = inner();
    unsafe { (xlib.XCloseDisplay)(display) };
    result
}

/// Background-thread worker: fullscreen the freshly-spawned kiosk window on
/// the monitor whose origin is `(target_x, target_y)`. Primary path is the
/// EWMH `_NET_WM_FULLSCREEN_MONITORS` client message; falls back to the legacy
/// wmctrl move+fullscreen (also the only path on non-Linux). `w`/`h` are used
/// only by the fallback.
fn place_kiosk_fullscreen(target_x: i32, target_y: i32, w: u32, h: u32) {
    let title = "J.A.R.V.I.S. \u{2014} kiosk";

    #[cfg(target_os = "linux")]
    {
        // Wait for the WM to map + register the kiosk window, then grab its
        // XID (≤ ~560 ms; the window is brand-new).
        let adapter = RealWmctrl;
        let mut xid = None;
        for _ in 0..8 {
            std::thread::sleep(std::time::Duration::from_millis(70));
            if let Some(id) = find_kiosk_xid(&adapter) {
                xid = Some(id);
                break;
            }
        }
        if let Some(xid) = xid {
            match x11_fullscreen_on_monitor(xid, target_x, target_y) {
                Ok(idx) => {
                    eprintln!(
                        "[kiosk] EWMH fullscreen on RandR monitor {} (xid {:#x})",
                        idx, xid
                    );
                    return;
                }
                Err(e) => eprintln!(
                    "[kiosk] EWMH fullscreen failed ({}); falling back to wmctrl",
                    e
                ),
            }
        } else {
            eprintln!("[kiosk] kiosk window XID not found; falling back to wmctrl");
        }
    }

    // Fallback: legacy wmctrl move+fullscreen. Known to mis-place on stacked
    // multi-monitor layouts (the bug above), but better than no fullscreen on
    // a WM that lacks `_NET_WM_FULLSCREEN_MONITORS`.
    let move_arg = format!("0,{},{},{},{}", target_x, target_y, w, h);
    let _ = std::process::Command::new("wmctrl")
        .args(["-r", title, "-e", &move_arg])
        .output();
    std::thread::sleep(std::time::Duration::from_millis(60));
    let _ = std::process::Command::new("wmctrl")
        .args(["-r", title, "-b", "add,fullscreen,above"])
        .output();
    std::thread::sleep(std::time::Duration::from_millis(80));
    let _ = std::process::Command::new("wmctrl")
        .args(["-r", title, "-e", &move_arg])
        .output();
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

    // Give the WM an initial position hint (harmless; the authoritative
    // placement happens via EWMH below). set_fullscreen is deliberately NOT
    // called here — on X11 it dispatches async through GTK→X11→WM and lands
    // on whichever monitor the window currently sits on, which is the laptop:
    // the builder spawns at (0,0) and xfwm4 constrains placement into
    // `_NET_WORKAREA`, which on a stacked multi-monitor layout EXCLUDES the
    // external display. That was the "select external → kiosk on laptop" bug.
    let _ = kiosk_window.set_position(PhysicalPosition::<i32>::new(pos_x, pos_y));
    let _ = kiosk_window.set_size(PhysicalSize::<u32>::new(size_w, size_h));

    eprintln!(
        "[kiosk] target monitor_idx={} pos=({},{}) size={}x{} scale={}",
        monitor_idx, pos_x, pos_y, size_w, size_h, scale
    );

    // Fullscreen on the SELECTED monitor on a background thread (so the main
    // loop isn't blocked while we wait for the WM to map the window). Primary
    // path is the EWMH `_NET_WM_FULLSCREEN_MONITORS` client message — race-free
    // because the WM spans the exact RandR monitor regardless of the window's
    // current position or the work area. Falls back to wmctrl if unavailable.
    std::thread::spawn(move || {
        place_kiosk_fullscreen(pos_x, pos_y, size_w, size_h);
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
    fn parse_wmctrl_lgx_extracts_padded_lines_and_spaced_titles() {
        // EXACT lines captured live (wmctrl right-aligns numerics with multiple
        // spaces). The old splitn(9) parser dropped these as len() < 9.
        let text = "\
0x0640089d  0 0    35   200  200  jarvis-desktop.Jarvis-desktop  Moon J.A.R.V.I.S. \u{2014} kiosk
0x05800004  0 841  2195 2560 1565 code.Code             Moon jarvis - Visual Studio Code
0x06400003  0 841  2195 2560 1600 jarvis-desktop.Jarvis-desktop  Moon J.A.R.V.I.S.";
        let ws = parse_wmctrl_lgx(text);
        assert_eq!(ws.len(), 3, "all three rows must parse");

        let kiosk = &ws[0];
        assert_eq!(kiosk.id, "0x0640089d");
        assert_eq!(kiosk.wm_class, "jarvis-desktop.Jarvis-desktop");
        assert_eq!(kiosk.title, "J.A.R.V.I.S. \u{2014} kiosk"); // title keeps spaces + em-dash
        assert_eq!((kiosk.x, kiosk.y, kiosk.w, kiosk.h), (0, 35, 200, 200));

        // Title with embedded spaces and a hyphen is preserved intact.
        assert_eq!(ws[1].title, "jarvis - Visual Studio Code");
        assert_eq!((ws[1].x, ws[1].y, ws[1].w, ws[1].h), (841, 2195, 2560, 1565));

        // The main overlay must NOT be mistaken for the kiosk.
        assert_eq!(ws[2].title, "J.A.R.V.I.S.");

        // End-to-end: find_kiosk_xid picks the kiosk, not the main overlay.
        struct Stub(Vec<WindowInfo>);
        impl WmctrlAdapter for Stub {
            fn list_visible_windows(&self) -> Result<Vec<WindowInfo>, WmctrlError> {
                Ok(self.0.clone())
            }
            fn minimize(&self, _: &str) -> Result<(), WmctrlError> { Ok(()) }
            fn unminimize(&self, _: &str) -> Result<(), WmctrlError> { Ok(()) }
        }
        assert_eq!(find_kiosk_xid(&Stub(ws)), Some(0x0640_089d));
    }

    #[test]
    fn parse_wmctrl_lgx_skips_garbage_lines() {
        assert!(parse_wmctrl_lgx("").is_empty());
        assert!(parse_wmctrl_lgx("too few fields here\n").is_empty());
    }

    #[test]
    fn parse_xid_handles_hex_forms() {
        assert_eq!(parse_xid("0x06400003"), Some(0x0640_0003));
        assert_eq!(parse_xid("  0x05800004 "), Some(0x0580_0004));
        assert_eq!(parse_xid("06400003"), Some(0x0640_0003)); // no 0x prefix
        assert_eq!(parse_xid("nothex"), None);
        assert_eq!(parse_xid(""), None);
    }

    #[test]
    fn pick_monitor_index_exact_origin_match() {
        // Live stacked layout: idx0 = laptop @ (841,2160), idx1 = external @ (0,0).
        let mons = vec![(841, 2160, 2560, 1600), (0, 0, 3840, 2160)];
        assert_eq!(pick_monitor_index(&mons, 0, 0), Some(1)); // external
        assert_eq!(pick_monitor_index(&mons, 841, 2160), Some(0)); // laptop
    }

    #[test]
    fn pick_monitor_index_falls_back_to_closest_origin() {
        let mons = vec![(841, 2160, 2560, 1600), (0, 0, 3840, 2160)];
        // Slight offset (e.g. logical-vs-physical rounding) still resolves right.
        assert_eq!(pick_monitor_index(&mons, 3, 2), Some(1));
        assert_eq!(pick_monitor_index(&mons, 840, 2161), Some(0));
    }

    #[test]
    fn pick_monitor_index_empty_is_none() {
        assert_eq!(pick_monitor_index(&[], 0, 0), None);
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn find_kiosk_xid_matches_kiosk_title_only() {
        let mock = MockWmctrl::new(vec![
            WindowInfo {
                id: "0x06400003".into(),
                wm_class: "jarvis-desktop.Jarvis-desktop".into(),
                title: "J.A.R.V.I.S.".into(), // main overlay — must NOT match
                ..Default::default()
            },
            WindowInfo {
                id: "0x06400099".into(),
                wm_class: "jarvis-desktop.Jarvis-desktop".into(),
                title: "J.A.R.V.I.S. \u{2014} kiosk".into(), // the kiosk
                ..Default::default()
            },
        ]);
        assert_eq!(find_kiosk_xid(&mock), Some(0x0640_0099));
    }

    /// Live end-to-end check of the PRODUCTION `x11_fullscreen_on_monitor`
    /// against the real WM. Targets the external monitor origin (0,0) — the
    /// exact case that used to land on the laptop. Ignored by default (needs
    /// a live X11 display + WM; briefly flashes a window). Run with:
    ///   cargo test live_ewmh_fullscreen -- --ignored --nocapture
    #[cfg(target_os = "linux")]
    #[test]
    #[ignore = "live: needs X11 display + WM; flashes a window on the external monitor"]
    fn live_ewmh_fullscreen_lands_on_target_monitor() {
        use std::os::raw::{c_int, c_uint, c_ulong};
        use std::ptr;

        let xlib = x11_dl::xlib::Xlib::open().expect("xlib open");
        let d = unsafe { (xlib.XOpenDisplay)(ptr::null()) };
        assert!(!d.is_null(), "no X display");
        let screen = unsafe { (xlib.XDefaultScreen)(d) };
        let root = unsafe { (xlib.XDefaultRootWindow)(d) };
        let black = unsafe { (xlib.XBlackPixel)(d, screen) };
        let win = unsafe { (xlib.XCreateSimpleWindow)(d, root, 60, 60, 400, 300, 0, black, black) };
        let title = std::ffi::CString::new("J.A.R.V.I.S. \u{2014} kiosk").unwrap();
        unsafe {
            (xlib.XStoreName)(d, win, title.as_ptr());
            (xlib.XMapWindow)(d, win);
            (xlib.XSync)(d, x11_dl::xlib::False);
        }
        std::thread::sleep(std::time::Duration::from_millis(700));

        // Target the EXTERNAL monitor origin (0,0).
        let idx = x11_fullscreen_on_monitor(win, 0, 0).expect("fullscreen call");
        std::thread::sleep(std::time::Duration::from_millis(600));

        let (mut rx, mut ry, mut child): (c_int, c_int, c_ulong) = (0, 0, 0);
        unsafe {
            (xlib.XTranslateCoordinates)(d, win, root, 0, 0, &mut rx, &mut ry, &mut child);
        }
        let (mut gx, mut gy): (c_int, c_int) = (0, 0);
        let (mut gw, mut gh, mut gb, mut gd): (c_uint, c_uint, c_uint, c_uint) = (0, 0, 0, 0);
        let mut rret: c_ulong = 0;
        unsafe {
            (xlib.XGetGeometry)(
                d, win, &mut rret, &mut gx, &mut gy, &mut gw, &mut gh, &mut gb, &mut gd,
            );
        }
        let cx = rx + gw as c_int / 2;
        let cy = ry + gh as c_int / 2;
        unsafe {
            (xlib.XDestroyWindow)(d, win);
            (xlib.XFlush)(d);
            (xlib.XCloseDisplay)(d);
        }

        eprintln!(
            "[live] monitor_idx={} root_pos=({},{}) size={}x{} center=({},{})",
            idx, rx, ry, gw, gh, cx, cy
        );
        // External monitor DP-1 is (0,0) 3840x2160 — center must lie inside it,
        // NOT on the laptop (which starts at x=841, y=2160).
        assert!(
            cx >= 0 && cx < 3840 && cy >= 0 && cy < 2160,
            "kiosk did NOT land on the external monitor: center=({},{})",
            cx,
            cy
        );
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
