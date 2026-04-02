#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use tauri::Manager;
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::menu::{MenuBuilder, MenuItemBuilder};
use tauri::image::Image;

#[tauri::command]
fn exit_fullscreen(window: tauri::Window) {
    let _ = window.set_fullscreen(false);
}

#[tauri::command]
fn toggle_fullscreen(window: tauri::Window) {
    let is_fs = window.is_fullscreen().unwrap_or(false);
    let _ = window.set_fullscreen(!is_fs);
}

fn main() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![exit_fullscreen, toggle_fullscreen])
        .setup(|app| {
            // Load tray icon
            let icon = Image::from_path("icons/icon.png").unwrap_or_else(|_| {
                Image::from_bytes(include_bytes!("../icons/icon.png")).expect("icon")
            });

            // Build tray menu
            let show = MenuItemBuilder::with_id("show", "Show JARVIS").build(app)?;
            let hide = MenuItemBuilder::with_id("hide", "Hide JARVIS").build(app)?;
            let fullscreen = MenuItemBuilder::with_id("fullscreen", "Toggle Fullscreen").build(app)?;
            let quit = MenuItemBuilder::with_id("quit", "Quit JARVIS").build(app)?;

            let menu = MenuBuilder::new(app)
                .items(&[&show, &hide, &fullscreen, &quit])
                .build()?;

            let _tray = TrayIconBuilder::new()
                .icon(icon)
                .tooltip("J.A.R.V.I.S.")
                .menu(&menu)
                .on_menu_event(|app, event| {
                    match event.id().as_ref() {
                        "quit" => std::process::exit(0),
                        "show" => {
                            if let Some(window) = app.get_webview_window("main") {
                                let _ = window.show();
                                let _ = window.set_focus();
                            }
                        }
                        "hide" => {
                            if let Some(window) = app.get_webview_window("main") {
                                let _ = window.hide();
                            }
                        }
                        "fullscreen" => {
                            if let Some(window) = app.get_webview_window("main") {
                                let is_fs = window.is_fullscreen().unwrap_or(false);
                                let _ = window.set_fullscreen(!is_fs);
                            }
                        }
                        _ => {}
                    }
                })
                .on_tray_icon_event(|tray, event| {
                    if let TrayIconEvent::Click {
                        button: MouseButton::Left,
                        button_state: MouseButtonState::Up,
                        ..
                    } = event
                    {
                        let app = tray.app_handle();
                        if let Some(window) = app.get_webview_window("main") {
                            let _ = window.show();
                            let _ = window.set_focus();
                        }
                    }
                })
                .build(app)?;

            // Configure WebKitGTK webview
            if let Some(window) = app.get_webview_window("main") {
                #[cfg(target_os = "linux")]
                {
                    let _ = window.with_webview(|webview| {
                        use webkit2gtk::PermissionRequestExt;
                        use webkit2gtk::WebViewExt;
                        use webkit2gtk::SettingsExt;

                        let wv = webview.inner();

                        // Transparent background so reactor floats on desktop
                        wv.set_background_color(&gdk::RGBA::new(0.0, 0.0, 0.0, 0.0));

                        // Auto-grant mic/camera/notification permissions
                        wv.connect_permission_request(|_wv, request| {
                            request.allow();
                            true
                        });

                        // Enable media features
                        if let Some(settings) = wv.settings() {
                            settings.set_enable_media_stream(true);
                            settings.set_enable_mediasource(true);
                            settings.set_media_playback_requires_user_gesture(false);
                            settings.set_enable_webaudio(true);
                        }
                    });
                }

                // Force desktop mode + keyboard shortcuts
                let _ = window.eval(r#"
                    // Force transparent desktop mode
                    function applyDesktopMode() {
                        document.documentElement.classList.remove('web-mode');
                        document.documentElement.classList.add('desktop-mode');
                        if (document.body) {
                            document.body.classList.remove('web-mode');
                            document.body.classList.add('desktop-mode');
                            document.body.style.background = 'transparent';
                        }
                    }
                    applyDesktopMode();
                    document.addEventListener('DOMContentLoaded', applyDesktopMode);
                    // Also re-apply after page fully loads (in case scripts override)
                    window.addEventListener('load', applyDesktopMode);

                    document.addEventListener('keydown', function(e) {
                        if (e.key === 'Escape' && window.__TAURI__) {
                            window.__TAURI__.core.invoke('exit_fullscreen');
                        }
                        if (e.key === 'f' && !e.ctrlKey && !e.metaKey && document.activeElement && document.activeElement.tagName !== 'INPUT' && window.__TAURI__) {
                            window.__TAURI__.core.invoke('toggle_fullscreen');
                        }
                    });
                "#);
            }

            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                let _ = window.hide();
                api.prevent_close();
            }
        })
        .run(tauri::generate_context!())
        .expect("error running JARVIS");
}
