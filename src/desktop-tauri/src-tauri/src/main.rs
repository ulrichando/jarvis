#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::sync::{Arc, Mutex};
use tauri::{
    Manager, State, WebviewWindow, PhysicalSize, PhysicalPosition,
    image::Image,
    menu::{MenuBuilder, MenuItemBuilder, PredefinedMenuItem},
    tray::{TrayIcon, TrayIconBuilder},
    Emitter, Wry,
};

/// Shared chat-open state between the tray menu and JS. React calls
/// `set_chat_state` from `openChat`/`closeChat` so the Rust-side toggle
/// never drifts out of sync with what the user actually sees.
struct ChatOpen(Arc<Mutex<bool>>);

/// Current panel bounds in viewport coordinates (x, y, w, h). React pushes
/// this on open / drag-end / resize-end so the hotspot polling thread knows
/// which screen region should capture clicks. (0,0,0,0) = no panel visible.
#[derive(Clone, Copy, Default)]
struct PanelRectData { x: i32, y: i32, w: i32, h: i32 }
struct PanelRect(Arc<Mutex<PanelRectData>>);

/// Handle to the system-tray icon, kept alive in Tauri state so the
/// `set_tray_state` command can swap colours based on the app state.
struct TrayHandle(Mutex<Option<TrayIcon<Wry>>>);

/// Build a 32×32 RGBA buffer of a filled circle in the given colour with
/// 1px anti-alias edge. Tauri's `Image::new` takes the raw RGBA bytes.
const TRAY_SIZE: u32 = 32;
fn make_tray_rgba(r: u8, g: u8, b: u8) -> Vec<u8> {
    let size = TRAY_SIZE;
    let mut buf = vec![0u8; (size * size * 4) as usize];
    let center = size as f32 / 2.0;
    let radius = center - 1.5;
    for y in 0..size {
        for x in 0..size {
            let dx = x as f32 + 0.5 - center;
            let dy = y as f32 + 0.5 - center;
            let d  = (dx * dx + dy * dy).sqrt();
            let alpha: u8 = if d <= radius {
                255
            } else if d <= radius + 1.0 {
                ((radius + 1.0 - d) * 255.0).clamp(0.0, 255.0) as u8
            } else {
                0
            };
            let i = ((y * size + x) * 4) as usize;
            buf[i    ] = r;
            buf[i + 1] = g;
            buf[i + 2] = b;
            buf[i + 3] = alpha;
        }
    }
    buf
}
fn tray_image_for(state: &str) -> Vec<u8> {
    // Colours tuned to read clearly against dark + light panels on XFCE.
    match state {
        "talking"  => make_tray_rgba(68, 147, 248),   // blue
        "thinking" => make_tray_rgba(250, 180, 50),   // gold / amber
        "offline"  => make_tray_rgba(239, 68, 68),    // red
        _          => make_tray_rgba(63, 185, 80),    // idle / listening — green
    }
}

// ── Helpers ────────────────────────────────────────────────────────────────

/// Get cursor position in physical screen coordinates via xdotool.
fn cursor_position() -> (i32, i32) {
    let Ok(out) = std::process::Command::new("xdotool")
        .args(["getmouselocation", "--shell"])
        .output()
    else { return (0, 0) };
    let text = String::from_utf8_lossy(&out.stdout);
    let mut cx = 0i32;
    let mut cy = 0i32;
    for line in text.lines() {
        if let Some(v) = line.strip_prefix("X=") { cx = v.trim().parse().unwrap_or(0); }
        if let Some(v) = line.strip_prefix("Y=") { cy = v.trim().parse().unwrap_or(0); }
    }
    (cx, cy)
}

/// Move window to whichever monitor the cursor is on.
fn snap_to_cursor_monitor(window: &WebviewWindow) {
    let (cx, cy) = cursor_position();
    let Ok(monitors) = window.available_monitors() else { return };
    let target = monitors.iter().find(|m| {
        let p = m.position();
        let s = m.size();
        cx >= p.x && cx < p.x + s.width as i32 &&
        cy >= p.y && cy < p.y + s.height as i32
    }).or_else(|| monitors.first());

    if let Some(mon) = target {
        let size = mon.size();
        let pos  = mon.position();
        println!("[JARVIS] Snap to monitor at {}x{}+{}+{} (cursor {},{} )", size.width, size.height, pos.x, pos.y, cx, cy);
        let _ = window.set_size(PhysicalSize::new(size.width, size.height));
        let _ = window.set_position(PhysicalPosition::new(pos.x, pos.y));
    }
}

