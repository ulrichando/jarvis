#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use tauri::{
    Manager, WebviewWindow, PhysicalSize, PhysicalPosition,
    menu::{MenuBuilder, MenuItemBuilder, PredefinedMenuItem},
    tray::TrayIconBuilder,
    Emitter,
};

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
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .setup(|app| {
            let window = app.get_webview_window("main").unwrap();

            // Use the primary monitor (laptop screen / eDP-1).
            // On multi-monitor setups this is always the built-in display.
            let target = window.primary_monitor().ok().flatten();
            if let Some(monitor) = target {
                let size = monitor.size();
                let pos  = monitor.position();
                println!(
                    "[JARVIS] Target monitor (primary): {}x{}+{}+{}",
                    size.width, size.height, pos.x, pos.y
                );
                let _ = window.set_size(PhysicalSize::new(size.width, size.height));
                let _ = window.set_position(PhysicalPosition::new(pos.x, pos.y));
            }

            // Start in click-through (sphere floats, doesn't block input)
            let _ = window.set_ignore_cursor_events(true);

            // ── System tray ──
            // Menu items — labels include keyboard shortcut hints
            let show_item    = MenuItemBuilder::with_id("toggle_vis",   "Show / Hide JARVIS   Ctrl+H").build(app)?;
            let chat_item    = MenuItemBuilder::with_id("open_chat",    "Open Chat Panel      Ctrl+H").build(app)?;
            let mute_item    = MenuItemBuilder::with_id("mute",         "Mute / Unmute Voice").build(app)?;
            let sep1         = PredefinedMenuItem::separator(app)?;
            let browser_item = MenuItemBuilder::with_id("open_browser", "Open in Browser").build(app)?;
            let sep2         = PredefinedMenuItem::separator(app)?;
            let quit_item    = MenuItemBuilder::with_id("quit",         "Quit JARVIS          Ctrl+Q").build(app)?;

            let menu = MenuBuilder::new(app)
                .item(&show_item)
                .item(&chat_item)
                .item(&mute_item)
                .item(&sep1)
                .item(&browser_item)
                .item(&sep2)
                .item(&quit_item)
                .build()?;

            let tray = TrayIconBuilder::new()
                .icon(tauri::include_image!("icons/tray.png"))
                .menu(&menu)
                .tooltip("J.A.R.V.I.S.")
                .on_menu_event(|app, event| {
                    let win = app.get_webview_window("main");
                    match event.id().as_ref() {
                        "toggle_vis" | "open_chat" => {
                            if let Some(w) = win {
                                let _ = w.emit("tray-open-chat", ());
                            }
                        }
                        "mute" => {
                            if let Some(w) = win {
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

            // Keep tray alive for the full app lifetime — Rust would drop it
            // at end of setup closure otherwise, removing the tray icon.
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
