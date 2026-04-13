#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::sync::{Arc, Mutex};
use tauri::{
    Manager, WebviewWindow, PhysicalSize, PhysicalPosition,
    menu::{MenuBuilder, MenuItemBuilder, PredefinedMenuItem},
    tray::TrayIconBuilder,
    Emitter,
};

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
    // Shared flag: is the chat panel currently open?
    let chat_open: Arc<Mutex<bool>> = Arc::new(Mutex::new(false));

    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .setup(move |app| {
            let window = app.get_webview_window("main").unwrap();

            // Start on primary monitor (laptop / eDP-1)
            if let Ok(Some(monitor)) = window.primary_monitor() {
                let size = monitor.size();
                let pos  = monitor.position();
                println!("[JARVIS] Target monitor (primary): {}x{}+{}+{}", size.width, size.height, pos.x, pos.y);
                let _ = window.set_size(PhysicalSize::new(size.width, size.height));
                let _ = window.set_position(PhysicalPosition::new(pos.x, pos.y));
            }

            // Transparent overlay: start click-through, reactor floats
            let _ = window.set_ignore_cursor_events(true);

            // ── System tray ──
            let show_item    = MenuItemBuilder::with_id("toggle_vis",   "Show / Hide JARVIS").build(app)?;
            let chat_item    = MenuItemBuilder::with_id("open_chat",    "Open Chat Panel").build(app)?;
            let mute_item    = MenuItemBuilder::with_id("mute",         "Mute / Unmute Voice").build(app)?;
            let sep1         = PredefinedMenuItem::separator(app)?;
            let browser_item = MenuItemBuilder::with_id("open_browser", "Open in Browser").build(app)?;
            let sep2         = PredefinedMenuItem::separator(app)?;
            let quit_item    = MenuItemBuilder::with_id("quit",         "Quit JARVIS").build(app)?;

            let menu = MenuBuilder::new(app)
                .item(&show_item)
                .item(&chat_item)
                .item(&mute_item)
                .item(&sep1)
                .item(&browser_item)
                .item(&sep2)
                .item(&quit_item)
                .build()?;

            let chat_open_tray = Arc::clone(&chat_open);

            let tray = TrayIconBuilder::new()
                .icon(tauri::include_image!("icons/tray.png"))
                .menu(&menu)
                .tooltip("J.A.R.V.I.S.")
                .on_menu_event(move |app, event| {
                    match event.id().as_ref() {
                        "toggle_vis" => {
                            // Show/Hide the reactor sphere — no chat panel
                            if let Some(w) = app.get_webview_window("main") {
                                snap_to_cursor_monitor(&w);
                                let _ = w.show();
                                let _ = w.emit("tray-toggle-reactor", ());
                                println!("[JARVIS] Reactor toggled via tray");
                            }
                        }
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

            std::mem::forget(tray);
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            set_click_through,
            set_layer,
            get_primary_monitor_info,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