/// Raise + focus the X11 window via xdotool (bypasses WM focus policies).
fn xdotool_raise(win_name: &str) {
    let _ = std::process::Command::new("xdotool")
        .args(["search", "--name", win_name, "windowraise", "windowfocus", "--sync"])
        .spawn();
}

// ── Tauri commands called from JS ──────────────────────────────────────────

#[tauri::command]
fn set_click_through(window: WebviewWindow, enabled: bool) -> Result<(), String> {
    window.set_ignore_cursor_events(enabled).map_err(|e| e.to_string())?;
    println!("[JARVIS] click-through: {}", if enabled { "ON" } else { "OFF" });
    Ok(())
}

#[tauri::command]
fn set_layer(window: WebviewWindow, above: bool) -> Result<(), String> {
    window.set_always_on_top(above).map_err(|e| e.to_string())?;
    println!("[JARVIS] layer: {}", if above { "ABOVE" } else { "normal" });
    Ok(())
}

/// Keep the Rust-side chat_open mutex in sync with React. Called from JS
/// whenever openChat/closeChat runs — prevents the tray toggle from
/// needing two clicks after the panel is closed via its X button / Esc.
#[tauri::command]
fn set_chat_state(open: bool, state: State<ChatOpen>) -> Result<(), String> {
    let mut guard = state.0.lock().map_err(|e| e.to_string())?;
    *guard = open;
    println!("[JARVIS] chat state synced from JS: {}", if open { "open" } else { "closed" });
    Ok(())
}

/// Report the panel's current bounds so the hotspot poller can enable
/// click-through only when the cursor is outside the panel. Viewport
/// coordinates (getBoundingClientRect).
#[tauri::command]
fn set_panel_rect(x: i32, y: i32, w: i32, h: i32, state: State<PanelRect>) -> Result<(), String> {
    let mut guard = state.0.lock().map_err(|e| e.to_string())?;
    *guard = PanelRectData { x, y, w, h };
    Ok(())
}

/// Set the system-tray icon colour to reflect the current app state.
/// Accepted values: "idle" | "listening" | "talking" | "thinking" | "offline".
/// "idle" and "listening" both render green (the mic is live either way).
#[tauri::command]
fn set_tray_state(state: &str, tray: State<TrayHandle>) -> Result<(), String> {
    let rgba = tray_image_for(state);
    let image = Image::new_owned(rgba, TRAY_SIZE, TRAY_SIZE);
    let guard = tray.0.lock().map_err(|e| e.to_string())?;
    if let Some(t) = guard.as_ref() {
        t.set_icon(Some(image)).map_err(|e| e.to_string())?;
    }
    Ok(())
}

#[tauri::command]
fn get_primary_monitor_info(window: WebviewWindow) -> Result<serde_json::Value, String> {
    let monitor = window
        .primary_monitor()
        .map_err(|e| e.to_string())?
        .ok_or_else(|| "No primary monitor".to_string())?;
    Ok(serde_json::json!({
        "width":  monitor.size().width,
        "height": monitor.size().height,
        "x":      monitor.position().x,
        "y":      monitor.position().y,
        "scale":  monitor.scale_factor(),
    }))
}

// ── Entry point ────────────────────────────────────────────────────────────

