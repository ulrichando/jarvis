#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::sync::{Arc, Mutex};
use tauri::{
    Manager, State, WebviewWindow, PhysicalSize, PhysicalPosition,
    image::Image,
    menu::{MenuBuilder, MenuItem, MenuItemBuilder, PredefinedMenuItem, SubmenuBuilder},
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

/// Handle to the "Tool: …" line inside the Models submenu.
/// Stashed in state so the `set_provider_label` command can rewrite
/// the label whenever the agent reports its active CLI model via
/// the voice-client `/status` endpoint.
struct ProviderLabel(Mutex<Option<MenuItem<Wry>>>);

/// Handle to the "Speech: …" line inside the Models submenu.
/// Same pattern as ProviderLabel but for the voice-LLM tier.
struct SpeechLabel(Mutex<Option<MenuItem<Wry>>>);

/// Handle to the "TTS: …" line inside the Models submenu.
/// Rewritten by set_tts_label / switch_tts_provider as the active
/// TTS voice changes.
struct TtsLabel(Mutex<Option<MenuItem<Wry>>>);

/// All five TTS voice menu items, stored so switch_tts_provider can
/// add/remove the "✓ " prefix to reflect the active selection.
/// Ordered to match TTS_VOICES.
struct TtsVoiceItems(Mutex<Vec<MenuItem<Wry>>>);

/// The source tray artwork (icons/tray.png — the concentric-ring /
/// reactor design). Embedded into the binary at compile time so the
/// icon can be tinted per-state at runtime without touching disk.
const TRAY_SRC_PNG: &[u8] = include_bytes!("../icons/tray.png");

/// Cached decoded source: (width, height, rgba). Decoded once on first
/// call by tray_image_for — decoding a 48×48 PNG is trivially fast but
/// there's no reason to repeat it on every state transition.
use std::sync::OnceLock;
static TRAY_SRC: OnceLock<(u32, u32, Vec<u8>)> = OnceLock::new();

fn decode_tray_source() -> (u32, u32, Vec<u8>) {
    // Tauri 2's Image::from_bytes decodes PNG when the `image-png`
    // feature is enabled on the tauri crate (it is — see Cargo.toml).
    // We fall back to a plain green disk if decode fails so the tray
    // never disappears on a broken build.
    match Image::from_bytes(TRAY_SRC_PNG) {
        Ok(img) => (img.width(), img.height(), img.rgba().to_vec()),
        Err(e)  => {
            eprintln!("[JARVIS] tray.png decode failed ({e}); falling back to solid circle");
            (32, 32, solid_circle_rgba(32, 63, 185, 80))
        }
    }
}

/// Fallback filled circle for the error path above. Kept minimal — in
/// practice the decode always succeeds because the PNG is embedded.
fn solid_circle_rgba(size: u32, r: u8, g: u8, b: u8) -> Vec<u8> {
    let mut buf = vec![0u8; (size * size * 4) as usize];
    let center = size as f32 / 2.0;
    let radius = center - 1.5;
    for y in 0..size {
        for x in 0..size {
            let dx = x as f32 + 0.5 - center;
            let dy = y as f32 + 0.5 - center;
            let d  = (dx * dx + dy * dy).sqrt();
            let a: u8 = if d <= radius { 255 }
                        else if d <= radius + 1.0 { ((radius + 1.0 - d) * 255.0).clamp(0.0, 255.0) as u8 }
                        else { 0 };
            let i = ((y * size + x) * 4) as usize;
            buf[i]=r; buf[i+1]=g; buf[i+2]=b; buf[i+3]=a;
        }
    }
    buf
}

/// Tint the source RGBA by a target colour, preserving the source's
/// alpha (so the artwork's shape is unchanged) and using the source's
/// luminance as a brightness multiplier (so inner detail stays visible
/// — bright pixels stay near the tint colour, darker pixels stay
/// darker). This is what the user asked for: keep the existing icon,
/// just change its colour.
fn tint_source(r: u8, g: u8, b: u8) -> (u32, u32, Vec<u8>) {
    let (w, h, src) = TRAY_SRC.get_or_init(decode_tray_source).clone();
    let mut out = Vec::with_capacity(src.len());
    let mut i = 0;
    while i < src.len() {
        let sr = src[i]     as f32;
        let sg = src[i + 1] as f32;
        let sb = src[i + 2] as f32;
        let sa = src[i + 3];
        // ITU-R BT.709 luma; kept in [0,1]. Pixels that were pure-white
        // in the source come out at full tint colour; near-black pixels
        // stay dim.
        let lum = (0.2126 * sr + 0.7152 * sg + 0.0722 * sb) / 255.0;
        out.push((r as f32 * lum).clamp(0.0, 255.0) as u8);
        out.push((g as f32 * lum).clamp(0.0, 255.0) as u8);
        out.push((b as f32 * lum).clamp(0.0, 255.0) as u8);
        out.push(sa);
        i += 4;
    }
    (w, h, out)
}

/// Return (width, height, rgba) for the given state. Tauri wraps the
/// buffer in Image::new_owned at the call site.
fn tray_image_for(state: &str) -> (u32, u32, Vec<u8>) {
    // Colours chosen to match the VoiceClientPill (top-right of the
    // overlay) 1:1 so the tray and the pill never tell you different
    // stories. Tuned to read clearly against both dark and light XFCE
    // panels.
    match state {
        "talking"   => tint_source( 68, 147, 248),   // blue   — JARVIS speaking
        "listening" => tint_source( 34, 211, 238),   // cyan   — you speaking
        "booting"   => tint_source(168,  85, 247),   // purple — service cold-starting
        "thinking"  => tint_source(250, 180,  50),   // amber  — LLM generating / tool running
        "muted"     => tint_source( 20,  20,  20),   // black  — mic muted
        "offline"   => tint_source(239,  68,  68),   // red    — voice client down
        _           => tint_source( 63, 185,  80),   // green  — idle / ready
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
/// Accepted values: "idle" | "listening" | "talking" | "booting" | "thinking" | "offline".
/// "idle" and "listening" both render green (the mic is live either way).
#[tauri::command]
fn set_tray_state(state: &str, tray: State<TrayHandle>) -> Result<(), String> {
    let (w, h, rgba) = tray_image_for(state);
    let image = Image::new_owned(rgba, w, h);
    let guard = tray.0.lock().map_err(|e| e.to_string())?;
    if let Some(t) = guard.as_ref() {
        t.set_icon(Some(image)).map_err(|e| e.to_string())?;
    }
    Ok(())
}

/// Map a CLI model ID to a short pretty label for the tray.
/// IDs and labels mirror jarvis_agent.py's CLI_MODELS dict.
fn cli_model_pretty(id: &str) -> Option<&'static str> {
    match id {
        "deepseek-chat"                                  => Some("DeepSeek · chat"),
        "deepseek-reasoner"                              => Some("DeepSeek · reasoner"),
        "deepseek-v4-flash"                              => Some("DeepSeek · v4 flash"),
        "deepseek-v4-pro"                                => Some("DeepSeek · v4 pro"),
        "qwen/qwen3-32b"                                 => Some("Groq · qwen3-32b"),
        "llama-3.3-70b-versatile"                        => Some("Groq · llama 3.3 70B"),
        "meta-llama/llama-4-scout-17b-16e-instruct"      => Some("Groq · llama 4 scout"),
        "openai/gpt-oss-120b"                            => Some("Groq · gpt-oss-120b"),
        _ => None,
    }
}

/// Map a speech model ID to a short pretty label.
/// Mirrors jarvis_agent.py's SPEECH_MODELS dict.
fn speech_model_pretty(id: &str) -> Option<&'static str> {
    match id {
        "llama-3.3-70b-versatile"                        => Some("Groq · llama 3.3 70B"),
        "llama-3.1-8b-instant"                           => Some("Groq · llama 3.1 8B instant"),
        "qwen/qwen3-32b"                                 => Some("Groq · qwen3-32b"),
        "openai/gpt-oss-120b"                            => Some("Groq · gpt-oss-120b"),
        "meta-llama/llama-4-scout-17b-16e-instruct"      => Some("Groq · llama 4 scout"),
        _ => None,
    }
}

/// Switch the active CLI model by POSTing to the voice-client. The
/// voice-client persists the choice in `~/.jarvis/cli-model`; the
/// next run_jarvis_cli call picks it up via env vars. No process
/// restarts needed. Spawned via curl to avoid pulling reqwest.
fn switch_cli_model(app: &tauri::AppHandle, id: &'static str) {
    let body = format!(r#"{{"model":"{id}"}}"#);
    let _ = std::process::Command::new("curl")
        .args([
            "-s", "-X", "POST",
            "http://127.0.0.1:8767/cli-model",
            "-H", "Content-Type: application/json",
            "-d", &body,
        ])
        .spawn();

    // Optimistic label update so the menu reflects the click without
    // waiting for the next /status poll.
    if let Some(pretty) = cli_model_pretty(id) {
        let label: State<ProviderLabel> = app.state();
        let guard = match label.0.lock() {
            Ok(g) => g,
            Err(_) => return,
        };
        if let Some(item) = guard.as_ref() {
            let _ = item.set_text(format!("Tool: {pretty}"));
        }
    }
}

/// Ordered list of (provider_spec, display_label) pairs.
/// Must match the order items are pushed into TtsVoiceItems.
const TTS_VOICES: &[(&str, &str)] = &[
    ("elevenlabs:JBFqnCBsd6RMkjVDRZzb", "ElevenLabs · George"),
    ("elevenlabs:pNInz6obpgDQGcFmaJgB", "ElevenLabs · Adam"),
    ("elevenlabs:nPczCjzI2devNBz1zQrb", "ElevenLabs · Brian"),
    ("groq:troy",                        "Groq Orpheus · Troy"),
    ("groq:austin",                      "Groq Orpheus · Austin"),
];

/// Map a TTS provider:voice spec to a short pretty label for the tray.
/// Mirrors TTS_PROVIDERS_AVAILABLE in jarvis_voice_client.py.
fn tts_provider_pretty(spec: &str) -> Option<&'static str> {
    TTS_VOICES.iter().find(|(s, _)| *s == spec).map(|(_, l)| *l)
}

/// Switch the active TTS voice by POSTing to the voice-client.
/// Voice-client writes `~/.jarvis/tts-provider`; the agent reads it
/// on the next session start (or via _build_tts_chain on each call).
/// No agent restart needed — ElevenLabs and Groq Orpheus are both
/// in the FallbackAdapter chain; order shifts on next utterance.
fn switch_tts_provider(app: &tauri::AppHandle, spec: &'static str) {
    let body = format!(r#"{{"provider":"{spec}"}}"#);
    let _ = std::process::Command::new("curl")
        .args([
            "-s", "-X", "POST",
            "http://127.0.0.1:8767/tts-provider",
            "-H", "Content-Type: application/json",
            "-d", &body,
        ])
        .spawn();

    // Update ✓ prefix on all voice submenu items.
    {
        let items_state: State<TtsVoiceItems> = app.state();
        if let Ok(items) = items_state.0.lock() {
            for (i, (s, label)) in TTS_VOICES.iter().enumerate() {
                if let Some(item) = items.get(i) {
                    let text = if *s == spec {
                        format!("✓  {label}")
                    } else {
                        (*label).to_string()
                    };
                    let _ = item.set_text(text);
                }
            }
        };
    }

    // Update the "TTS: …" header line.
    if let Some(pretty) = tts_provider_pretty(spec) {
        let label: State<TtsLabel> = app.state();
        if let Ok(guard) = label.0.lock() {
            if let Some(item) = guard.as_ref() {
                let _ = item.set_text(format!("TTS: {pretty}"));
            }
        };
    }
}

/// Switch the active speech model by POSTing to the voice-client.
/// Voice-client persists the choice in `~/.jarvis/voice-model` AND
/// triggers `systemctl --user restart jarvis-voice-agent` so the
/// new LLM is built on the next session start. The pill flips to
/// amber "JARVIS booting" for ~5 s then back to green.
fn switch_speech_model(app: &tauri::AppHandle, id: &'static str) {
    let body = format!(r#"{{"model":"{id}"}}"#);
    let _ = std::process::Command::new("curl")
        .args([
            "-s", "-X", "POST",
            "http://127.0.0.1:8767/voice-model",
            "-H", "Content-Type: application/json",
            "-d", &body,
        ])
        .spawn();

    if let Some(pretty) = speech_model_pretty(id) {
        let label: State<SpeechLabel> = app.state();
        let guard = match label.0.lock() {
            Ok(g) => g,
            Err(_) => return,
        };
        if let Some(item) = guard.as_ref() {
            let _ = item.set_text(format!("Speech: {pretty}"));
        }
    }
}

/// Update the "Speech: …" line inside the Models tray submenu.
/// React calls this whenever the voice-client `/status` reports a
/// new speech_model field. Empty string = "no choice yet".
#[tauri::command]
fn set_speech_label(name: &str, label: State<SpeechLabel>) -> Result<(), String> {
    let text: String = if name.is_empty() {
        "Speech: (loading…)".to_string()
    } else {
        match speech_model_pretty(name) {
            Some(pretty) => format!("Speech: {pretty}"),
            None         => return Err(format!("unknown speech model: {name}")),
        }
    };
    let guard = label.0.lock().map_err(|e| e.to_string())?;
    if let Some(item) = guard.as_ref() {
        item.set_text(text).map_err(|e| e.to_string())?;
    }
    Ok(())
}

/// Update the "TTS: …" header line AND the ✓ checkmarks in the voice
/// submenu. Called from JS on every /status poll when tts_provider
/// changes — keeps the tray in sync even when the switch happens via
/// the Python endpoint rather than a tray click.
#[tauri::command]
fn set_tts_label(name: &str, label: State<TtsLabel>, items: State<TtsVoiceItems>) -> Result<(), String> {
    let text: String = if name.is_empty() {
        "TTS: (loading…)".to_string()
    } else {
        match tts_provider_pretty(name) {
            Some(pretty) => format!("TTS: {pretty}"),
            None         => format!("TTS: {name}"),
        }
    };
    // Update the header line.
    {
        let guard = label.0.lock().map_err(|e| e.to_string())?;
        if let Some(item) = guard.as_ref() {
            item.set_text(text).map_err(|e| e.to_string())?;
        }
    };
    // Sync ✓ checkmarks on the voice submenu items.
    if !name.is_empty() {
        if let Ok(voice_items) = items.0.lock() {
            for (i, (spec, lbl)) in TTS_VOICES.iter().enumerate() {
                if let Some(item) = voice_items.get(i) {
                    let t = if *spec == name {
                        format!("✓  {lbl}")
                    } else {
                        (*lbl).to_string()
                    };
                    let _ = item.set_text(t);
                }
            }
        };
    }
    Ok(())
}

/// Update the "Tool: …" line inside the Models tray submenu.
/// React calls this whenever the voice-client `/status` reports a new
/// model ID. Empty string = "no choice yet". Speech model is static
/// in the menu (Llama 3.3 70B on Groq) so no setter for it.
#[tauri::command]
fn set_provider_label(name: &str, label: State<ProviderLabel>) -> Result<(), String> {
    let text: String = if name.is_empty() {
        "Tool: (loading…)".to_string()
    } else {
        match cli_model_pretty(name) {
            Some(pretty) => format!("Tool: {pretty}"),
            None         => return Err(format!("unknown CLI model: {name}")),
        }
    };
    let guard = label.0.lock().map_err(|e| e.to_string())?;
    if let Some(item) = guard.as_ref() {
        item.set_text(text).map_err(|e| e.to_string())?;
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
        .manage(ProviderLabel(Mutex::new(None)))
        .manage(SpeechLabel(Mutex::new(None)))
        .manage(TtsLabel(Mutex::new(None)))
        .manage(TtsVoiceItems(Mutex::new(Vec::new())))
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

            // Enable mic / media stream / WebRTC in the WebKit2GTK webview
            // and auto-grant permission requests so getUserMedia + LiveKit
            // WebRTC work for the always-listening voice loop.
            //
            // Note on `enable-webrtc`: the webkit2gtk Rust crate 2.0's
            // `set_enable_webrtc()` targets the GObject property of the
            // same name but on some WebKitGTK versions the typed setter
            // short-circuits if a runtime-feature gate is off. Setting
            // the property directly via `ObjectExt::set_property` is the
            // bulletproof path — it's what `g_object_set(settings,
            // "enable-webrtc", TRUE, NULL)` does in C, and matches the
            // names the Python probe just showed: enable-webrtc /
            // enable-media-stream / enable-media-capabilities.
            //
            // After we set it, we read it back via `get_property` and
            // println! the result so a reboot/relaunch immediately shows
            // in the launch log whether WebRTC is actually ON.
            #[cfg(target_os = "linux")]
            {
                use webkit2gtk::{WebViewExt, SettingsExt, PermissionRequestExt};
                use webkit2gtk::gio::prelude::ObjectExt;
                let _ = window.with_webview(|webview| {
                    let wv = webview.inner();
                    if let Some(settings) = WebViewExt::settings(&wv) {
                        settings.set_enable_media_stream(true);
                        settings.set_enable_mediasource(true);
                        settings.set_media_playback_requires_user_gesture(false);
                        // WebRTC — set via both the typed setter and the raw
                        // property to cover bindings that don't route through
                        // each other.
                        settings.set_enable_webrtc(true);
                        settings.set_property("enable-webrtc",           &true);
                        settings.set_property("enable-media-capabilities", &true);
                        settings.set_property("enable-media-stream",      &true);
                        // Confirm post-set — prints to /tmp/jarvis-launch.log.
                        let webrtc_on = settings.property::<bool>("enable-webrtc");
                        let mstream_on = settings.property::<bool>("enable-media-stream");
                        let mcap_on    = settings.property::<bool>("enable-media-capabilities");
                        println!(
                            "[JARVIS] WebKit settings post-set: webrtc={} mediastream={} mediacap={}",
                            webrtc_on, mstream_on, mcap_on,
                        );
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
            // Computer-use kill switch. Writes ~/.jarvis/computer-use-stop;
            // jarvis_computer_use.py's _check_guards picks it up on the
            // next action and raises ComputerUseError so the LLM exits the
            // session and reports back. No-op if no session is active.
            let stop_cu_item = MenuItemBuilder::with_id("stop_computer_use", "Stop Computer Use").build(app)?;

            // ── 📷 Camera source submenu ──
            // Picks which video device the webcam_* / face_* tools capture
            // from. Selection persists to ~/.jarvis/webcam-device which
            // jarvis_computer_use.py reads on each capture. The Dell laptop
            // exposes RGB at /dev/video0 (visible) and IR at /dev/video2
            // (Windows-Hello-style, works in low light).
            let cam_rgb = MenuItemBuilder::with_id("camera_rgb", "RGB · /dev/video0 (default webcam)").build(app)?;
            let cam_ir  = MenuItemBuilder::with_id("camera_ir",  "IR · /dev/video2 (Windows Hello camera, low-light)").build(app)?;
            let cam_submenu = SubmenuBuilder::new(app, "📷 Camera source ▸")
                .item(&cam_rgb)
                .item(&cam_ir)
                .build()?;

            // ── 🖥 Start Screen Sharing ──
            // Writes ~/.jarvis/start-screen-share with a duration in
            // seconds; jarvis_agent.py's screen-share watcher picks it up
            // and calls live_screen(N) which streams to Gemini Live API.
            let share_screen_item = MenuItemBuilder::with_id("start_screen_share", "🖥 Start Screen Sharing (30s)").build(app)?;

            let sep1         = PredefinedMenuItem::separator(app)?;
            let browser_item = MenuItemBuilder::with_id("open_browser", "Open in Browser").build(app)?;
            let sep_prov     = PredefinedMenuItem::separator(app)?;

            // ── Models submenu ──
            // Two layers of models, surfaced clearly in the menu:
            //
            //   1) SPEECH model (the voice LLM that composes spoken
            //      replies). Hard-coded to llama-3.3-70b on Groq for
            //      latency reasons — informational only, not switchable.
            //
            //   2) TOOL model (run_jarvis_cli's underlying LLM). Live-
            //      switchable via the items below. Currently 8 options
            //      (DeepSeek×4, Groq×4) mirroring the CLI's /model
            //      picker. IDs match jarvis_agent.py's CLI_MODELS dict.
            //
            // The "Tool: …" line is dynamic — set_provider_label
            // rewrites it as the voice-client /status poll surfaces
            // the active tool model, and switch_cli_model also pokes
            // it optimistically on click.
            // Two dynamic header lines, both labeled live by JS as
            // /status reports each tier's active model.
            let speech_current = MenuItemBuilder::with_id("speech_current", "Speech: (loading…)")
                .enabled(false)
                .build(app)?;
            let provider_current = MenuItemBuilder::with_id("provider_current", "Tool: (loading…)")
                .enabled(false)
                .build(app)?;
            let header_sep   = PredefinedMenuItem::separator(app)?;

            // ── SPEECH submenu (nested under Models) ──
            // Switching speech requires an agent restart (~5 s amber).
            // Items mirror jarvis_agent.py's SPEECH_MODELS dict.
            let v_llama33   = MenuItemBuilder::with_id("speech_llama-3.3-70b-versatile",                       "Use Groq · llama 3.3 70B").build(app)?;
            let v_llama8b   = MenuItemBuilder::with_id("speech_llama-3.1-8b-instant",                          "Use Groq · llama 3.1 8B instant").build(app)?;
            let v_qwen      = MenuItemBuilder::with_id("speech_qwen/qwen3-32b",                                "Use Groq · qwen3-32b").build(app)?;
            let v_gptoss    = MenuItemBuilder::with_id("speech_openai/gpt-oss-120b",                           "Use Groq · gpt-oss-120b").build(app)?;
            let v_llama4    = MenuItemBuilder::with_id("speech_meta-llama/llama-4-scout-17b-16e-instruct",     "Use Groq · llama 4 scout").build(app)?;
            // (DeepSeek removed from speech — openai plugin can't
            // round-trip the reasoning_content field; see SPEECH_MODELS
            // in jarvis_agent.py. Still available as a Tool model.)
            let speech_submenu = SubmenuBuilder::new(app, "Speech model ▸")
                .item(&v_llama33)
                .item(&v_llama8b)
                .item(&v_qwen)
                .item(&v_gptoss)
                .item(&v_llama4)
                .build()?;

            // ── TTS VOICE submenu (nested under Models) ──
            // Switches the synthesis voice without restarting the agent.
            // Voice-client writes ~/.jarvis/tts-provider; agent's
            // _build_tts_chain reads it on next utterance. ElevenLabs
            // items require ELEVENLABS_API_KEY in env; Groq Orpheus is
            // the offline fallback.

            // Read the current selection from disk so we can pre-mark
            // it with ✓ immediately — no wait for a /status poll.
            let saved_tts = std::env::var("HOME").ok()
                .map(|h| std::path::PathBuf::from(h).join(".jarvis/tts-provider"))
                .and_then(|p| std::fs::read_to_string(p).ok())
                .map(|s| s.trim().to_string())
                .unwrap_or_default();

            let tts_item_label = |spec: &str, label: &str| -> String {
                if spec == saved_tts.as_str() { format!("✓  {label}") } else { label.to_string() }
            };
            let init_tts_header = tts_provider_pretty(&saved_tts)
                .map(|p| format!("TTS: {p}"))
                .unwrap_or_else(|| "TTS: (loading…)".to_string());

            let tts_current = MenuItemBuilder::with_id("tts_current", &init_tts_header)
                .enabled(false)
                .build(app)?;
            let tts_el_george = MenuItemBuilder::with_id("tts_el_george", &tts_item_label("elevenlabs:JBFqnCBsd6RMkjVDRZzb", "ElevenLabs · George")).build(app)?;
            let tts_el_adam   = MenuItemBuilder::with_id("tts_el_adam",   &tts_item_label("elevenlabs:pNInz6obpgDQGcFmaJgB", "ElevenLabs · Adam")).build(app)?;
            let tts_el_brian  = MenuItemBuilder::with_id("tts_el_brian",  &tts_item_label("elevenlabs:nPczCjzI2devNBz1zQrb", "ElevenLabs · Brian")).build(app)?;
            let tts_gr_troy   = MenuItemBuilder::with_id("tts_gr_troy",   &tts_item_label("groq:troy",                        "Groq Orpheus · Troy")).build(app)?;
            let tts_gr_austin = MenuItemBuilder::with_id("tts_gr_austin", &tts_item_label("groq:austin",                      "Groq Orpheus · Austin")).build(app)?;
            let tts_submenu = SubmenuBuilder::new(app, "TTS voice ▸")
                .item(&tts_el_george)
                .item(&tts_el_adam)
                .item(&tts_el_brian)
                .item(&tts_gr_troy)
                .item(&tts_gr_austin)
                .build()?;

            // ── TOOL submenu (nested under Models) ──
            // No restart needed — every run_jarvis_cli call re-reads
            // ~/.jarvis/cli-model and exports JARVIS_PROVIDER+MODEL.
            let m_ds_chat      = MenuItemBuilder::with_id("model_deepseek-chat",                              "Use DeepSeek · chat").build(app)?;
            let m_ds_reason    = MenuItemBuilder::with_id("model_deepseek-reasoner",                          "Use DeepSeek · reasoner").build(app)?;
            let m_ds_v4_flash  = MenuItemBuilder::with_id("model_deepseek-v4-flash",                          "Use DeepSeek · v4 flash").build(app)?;
            let m_ds_v4_pro    = MenuItemBuilder::with_id("model_deepseek-v4-pro",                            "Use DeepSeek · v4 pro").build(app)?;
            let m_qwen         = MenuItemBuilder::with_id("model_qwen/qwen3-32b",                             "Use Groq · qwen3-32b").build(app)?;
            let m_llama33      = MenuItemBuilder::with_id("model_llama-3.3-70b-versatile",                    "Use Groq · llama 3.3 70B").build(app)?;
            let m_llama4       = MenuItemBuilder::with_id("model_meta-llama/llama-4-scout-17b-16e-instruct",  "Use Groq · llama 4 scout").build(app)?;
            let m_gptoss       = MenuItemBuilder::with_id("model_openai/gpt-oss-120b",                        "Use Groq · gpt-oss-120b").build(app)?;
            let tool_submenu = SubmenuBuilder::new(app, "Tool model ▸")
                .item(&m_ds_chat)
                .item(&m_ds_reason)
                .item(&m_ds_v4_flash)
                .item(&m_ds_v4_pro)
                .item(&m_qwen)
                .item(&m_llama33)
                .item(&m_llama4)
                .item(&m_gptoss)
                .build()?;

            let tts_sep = PredefinedMenuItem::separator(app)?;
            let provider_submenu = SubmenuBuilder::new(app, "Models")
                .item(&speech_current)
                .item(&provider_current)
                .item(&tts_current)
                .item(&header_sep)
                .item(&speech_submenu)
                .item(&tool_submenu)
                .item(&tts_sep)
                .item(&tts_submenu)
                .build()?;

            // Hand dynamic header items to managed state so the label
            // commands can rewrite them as /status polls report changes.
            {
                let pl: State<ProviderLabel> = app.state();
                *pl.0.lock().unwrap() = Some(provider_current);
            }
            {
                let sl: State<SpeechLabel> = app.state();
                *sl.0.lock().unwrap() = Some(speech_current);
            }
            {
                let tl: State<TtsLabel> = app.state();
                *tl.0.lock().unwrap() = Some(tts_current);
            }
            {
                let vi: State<TtsVoiceItems> = app.state();
                *vi.0.lock().unwrap() = vec![tts_el_george, tts_el_adam, tts_el_brian, tts_gr_troy, tts_gr_austin];
            }

            let sep2         = PredefinedMenuItem::separator(app)?;
            let quit_item    = MenuItemBuilder::with_id("quit",         "Quit JARVIS").build(app)?;

            let menu = MenuBuilder::new(app)
                .item(&chat_item)
                .item(&mute_item)
                .item(&stop_cu_item)
                .item(&cam_submenu)
                .item(&share_screen_item)
                .item(&sep1)
                .item(&browser_item)
                .item(&sep_prov)
                .item(&provider_submenu)
                .item(&sep2)
                .item(&quit_item)
                .build()?;

            let chat_open_tray = Arc::clone(&chat_open);

            // Start the tray on the green "idle" indicator — React will
            // push state updates via set_tray_state as soon as the webview
            // boots and the WS reports status.
            let (iw, ih, idle_rgba) = tray_image_for("idle");
            let idle_icon = Image::new_owned(idle_rgba, iw, ih);
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
                            // Two voice paths exist in parallel: the legacy
                            // sidecar on :8765/api/mute (toggles useSpeech)
                            // and the LiveKit native client on :8767/mute
                            // (toggles the PipeWire mic track). Fire both so
                            // the tray mute button does the intuitive thing
                            // regardless of which pipeline the user is on.
                            // curl invocation mirrors the pre-existing
                            // pattern — Tauri webview CORS doesn't apply to
                            // subprocess calls out of the webview.
                            let _ = std::process::Command::new("curl")
                                .args(["-s", "-X", "POST", "http://127.0.0.1:8765/api/mute"])
                                .spawn();
                            // Toggle the voice-client by POSTing with no
                            // body — the Python handler defaults to "flip
                            // current state" when `mute` is absent.
                            let _ = std::process::Command::new("curl")
                                .args(["-s", "-X", "POST",
                                       "http://127.0.0.1:8767/mute",
                                       "-H", "Content-Type: application/json",
                                       "-d", "{}"])
                                .spawn();
                            if let Some(w) = app.get_webview_window("main") {
                                let _ = w.emit("tray-toggle-mute", ());
                            }
                        }
                        "camera_rgb" => {
                            if let Ok(home) = std::env::var("HOME") {
                                let p = std::path::PathBuf::from(home).join(".jarvis/webcam-device");
                                let _ = std::fs::create_dir_all(p.parent().unwrap());
                                match std::fs::write(&p, "/dev/video0\n") {
                                    Ok(_)  => println!("[JARVIS] camera source → RGB (/dev/video0)"),
                                    Err(e) => eprintln!("[JARVIS] failed to write {}: {e}", p.display()),
                                }
                            }
                        }
                        "camera_ir" => {
                            if let Ok(home) = std::env::var("HOME") {
                                let p = std::path::PathBuf::from(home).join(".jarvis/webcam-device");
                                let _ = std::fs::create_dir_all(p.parent().unwrap());
                                match std::fs::write(&p, "/dev/video2\n") {
                                    Ok(_)  => println!("[JARVIS] camera source → IR (/dev/video2)"),
                                    Err(e) => eprintln!("[JARVIS] failed to write {}: {e}", p.display()),
                                }
                            }
                        }
                        "start_screen_share" => {
                            // Write ~/.jarvis/start-screen-share with duration.
                            // jarvis_agent.py's screen-share watcher polls
                            // for this file, calls live_screen(N), and voices
                            // the result. Default 30s.
                            if let Ok(home) = std::env::var("HOME") {
                                let p = std::path::PathBuf::from(home).join(".jarvis/start-screen-share");
                                let _ = std::fs::create_dir_all(p.parent().unwrap());
                                match std::fs::write(&p, "30\n") {
                                    Ok(_)  => println!("[JARVIS] screen-share requested (30s)"),
                                    Err(e) => eprintln!("[JARVIS] failed to write {}: {e}", p.display()),
                                }
                            }
                        }
                        "stop_computer_use" => {
                            // Touch ~/.jarvis/computer-use-stop. The voice
                            // agent's _check_guards reads + unlinks this on
                            // the next action and raises ComputerUseError,
                            // which the action tool returns as success=False
                            // so the LLM stops and reports back.
                            if let Ok(home) = std::env::var("HOME") {
                                let p = std::path::PathBuf::from(home).join(".jarvis/computer-use-stop");
                                if let Some(parent) = p.parent() {
                                    let _ = std::fs::create_dir_all(parent);
                                }
                                match std::fs::write(&p, "stop\n") {
                                    Ok(_)  => println!("[JARVIS] computer-use stop signaled at {}", p.display()),
                                    Err(e) => eprintln!("[JARVIS] failed to write {}: {e}", p.display()),
                                }
                            }
                        }
                        "open_browser" => {
                            // Pick the first port that responds AND
                            // is the JARVIS web (Next.js). Probe
                            // checks for a JARVIS-specific marker
                            // header / path so we don't accidentally
                            // open Open-WebUI on :3000 or some other
                            // app the user has running. Override
                            // with JARVIS_WEB_URL to skip detection.
                            let url = std::env::var("JARVIS_WEB_URL").ok()
                                .or_else(|| {
                                    for port in [3001u16, 3002, 3000, 8765] {
                                        let probe_url = format!("http://127.0.0.1:{port}/api/conversations");
                                        if let Ok(mut stream) = std::net::TcpStream::connect_timeout(
                                            &format!("127.0.0.1:{port}").parse().unwrap(),
                                            std::time::Duration::from_millis(150),
                                        ) {
                                            use std::io::{Read, Write};
                                            let _ = stream.set_read_timeout(Some(std::time::Duration::from_millis(300)));
                                            let req = format!(
                                                "GET /api/conversations HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n",
                                            );
                                            let _ = stream.write_all(req.as_bytes());
                                            let mut buf = [0u8; 256];
                                            if let Ok(n) = stream.read(&mut buf) {
                                                let head = String::from_utf8_lossy(&buf[..n]);
                                                // JARVIS web has /api/conversations → 200 with json content-type;
                                                // open-webui returns 404 / different shape.
                                                if head.starts_with("HTTP/1.1 200")
                                                    && head.contains("application/json")
                                                {
                                                    let _ = probe_url;
                                                    return Some(format!("http://127.0.0.1:{port}/"));
                                                }
                                            }
                                        }
                                    }
                                    None
                                })
                                .unwrap_or_else(|| "http://127.0.0.1:3001/".to_string());
                            let _ = std::process::Command::new("xdg-open")
                                .arg(&url)
                                .spawn();
                        }
                        "model_deepseek-chat"                              => switch_cli_model(app, "deepseek-chat"),
                        "model_deepseek-reasoner"                          => switch_cli_model(app, "deepseek-reasoner"),
                        "model_deepseek-v4-flash"                          => switch_cli_model(app, "deepseek-v4-flash"),
                        "model_deepseek-v4-pro"                            => switch_cli_model(app, "deepseek-v4-pro"),
                        "model_qwen/qwen3-32b"                             => switch_cli_model(app, "qwen/qwen3-32b"),
                        "model_llama-3.3-70b-versatile"                    => switch_cli_model(app, "llama-3.3-70b-versatile"),
                        "model_meta-llama/llama-4-scout-17b-16e-instruct"  => switch_cli_model(app, "meta-llama/llama-4-scout-17b-16e-instruct"),
                        "model_openai/gpt-oss-120b"                        => switch_cli_model(app, "openai/gpt-oss-120b"),
                        // Speech-model picks (these trigger an agent restart)
                        "speech_llama-3.3-70b-versatile"                   => switch_speech_model(app, "llama-3.3-70b-versatile"),
                        "speech_llama-3.1-8b-instant"                      => switch_speech_model(app, "llama-3.1-8b-instant"),
                        "speech_qwen/qwen3-32b"                            => switch_speech_model(app, "qwen/qwen3-32b"),
                        "speech_openai/gpt-oss-120b"                       => switch_speech_model(app, "openai/gpt-oss-120b"),
                        "speech_meta-llama/llama-4-scout-17b-16e-instruct" => switch_speech_model(app, "meta-llama/llama-4-scout-17b-16e-instruct"),
                        // TTS-voice picks (no agent restart — file written, read on next utterance)
                        "tts_el_george" => switch_tts_provider(app, "elevenlabs:JBFqnCBsd6RMkjVDRZzb"),
                        "tts_el_adam"   => switch_tts_provider(app, "elevenlabs:pNInz6obpgDQGcFmaJgB"),
                        "tts_el_brian"  => switch_tts_provider(app, "elevenlabs:nPczCjzI2devNBz1zQrb"),
                        "tts_gr_troy"   => switch_tts_provider(app, "groq:troy"),
                        "tts_gr_austin" => switch_tts_provider(app, "groq:austin"),
                        "quit" => {
                            // "Quit JARVIS" must stop everything the user
                            // perceives as JARVIS — not just the overlay.
                            // The voice agent + voice client run as
                            // separate systemd user units and would
                            // happily keep listening if we only called
                            // app.exit(0). Stop them first, then exit.
                            //
                            // Spawn detached so we don't block the tray
                            // event handler. Failure is non-fatal — if
                            // the units were already stopped or systemctl
                            // isn't available, the desktop still exits.
                            let _ = std::process::Command::new("systemctl")
                                .args([
                                    "--user", "stop",
                                    "jarvis-voice-agent",
                                    "jarvis-voice-client",
                                ])
                                .spawn();
                            // Give systemctl ~500 ms to issue the SIGTERM
                            // before we kill the desktop, so the agent
                            // gets a chance to clean up SFU room state.
                            std::thread::sleep(std::time::Duration::from_millis(500));
                            app.exit(0);
                        }
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
            set_provider_label,
            set_speech_label,
            set_tts_label,
            get_primary_monitor_info,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
