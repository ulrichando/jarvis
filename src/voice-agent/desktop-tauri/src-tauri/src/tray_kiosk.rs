//! Kiosk-related tray submenu construction + dispatch.
//!
//! v2 separates tray-construction concerns from main.rs to keep both
//! files focused. Per-monitor MenuItems are retained in managed
//! AppState so set_checked() works later (Tauri Linux GTK + dynamic
//! submenu items requires this pattern — issues #11462 / #12649).
//!
//! See docs/superpowers/specs/2026-05-28-jarvis-kiosk-mode-v2-design.md.

use std::sync::Mutex;

use tauri::{
    menu::{CheckMenuItem, CheckMenuItemBuilder, MenuItem, MenuItemBuilder, PredefinedMenuItem, Submenu, SubmenuBuilder},
    AppHandle, Listener, Manager, Wry,
};

/// Per-monitor CheckMenuItems retained in managed state so we can flip
/// their checked state from the kiosk-changed event listener.
pub struct KioskMonitorItems(pub Mutex<Vec<CheckMenuItem<Wry>>>);

/// Build the "Focus mode (kiosk) ▸" submenu. Returns the submenu (to be
/// attached to the main tray MenuBuilder via .item(&submenu)).
///
/// IDs emitted:
///   - "kiosk_mon_<idx>" — per-monitor CheckMenuItems (one per detected screen)
///   - "kiosk_off"       — explicit exit MenuItem
///
/// Monitor enumeration runs against the main window — if it isn't yet
/// mapped (rare in setup), the submenu shows just "Exit focus mode" and
/// the user can refresh by reopening JARVIS.
pub fn build_kiosk_submenu(app: &AppHandle) -> tauri::Result<Submenu<Wry>> {
    let main_win = app.get_webview_window("main");
    let monitors: Vec<_> = main_win
        .as_ref()
        .and_then(|w| w.available_monitors().ok())
        .unwrap_or_default();
    // Primary-monitor probe: position equality is the cheapest stable check
    // across Tauri's Monitor type (no PartialEq on the struct itself).
    let primary_pos = main_win
        .as_ref()
        .and_then(|w| w.primary_monitor().ok())
        .flatten()
        .map(|p| (p.position().x, p.position().y));

    let mut mon_items: Vec<CheckMenuItem<Wry>> = Vec::new();
    for (i, m) in monitors.iter().enumerate() {
        let pos = m.position();
        let size = m.size();
        let mon_name = m.name().map(|s| s.as_str()).unwrap_or("");
        let is_primary = primary_pos == Some((pos.x, pos.y));
        // RandR connector-name heuristic: eDP-/LVDS-/DSI- are internal panels
        // (laptop screens). Anything else (HDMI-, DP-, DVI-, VGA-) is an
        // external display. Falls back to "" when the name is missing.
        let kind = if mon_name.starts_with("eDP")
            || mon_name.starts_with("LVDS")
            || mon_name.starts_with("DSI")
        {
            "laptop"
        } else if mon_name.is_empty() {
            ""
        } else {
            "external"
        };
        let mut tags: Vec<&str> = Vec::new();
        if !kind.is_empty() {
            tags.push(kind);
        }
        if is_primary {
            tags.push("primary");
        }
        let tag_suffix = if tags.is_empty() {
            String::new()
        } else {
            format!(" [{}]", tags.join(", "))
        };
        let prefix = if mon_name.is_empty() {
            format!("Monitor {}", i)
        } else {
            mon_name.to_string()
        };
        let label = format!(
            "{}{}: {}x{} at {},{}",
            prefix, tag_suffix, size.width, size.height, pos.x, pos.y
        );
        let id = format!("kiosk_mon_{}", i);
        let item = CheckMenuItemBuilder::with_id(&id, &label).checked(false).build(app)?;
        mon_items.push(item);
    }

    let exit_item: MenuItem<Wry> = MenuItemBuilder::with_id("kiosk_off", "Exit focus mode").build(app)?;
    let sep = PredefinedMenuItem::separator(app)?;

    let mut sb = SubmenuBuilder::new(app, "Focus mode (kiosk) ▸");
    for it in &mon_items {
        sb = sb.item(it);
    }
    if !mon_items.is_empty() {
        sb = sb.item(&sep);
    }
    sb = sb.item(&exit_item);
    let submenu = sb.build()?;

    // Stash mon_items in managed AppState so set_checked() works from the
    // kiosk-changed listener.
    app.manage(KioskMonitorItems(Mutex::new(mon_items)));

    Ok(submenu)
}

/// Dispatch a tray menu click. Returns true if this id was a kiosk
/// event we handled; false otherwise.
pub fn handle_kiosk_menu_event(app: &AppHandle, id: &str) -> bool {
    if id == "kiosk_off" {
        if let Err(e) = crate::kiosk::exit_kiosk(app.clone()) {
            eprintln!("[JARVIS] kiosk_off failed: {}", e);
        }
        return true;
    }
    if let Some(idx_str) = id.strip_prefix("kiosk_mon_") {
        let Ok(idx) = idx_str.parse::<usize>() else {
            eprintln!("[JARVIS] kiosk_mon_<bad-idx>: {}", id);
            return true;
        };
        // Three-way dispatch:
        //   - off          → enter on idx
        //   - on @ idx     → exit (clicking the active monitor exits)
        //   - on @ other   → switch (exit + immediate re-enter on idx)
        let current = crate::kiosk::KIOSK_STATE
            .lock()
            .ok()
            .and_then(|s| s.as_ref().map(|x| x.monitor_idx));
        match current {
            None => {
                if let Err(e) = crate::kiosk::enter_kiosk_on_monitor(app.clone(), idx) {
                    eprintln!("[JARVIS] kiosk_mon_{} enter failed: {}", idx, e);
                }
            }
            Some(active) if active == idx => {
                if let Err(e) = crate::kiosk::exit_kiosk(app.clone()) {
                    eprintln!("[JARVIS] kiosk_mon_{} exit-toggle failed: {}", idx, e);
                }
            }
            Some(_) => {
                if let Err(e) = crate::kiosk::exit_kiosk(app.clone()) {
                    eprintln!("[JARVIS] kiosk_mon switch-exit failed: {}", e);
                }
                if let Err(e) = crate::kiosk::enter_kiosk_on_monitor(app.clone(), idx) {
                    eprintln!("[JARVIS] kiosk_mon_{} switch-enter failed: {}", idx, e);
                }
            }
        }
        return true;
    }
    false
}

/// Install the kiosk-changed event listener that syncs CheckMenuItem
/// checked states. Call after the main window is created (in setup()).
pub fn install_kiosk_changed_listener(app: &AppHandle) {
    let Some(main_win) = app.get_webview_window("main") else { return };
    let app_for_listener = app.clone();
    main_win.listen("kiosk-changed", move |event| {
        // Payload is { on: bool, monitor?: usize }.
        let on_idx: Option<usize> = serde_json::from_str::<serde_json::Value>(event.payload())
            .ok()
            .and_then(|v| {
                let on = v.get("on")?.as_bool()?;
                if !on {
                    return Some(None);
                }
                let m = v.get("monitor")?.as_u64()? as usize;
                Some(Some(m))
            })
            .unwrap_or(None);

        if let Some(state) = app_for_listener.try_state::<KioskMonitorItems>() {
            if let Ok(items) = state.0.lock() {
                for (i, item) in items.iter().enumerate() {
                    let want = on_idx.map(|on| on == i).unwrap_or(false);
                    let _ = item.set_checked(want);
                }
            }
        }
    });
}