fn main() {
    // Shared flag: is the chat panel currently open? Accessible from both
    // the tray menu (via clone below) and JS (via the ChatOpen state).
    let chat_open: Arc<Mutex<bool>> = Arc::new(Mutex::new(false));
    let chat_open_state = chat_open.clone();
    let chat_open_poll  = chat_open.clone();

    let panel_rect: Arc<Mutex<PanelRectData>> = Arc::new(Mutex::new(PanelRectData::default()));
    let panel_rect_state = panel_rect.clone();
    let panel_rect_poll  = panel_rect.clone();

    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(
            tauri_plugin_global_shortcut::Builder::new()
                .with_handler(|app, shortcut, event| {
                    use tauri_plugin_global_shortcut::ShortcutState;
                    if event.state() != ShortcutState::Pressed { return }
                    println!("[JARVIS] global shortcut fired: {:?}", shortcut);
                    if let Some(w) = app.get_webview_window("main") {
                        let _ = w.emit("tray-toggle-chat", ());
                    }
                })
                .build(),
        )
        .manage(ChatOpen(chat_open_state))
        .manage(PanelRect(panel_rect_state))
        .manage(TrayHandle(Mutex::new(None)))
        .setup(move |app| {
            let window = app.get_webview_window("main").unwrap();
            let window_for_poll = window.clone();

            // Full-screen transparent overlay on primary monitor.
            if let Ok(Some(monitor)) = window.primary_monitor() {
                let size = monitor.size();
                let pos  = monitor.position();
                println!("[JARVIS] Target monitor (primary): {}x{}+{}+{}", size.width, size.height, pos.x, pos.y);
                let _ = window.set_size(PhysicalSize::new(size.width, size.height));
                let _ = window.set_position(PhysicalPosition::new(pos.x, pos.y));
            }

            // Transparent overlay: click-through so desktop stays usable.
            let _ = window.set_ignore_cursor_events(true);
            let _ = window.show();

            // Enable mic / media stream in the WebKit2GTK webview and
            // auto-grant permission requests so getUserMedia works for the
            // always-listening voice loop.
            #[cfg(target_os = "linux")]
            {
                use webkit2gtk::{WebViewExt, SettingsExt, PermissionRequestExt};
                let _ = window.with_webview(|webview| {
                    let wv = webview.inner();
                    if let Some(settings) = WebViewExt::settings(&wv) {
                        settings.set_enable_media_stream(true);
                        settings.set_enable_webrtc(true);
                        settings.set_enable_mediasource(true);
                        // Let TTS audio autoplay without a prior user gesture.
                        settings.set_media_playback_requires_user_gesture(false);
                    }
                    wv.connect_permission_request(|_wv, req| {
                        req.allow();
                        true
                    });
                });
            }

            // ── Global hotkey ──
            // Ctrl+Shift+Space summons/dismisses the chat panel (Ctrl+Space
            // alone conflicts with XFCE/IBus input-method switcher).
            // Emits tray-toggle-chat which the React side treats like
            // any other tray click.
            {
                use tauri_plugin_global_shortcut::{Code, Modifiers, Shortcut, GlobalShortcutExt};
                let handle = app.handle();
                let mods = Modifiers::CONTROL | Modifiers::SHIFT;
                let sc = Shortcut::new(Some(mods), Code::Space);
                match handle.global_shortcut().register(sc) {
                    Ok(_)  => println!("[JARVIS] global shortcut registered: Ctrl+Shift+Space"),
                    Err(e) => eprintln!("[JARVIS] failed to register Ctrl+Shift+Space: {:?}", e),
                }
            }

            // ── System tray ──
            let chat_item    = MenuItemBuilder::with_id("open_chat",    "Open Chat Panel").build(app)?;
            let mute_item    = MenuItemBuilder::with_id("mute",         "Mute / Unmute Voice").build(app)?;
            let sep1         = PredefinedMenuItem::separator(app)?;
            let browser_item = MenuItemBuilder::with_id("open_browser", "Open in Browser").build(app)?;
            let sep2         = PredefinedMenuItem::separator(app)?;
            let quit_item    = MenuItemBuilder::with_id("quit",         "Quit JARVIS").build(app)?;

            let menu = MenuBuilder::new(app)
                .item(&chat_item)
                .item(&mute_item)
                .item(&sep1)
                .item(&browser_item)
                .item(&sep2)
                .item(&quit_item)
                .build()?;

            let chat_open_tray = Arc::clone(&chat_open);

            // Start the tray on the green "idle" indicator — React will
            // push state updates via set_tray_state as soon as the webview
            // boots and the WS reports status.
            let idle_rgba = tray_image_for("idle");
            let idle_icon = Image::new_owned(idle_rgba, TRAY_SIZE, TRAY_SIZE);
            let tray = TrayIconBuilder::new()
                .icon(idle_icon)
                .menu(&menu)
                .tooltip("J.A.R.V.I.S.")
                .on_menu_event(move |app, event| {
                    match event.id().as_ref() {
                        "open_chat" => {
                            let Some(w) = app.get_webview_window("main") else { return };
                            let mut open = chat_open_tray.lock().unwrap();
                            if *open {
                                *open = false;
                                let _ = w.set_always_on_top(false);
                                let _ = w.set_ignore_cursor_events(true);
                                let _ = w.emit("tray-close-chat", ());
                                println!("[JARVIS] Chat closed via tray");
                            } else {
                                *open = true;
                                snap_to_cursor_monitor(&w);
                                let _ = w.show();
                                let _ = w.set_always_on_top(true);
                                let _ = w.set_ignore_cursor_events(false);
                                let _ = w.set_focus();
                                let _ = w.emit("tray-open-chat", ());
                                xdotool_raise("J.A.R.V.I.S.");
                                println!("[JARVIS] Chat opened via tray");
                            }
                        }
                        "mute" => {
                            // Call mute API directly from Rust (avoids Tauri CORS restrictions on fetch)
                            let _ = std::process::Command::new("curl")
                                .args(["-s", "-X", "POST", "http://127.0.0.1:8765/api/mute"])
                                .spawn();
                            if let Some(w) = app.get_webview_window("main") {
                                let _ = w.emit("tray-toggle-mute", ());
                            }
                        }
                        "open_browser" => {
                            let _ = std::process::Command::new("xdg-open")
                                .arg("http://127.0.0.1:8765/")
                                .spawn();
                        }
                        "quit" => app.exit(0),
                        _ => {}
                    }
                })
                .build(app)?;

            // Stash the tray handle in state so set_tray_state can update
            // the icon. Previously std::mem::forget leaked it, which
            // prevented ever changing the colour.
            {
                let tray_state: State<TrayHandle> = app.state();
                let mut g = tray_state.0.lock().unwrap();
                *g = Some(tray);
            }

            // ── Hotspot polling thread ──
            // Per-region click-through: while the chat panel is open, poll
            // the cursor position ~30×/s via X11 and flip click-through
            // based on whether the cursor is inside the panel rectangle.
            // Inside panel → window captures clicks (panel is interactive).
            // Outside panel → window is click-through so empty area passes
            // clicks to the desktop / other windows underneath.
            #[cfg(target_os = "linux")]
            {
                let chat_open_poll = chat_open_poll.clone();
                let panel_rect_poll = panel_rect_poll.clone();
                let win = window_for_poll.clone();
                std::thread::spawn(move || {
                    let xlib = match x11_dl::xlib::Xlib::open() {
                        Ok(x) => x,
                        Err(e) => {
                            eprintln!("[JARVIS] xlib open failed, hotspot polling disabled: {:?}", e);
                            return;
                        }
                    };
                    let display = unsafe { (xlib.XOpenDisplay)(std::ptr::null()) };
                    if display.is_null() {
                        eprintln!("[JARVIS] XOpenDisplay returned null, hotspot polling disabled");
                        return;
                    }
                    let root = unsafe { (xlib.XDefaultRootWindow)(display) };

                    let mut last_inside = false;
                    loop {
                        std::thread::sleep(std::time::Duration::from_millis(33));

                        let open = match chat_open_poll.lock() {
                            Ok(g) => *g,
                            Err(_) => false,
                        };
                        if !open {
                            if last_inside {
                                let _ = win.set_ignore_cursor_events(true);
                                last_inside = false;
                            }
                            continue;
                        }

                        let rect = match panel_rect_poll.lock() {
                            Ok(g) => *g,
                            Err(_) => continue,
                        };
                        if rect.w <= 0 || rect.h <= 0 {
                            if last_inside {
                                let _ = win.set_ignore_cursor_events(true);
                                last_inside = false;
                            }
                            continue;
                        }

                        let mut root_ret: u64 = 0;
                        let mut child_ret: u64 = 0;
                        let mut root_x = 0i32;
                        let mut root_y = 0i32;
                        let mut win_x = 0i32;
                        let mut win_y = 0i32;
                        let mut mask = 0u32;
                        let ok = unsafe {
                            (xlib.XQueryPointer)(
                                display, root,
                                &mut root_ret, &mut child_ret,
                                &mut root_x, &mut root_y,
                                &mut win_x, &mut win_y,
                                &mut mask,
                            )
                        };
                        if ok == 0 { continue; }

                        // Convert viewport-relative panel rect to screen coords
                        // by adding the Tauri window's outer position.
                        let (wx, wy) = match win.outer_position() {
                            Ok(p) => (p.x, p.y),
                            Err(_) => (0, 0),
                        };
                        let abs_x = wx + rect.x;
                        let abs_y = wy + rect.y;
                        let inside = root_x >= abs_x && root_x < abs_x + rect.w
                                  && root_y >= abs_y && root_y < abs_y + rect.h;

                        if inside != last_inside {
                            let _ = win.set_ignore_cursor_events(!inside);
                            last_inside = inside;
                        }
                    }
                });
            }

            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            set_click_through,
            set_layer,
            set_chat_state,
            set_panel_rect,
            set_tray_state,
            get_primary_monitor_info,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
