#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::sync::{Arc, Mutex};
use tauri::{
    AppHandle, Manager, State, WebviewWindow, PhysicalSize, PhysicalPosition,
    image::Image,
    menu::{MenuBuilder, MenuItem, MenuItemBuilder, PredefinedMenuItem, Submenu, SubmenuBuilder},
    tray::{TrayIcon, TrayIconBuilder},
    Emitter, Wry,
};
use tauri_plugin_opener::OpenerExt;

pub mod kiosk;
pub mod tray_kiosk;
mod supervisor;

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

/// Handle to the "Start / Stop Screen Share" tray entry. Stashed in
/// state so the `set_share_label` command can rewrite it from the
/// status poll — label flips to "Stop Screen Share ✓" when the
/// voice-client is publishing, "Start Screen Share" when not.
struct ShareLabel(Mutex<Option<MenuItem<Wry>>>);

/// Handle to the "Active: …" header line inside the Conversation
/// mode submenu. Refreshed every 3 s by a background task that
/// checks systemd --user state of the gemini/gpt direct-mode units.
struct ModeLabel(Mutex<Option<MenuItem<Wry>>>);

/// The three mode-switch menu items (jarvis / gemini / openai). Stored
/// so the refresh task can add/remove the "✓ " prefix on whichever
/// mode is currently active. Ordered: [jarvis, gemini, openai].
struct ModeItems(Mutex<Vec<MenuItem<Wry>>>);

/// Microphone + Speaker device-picker items, retained so the ✓ can be
/// repainted onto the just-selected device after a pick. Each entry is
/// (device-name or the sentinel "__default__", item).
#[derive(Default)]
struct AudioItemsInner {
    input:  Vec<(String, MenuItem<Wry>)>,
    output: Vec<(String, MenuItem<Wry>)>,
}
struct AudioItems(Mutex<AudioItemsInner>);

/// Kokoro-voice picker items, retained for ✓ repaint.
#[derive(Default)]
struct LocalVoiceItemsInner {
    voice: Vec<(String, MenuItem<Wry>)>,
}
struct LocalVoiceItems(Mutex<LocalVoiceItemsInner>);

/// (value, label) for the local Kokoro voice picker (curated subset of 54).
const KOKORO_VOICE_CHOICES: &[(&str, &str)] = &[
    ("af_heart",    "Heart  (US female, default)"),
    ("af_bella",    "Bella  (US female)"),
    ("af_nicole",   "Nicole  (US female)"),
    ("am_michael",  "Michael  (US male)"),
    ("am_onyx",     "Onyx  (US male, deep)"),
    ("am_puck",     "Puck  (US male)"),
    ("bf_emma",     "Emma  (UK female)"),
    ("bf_isabella", "Isabella  (UK female)"),
    ("bm_george",   "George  (UK male)"),
    ("bm_daniel",   "Daniel  (UK male)"),
];

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
///
/// `sharing` adds a magenta outer ring on top of the state-tinted icon
/// to indicate the screen-share track is live (added 2026-05-11).
/// Two-axis indicator: center colour = voice state, ring = sharing
/// on/off. Both signals visible simultaneously.
// ═══════════════════════════════════════════════════════════════════════
// FROZEN INDICATOR — do NOT modify without explicit user sign-off (2026-05-20).
// The state→colour map below, the magenta share-ring (apply_sharing_ring), the
// 7 states, the React→Rust poll rate, and icons/tray.png are FINAL. The user
// was repeatedly frustrated by churn here (ring px, colours, poll rate:
// 030378c0 / 21ec58b6 / e8cbdc31 / 702a1eb6). Don't "improve" it.
// See .claude/rules/desktop-tauri.md → "The system-tray indicator is FROZEN".
// ═══════════════════════════════════════════════════════════════════════
fn tray_image_for(state: &str, sharing: bool) -> (u32, u32, Vec<u8>) {
    // Colours chosen to match the VoiceClientPill (top-right of the
    // overlay) 1:1 so the tray and the pill never tell you different
    // stories. Tuned to read clearly against both dark and light XFCE
    // panels.
    let (w, h, mut rgba) = match state {
        "talking"   => tint_source( 68, 147, 248),   // blue   — JARVIS speaking
        "listening" => tint_source( 34, 211, 238),   // cyan   — you speaking
        "booting"   => tint_source(168,  85, 247),   // purple — service cold-starting
        "thinking"  => tint_source(250, 180,  50),   // amber  — LLM generating / tool running
        "muted"     => tint_source( 20,  20,  20),   // black  — mic muted
        "offline"   => tint_source(239,  68,  68),   // red    — voice client down
        _           => tint_source( 63, 185,  80),   // green  — idle / ready
    };
    if sharing {
        apply_sharing_ring(&mut rgba, w, h);
    }
    (w, h, rgba)
}


/// Paint a magenta ring at the outer edge of the tray buffer to
/// indicate JARVIS is observing a live screen-share. Sits in the
/// transparent margin around the existing concentric-ring icon —
/// doesn't overwrite the state-tinted body, just adds a halo.
///
/// Magenta (236, 72, 153) is distinct from all 7 state colours
/// (red/green/blue/cyan/purple/amber/black) so the ring is
/// unmistakable against any state.
fn apply_sharing_ring(rgba: &mut [u8], w: u32, h: u32) {
    let cx = w as f32 / 2.0;
    let cy = h as f32 / 2.0;
    // Ring width bumped from 3px to 5px on 2026-05-11 evening after
    // the user reported the indicator wasn't visible. Most Linux
    // panels downscale the 48x48 icon to ~22-28px — a 3px ring becomes
    // ~1.5px wide which is easy to miss. 5px stays clearly visible
    // even at 22px panel size.
    let outer = (w.min(h) as f32) / 2.0 - 0.5;
    let inner = outer - 5.0;
    // Brighter magenta-pink for high contrast against any state colour.
    const MR: u8 = 255;
    const MG: u8 =  20;
    const MB: u8 = 147;
    for y in 0..h {
        for x in 0..w {
            let dx = x as f32 + 0.5 - cx;
            let dy = y as f32 + 0.5 - cy;
            let d = (dx * dx + dy * dy).sqrt();
            // Anti-aliased band: fully opaque in [inner, outer-0.5],
            // soft edge for the outermost half-pixel so the ring
            // doesn't shimmer against the tray background.
            let alpha: f32 = if d >= inner && d <= outer - 0.5 {
                1.0
            } else if d > outer - 0.5 && d <= outer {
                outer - d + 0.5
            } else if d < inner && d >= inner - 0.5 {
                d - inner + 0.5
            } else {
                0.0
            };
            if alpha <= 0.0 {
                continue;
            }
            let i = ((y * w + x) * 4) as usize;
            // Composite the magenta ring OVER the existing pixel
            // (most of which is transparent here, so this is just
            // a write — but the alpha math handles the rare overlap
            // with the icon's own outermost ring gracefully).
            let a = (alpha * 255.0).clamp(0.0, 255.0) as u8;
            rgba[i]     = MR;
            rgba[i + 1] = MG;
            rgba[i + 2] = MB;
            rgba[i + 3] = rgba[i + 3].max(a);
        }
    }
}

// ── Helpers ────────────────────────────────────────────────────────────────

// ── Tauri commands called from JS ──────────────────────────────────────────

// ── API key management ────────────────────────────────────────────────────
// Two-tier key storage:
//   1) ~/.jarvis/keys.env (user override, highest priority)
//   2) Repo defaults, first-wins order:
//        .env  (centralized LLM provider keys, 2026-05-15)
//        src/voice-agent/.env
//        src/cli/.env.local
// keys_read merges them with source labels. keys_set always writes to
// the user override. keys_clear can target either tier (with
// confirmation handled by the UI). src/cli/.env.providers was removed
// 2026-05-15 (all values were placeholders duplicated from the canonical
// sources).
/// Cross-platform home directory. Windows has no $HOME — use %USERPROFILE%;
/// Unix uses $HOME. Falls back to /tmp only as a last resort. Before this,
/// every `std::env::var("HOME")` site below silently resolved to `/tmp` on
/// Windows (caught on the 2026-06-18 Windows deploy: the API-keys panel saved
/// keys to C:\tmp\.jarvis\keys.env, where the agent/CLI never looked).
fn jarvis_home() -> std::path::PathBuf {
    #[cfg(windows)]
    let var = "USERPROFILE";
    #[cfg(not(windows))]
    let var = "HOME";
    std::path::PathBuf::from(std::env::var(var).unwrap_or_else(|_| "/tmp".to_string()))
}

/// Build a Command that won't flash a console window when spawned from the
/// desktop (a GUI app with no console of its own). On Windows, launching a
/// console child (powershell, bun, curl) pops a cmd window unless
/// CREATE_NO_WINDOW is set — which is why "Open in Browser" (bun run dev) and
/// the model-switch restart showed a terminal. No-op on Unix. Use this in
/// place of Command::new for anything the desktop spawns.
fn hidden_command<S: AsRef<std::ffi::OsStr>>(program: S) -> std::process::Command {
    let mut cmd = std::process::Command::new(program);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }
    cmd
}

/// Repo checkout root. Derived from the running exe when possible
/// (`<repo>/src/desktop-tauri/src-tauri/target/release/jarvis-desktop[.exe]`,
/// 6 ancestors up), else the platform-conventional Documents location (the
/// Linux dev box uses `~/Documents/Projects/jarvis`; the Windows installer
/// clones to `~/Documents/jarvis`).
fn repo_root() -> std::path::PathBuf {
    if let Ok(exe) = std::env::current_exe() {
        if let Some(root) = exe.ancestors().nth(6) {
            if root.join("CLAUDE.md").exists() {
                return root.to_path_buf();
            }
        }
    }
    #[cfg(windows)]
    { jarvis_home().join("Documents").join("jarvis") }
    #[cfg(not(windows))]
    { jarvis_home().join("Documents").join("Projects").join("jarvis") }
}

/// Restart the voice agent, cross-platform. Linux bounces the systemd
/// --user unit; Windows re-runs the voice launcher the installer drops in
/// `~/.jarvis` (the Windows voice stack runs in the USER session — not a
/// service — so the mic/speakers work). Returns a human-readable error
/// instead of the bare "program not found" the raw `systemctl` call gave on
/// Windows.
fn restart_voice_agent_cmd() -> Result<(), String> {
    #[cfg(not(windows))]
    {
        let out = std::process::Command::new("systemctl")
            .args(["--user", "restart", "jarvis-voice-agent.service"])
            .output()
            .map_err(|e| e.to_string())?;
        if !out.status.success() {
            return Err(String::from_utf8_lossy(&out.stderr).to_string());
        }
        Ok(())
    }
    #[cfg(windows)]
    {
        let home = jarvis_home();
        let stop = home.join(".jarvis").join("stop-jarvis-voice.ps1");
        let start = home.join(".jarvis").join("start-jarvis-voice.ps1");
        if !start.exists() {
            return Err(format!(
                "voice launcher not found at {} — re-run the Windows installer",
                start.display()
            ));
        }
        if stop.exists() {
            let _ = hidden_command("powershell")
                .args(["-NoProfile", "-ExecutionPolicy", "Bypass", "-File"])
                .arg(&stop)
                .output();
        }
        let out = hidden_command("powershell")
            .args(["-NoProfile", "-ExecutionPolicy", "Bypass", "-File"])
            .arg(&start)
            .output()
            .map_err(|e| e.to_string())?;
        if !out.status.success() {
            return Err(String::from_utf8_lossy(&out.stderr).to_string());
        }
        Ok(())
    }
}

fn _keys_file() -> std::path::PathBuf {
    jarvis_home().join(".jarvis").join("keys.env")
}

fn _repo_env_files() -> Vec<std::path::PathBuf> {
    let base = repo_root();
    vec![
        base.join(".env"),                  // centralized LLM keys (first-wins)
        base.join("src/voice-agent/.env"),
        base.join("src/cli/.env.local"),
    ]
}

/// Returns true if the voice agent had a turn within the last 60 s.
/// Uses sqlite3 (already on the box) instead of pulling rusqlite into
/// the desktop binary. Errors / missing DB / no rows → returns false:
/// "no evidence of an active session" is the conservative default for
/// a destructive UI action like Quit.
///
/// Mirrors the CLI's `checkActiveSession` in
/// `src/cli/src/commands/voice/restart.ts`. CLAUDE.md operational
/// rule: "Don't restart `jarvis-voice-agent.service` while a session
/// is active. Check `~/.local/share/jarvis/turn_telemetry.db` for the
/// latest `ts_utc`; if within 60 s, ask the user first."
///
/// Currently no production callers — the tray-Quit handler used to
/// consult this and skip systemctl stop on a recent turn, but that
/// was wrong UX (Quit is explicit user intent, they DO want to
/// interrupt) and was removed 2026-05-11. Kept for the unit tests
/// and as a building block if a future "Pause" / "Restart" tray
/// option wants the same guard.
#[allow(dead_code)]
fn voice_session_within_60s() -> bool {
    let db_path = jarvis_home().join(".local/share/jarvis/turn_telemetry.db");
    voice_session_within_60s_at(&db_path)
}

/// Path-parameterized inner — split out so unit tests can drop a
/// fixture DB into a tempdir and exercise all branches without
/// touching the real $HOME store.
fn voice_session_within_60s_at(db_path: &std::path::Path) -> bool {
    if !db_path.exists() {
        return false;
    }
    let output = match std::process::Command::new("sqlite3")
        .args([
            db_path.to_string_lossy().as_ref(),
            // sqlite's strftime parses ISO 8601 timestamps; voice-agent
            // writes ts_utc as an ISO string. Returning age in seconds
            // keeps the parse trivial on this side.
            "SELECT CAST((strftime('%s','now') - strftime('%s', ts_utc)) AS INTEGER) \
             FROM turns ORDER BY ts_utc DESC LIMIT 1",
        ])
        .output()
    {
        Ok(o) if o.status.success() => o,
        _ => return false,
    };
    let stdout = String::from_utf8_lossy(&output.stdout);
    match stdout.trim().parse::<i64>() {
        Ok(age_secs) => age_secs < 60 && age_secs >= 0,
        Err(_) => false,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;
    use std::process::Command;

    fn make_telemetry_db(dir: &std::path::Path, ts_iso: Option<&str>) -> PathBuf {
        let path = dir.join("telemetry.db");
        // Match the voice-agent schema enough to satisfy the query.
        // ts_utc TEXT NOT NULL is the only column the function reads.
        let create = "CREATE TABLE turns (id INTEGER PRIMARY KEY, ts_utc TEXT NOT NULL);";
        Command::new("sqlite3")
            .arg(&path)
            .arg(create)
            .status()
            .expect("sqlite3 create");
        if let Some(ts) = ts_iso {
            Command::new("sqlite3")
                .arg(&path)
                .arg(format!("INSERT INTO turns (ts_utc) VALUES ('{ts}');"))
                .status()
                .expect("sqlite3 insert");
        }
        path
    }

    fn iso_seconds_ago(secs: i64) -> String {
        let out = Command::new("date")
            .args(["-u", "-d", &format!("@{}", chrono_like_now() - secs), "+%Y-%m-%dT%H:%M:%SZ"])
            .output()
            .expect("date");
        String::from_utf8(out.stdout).unwrap().trim().to_string()
    }

    fn chrono_like_now() -> i64 {
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_secs() as i64)
            .unwrap_or(0)
    }

    #[test]
    fn returns_false_when_db_missing() {
        let tmp = tempdir_safe();
        let path = tmp.join("does-not-exist.db");
        assert!(!voice_session_within_60s_at(&path));
    }

    #[test]
    fn returns_false_when_table_empty() {
        let tmp = tempdir_safe();
        let path = make_telemetry_db(&tmp, None);
        assert!(!voice_session_within_60s_at(&path));
    }

    #[test]
    fn returns_true_for_recent_turn() {
        let tmp = tempdir_safe();
        let path = make_telemetry_db(&tmp, Some(&iso_seconds_ago(10)));
        assert!(voice_session_within_60s_at(&path));
    }

    #[test]
    fn returns_false_for_stale_turn() {
        let tmp = tempdir_safe();
        let path = make_telemetry_db(&tmp, Some(&iso_seconds_ago(120)));
        assert!(!voice_session_within_60s_at(&path));
    }

    #[test]
    fn returns_false_when_age_negative() {
        // Future ts_utc (clock skew, unrealistic but possible) → safe path
        // is "no active session" rather than "lock the user out of Quit"
        let tmp = tempdir_safe();
        let path = make_telemetry_db(&tmp, Some(&iso_seconds_ago(-30)));
        assert!(!voice_session_within_60s_at(&path));
    }

    #[test]
    fn token_from_file_returns_value_when_present() {
        let tmp = tempdir_safe();
        let path = tmp.join("local-api-token.env");
        std::fs::write(&path, "JARVIS_LOCAL_API_TOKEN=deadbeef\n").unwrap();
        assert_eq!(local_api_token_from_file(&path), "deadbeef");
    }

    #[test]
    fn token_from_file_returns_empty_when_missing_file() {
        let tmp = tempdir_safe();
        let path = tmp.join("does-not-exist.env");
        assert_eq!(local_api_token_from_file(&path), "");
    }

    #[test]
    fn token_from_file_returns_empty_when_key_absent() {
        let tmp = tempdir_safe();
        let path = tmp.join("other-keys.env");
        std::fs::write(&path, "SOME_OTHER_KEY=value\n").unwrap();
        assert_eq!(local_api_token_from_file(&path), "");
    }

    #[test]
    fn token_from_file_strips_surrounding_quotes() {
        let tmp = tempdir_safe();
        let path = tmp.join("quoted.env");
        std::fs::write(&path, "JARVIS_LOCAL_API_TOKEN=\"abc123\"\n").unwrap();
        assert_eq!(local_api_token_from_file(&path), "abc123");
    }

    fn tempdir_safe() -> PathBuf {
        let pid = std::process::id();
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.subsec_nanos())
            .unwrap_or(0);
        let p = std::env::temp_dir().join(format!("jarvis-desktop-test-{pid}-{nanos}"));
        std::fs::create_dir_all(&p).expect("mkdir tempdir");
        p
    }
}

fn _parse_env_file(path: &std::path::Path) -> std::collections::BTreeMap<String, String> {
    let mut out = std::collections::BTreeMap::new();
    if let Ok(text) = std::fs::read_to_string(path) {
        for line in text.lines() {
            let line = line.trim();
            if line.is_empty() || line.starts_with('#') { continue }
            if let Some((k, v)) = line.split_once('=') {
                let k = k.trim().to_string();
                let v = v.trim().trim_matches('"').trim_matches('\'').to_string();
                if !k.is_empty() { out.insert(k, v); }
            }
        }
    }
    out
}

fn _keys_read_map() -> std::collections::BTreeMap<String, String> {
    _parse_env_file(&_keys_file())
}

/// Bridge bearer token. Env var first, then fall back to parsing
/// ~/.jarvis/local-api-token.env (the file `start-desktop.sh` writes
/// + exports on launch). Without the fallback, a desktop binary
/// launched directly — double-click, XFCE autostart, `cargo run`,
/// `target/release/jarvis-desktop` — has an empty env and every tray
/// action that POSTs to the bridge gets rejected with 401 when
/// `JARVIS_REQUIRE_LOCAL_AUTH=1`. Live failure 2026-05-28: tray Mute
/// silently 401'd, `voice_muted` broadcast never fired, UI showed no
/// muted state. (The mic still toggled via the unauthenticated
/// voice-client `/mute` on :8767, so the bug presented as "click does
/// nothing" rather than a visible error.) Read on every call — the
/// file is one line and reads are cheap; the alternative (cache once
/// at startup) would miss token rotations that don't restart the
/// desktop.
fn local_api_token() -> String {
    let env = std::env::var("JARVIS_LOCAL_API_TOKEN").unwrap_or_default();
    if !env.is_empty() {
        return env;
    }
    let path = jarvis_home().join(".jarvis").join("local-api-token.env");
    local_api_token_from_file(&path)
}

/// Pure file-path variant for unit testing without mutating $HOME or
/// $JARVIS_LOCAL_API_TOKEN (env mutation is unsafe across parallel
/// cargo tests). Mirrors the voice_session_within_60s / _at pattern.
fn local_api_token_from_file(path: &std::path::Path) -> String {
    _parse_env_file(path)
        .get("JARVIS_LOCAL_API_TOKEN")
        .cloned()
        .unwrap_or_default()
}

/// Read all repo .env files and return one merged map.
/// Earlier files in the list win on collision.
fn _repo_keys_read_map() -> std::collections::BTreeMap<String, String> {
    let mut out = std::collections::BTreeMap::new();
    for path in _repo_env_files() {
        for (k, v) in _parse_env_file(&path) {
            out.entry(k).or_insert(v);   // first-wins
        }
    }
    out
}

/// Find which repo .env file contains the given key. Returns the
/// first match (we try in priority order: root .env → voice-agent →
/// cli/.env.local). Used by keys_clear to know which file to modify.
fn _find_repo_key_file(key: &str) -> Option<std::path::PathBuf> {
    for path in _repo_env_files() {
        if _parse_env_file(&path).contains_key(key) {
            return Some(path);
        }
    }
    None
}

/// Remove `key=...` line(s) from a repo .env file. Preserves comments
/// and other lines. Returns Ok(true) if a line was removed.
fn _remove_key_from_file(path: &std::path::Path, key: &str) -> Result<bool, String> {
    let text = std::fs::read_to_string(path).map_err(|e| e.to_string())?;
    let mut changed = false;
    let kept: Vec<&str> = text.lines().filter(|line| {
        let trimmed = line.trim();
        if trimmed.starts_with('#') || !trimmed.contains('=') { return true; }
        let starts_with_key = trimmed
            .split_once('=')
            .map(|(k, _)| k.trim() == key)
            .unwrap_or(false);
        if starts_with_key { changed = true; false } else { true }
    }).collect();
    if changed {
        let mut new_text = kept.join("\n");
        if !new_text.ends_with('\n') { new_text.push('\n'); }
        std::fs::write(path, new_text).map_err(|e| e.to_string())?;
    }
    Ok(changed)
}

fn _keys_write_map(map: &std::collections::BTreeMap<String, String>) -> Result<(), String> {
    let path = _keys_file();
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    }
    let mut buf = String::from("# JARVIS API keys — managed by the tray UI.\n");
    buf.push_str("# Lines here OVERRIDE keys in repo-root .env, src/voice-agent/.env, and src/cli/.env.local.\n");
    buf.push_str("# Empty value = key not set.\n\n");
    for (k, v) in map.iter() {
        if v.is_empty() { continue; }
        buf.push_str(&format!("{k}={v}\n"));
    }
    std::fs::write(&path, buf).map_err(|e| e.to_string())?;
    // chmod 600 — secrets file
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600));
    }
    Ok(())
}

#[tauri::command]
fn keys_read() -> Result<Vec<serde_json::Value>, String> {
    // Catalogue of providers the UI shows. Adding a new provider here
    // is the only change needed to surface a new row.
    const PROVIDERS: &[(&str, &str)] = &[
        ("GROQ_API_KEY",      "Groq"),
        ("DEEPSEEK_API_KEY",  "DeepSeek"),
        ("OPENAI_API_KEY",    "OpenAI"),
        ("ANTHROPIC_API_KEY", "Anthropic"),
        ("GOOGLE_API_KEY",    "Google (Gemini + Geolocation)"),
        ("MISTRAL_API_KEY",   "Mistral"),
        ("KIMI_API_KEY",      "Kimi (Moonshot)"),
        ("XAI_API_KEY",       "xAI (Grok)"),
        // STT / voice provider keys (not LLMs) — surfaced here so the panel
        // can manage them too. Deepgram is the primary streaming-STT path
        // (fast barge-in); blank = degrade to Groq Whisper / local whisper.
        ("DEEPGRAM_API_KEY",  "Deepgram (streaming STT)"),
    ];
    let user_keys = _keys_read_map();
    let repo_keys = _repo_keys_read_map();
    let mut out = Vec::with_capacity(PROVIDERS.len());
    for (env, label) in PROVIDERS {
        // User keys.env takes precedence over repo .env defaults.
        let (value, source) = if let Some(v) = user_keys.get(*env) {
            (v.clone(), "user")
        } else if let Some(v) = repo_keys.get(*env) {
            (v.clone(), "repo")
        } else {
            (String::new(), "none")
        };
        let masked = if value.is_empty() {
            String::new()
        } else if value.len() > 8 {
            // Show first 4 + last 4, hide middle. Lets user verify they
            // pasted the right key without exposing it.
            format!("{}…{}", &value[..4], &value[value.len()-4..])
        } else {
            "•••••".to_string()
        };
        out.push(serde_json::json!({
            "env":     env,
            "label":   label,
            "present": !value.is_empty(),
            "source":  source,        // "user" | "repo" | "none"
            "masked":  masked,
        }));
    }
    Ok(out)
}

// ── MCP servers (~/.jarvis/mcp.json) — desktop mirror of the web
// Settings → Connectors card. Lets the desktop list / toggle / remove /
// add the MCP servers the voice agent + web chat use. OAuth sign-in
// (Vercel / Notion) stays in the web app — it needs the browser flow —
// so this handles the token-based + local servers and on/off control.
// Same {"servers": {name: spec}} shape the web store reads/writes.
fn _mcp_path() -> std::path::PathBuf {
    jarvis_home().join(".jarvis").join("mcp.json")
}

fn _mcp_read_doc() -> serde_json::Value {
    let txt = std::fs::read_to_string(_mcp_path()).unwrap_or_default();
    let parsed: serde_json::Value =
        serde_json::from_str(&txt).unwrap_or_else(|_| serde_json::json!({}));
    // Normalize to { "servers": { … } }; accept a bare {name: spec} map too.
    if parsed.get("servers").map(|s| s.is_object()).unwrap_or(false) {
        parsed
    } else if parsed.is_object() {
        serde_json::json!({ "servers": parsed })
    } else {
        serde_json::json!({ "servers": {} })
    }
}

fn _mcp_write_doc(doc: &serde_json::Value) -> Result<(), String> {
    let path = _mcp_path();
    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let body = serde_json::to_string_pretty(doc).map_err(|e| e.to_string())? + "\n";
    let tmp = path.with_extension("json.tmp");
    std::fs::write(&tmp, body).map_err(|e| e.to_string())?;
    std::fs::rename(&tmp, &path).map_err(|e| e.to_string())?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        // mcp.json can hold a Bearer token → keep it owner-only.
        let _ = std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600));
    }
    Ok(())
}

#[tauri::command]
fn mcp_list() -> Result<Vec<serde_json::Value>, String> {
    let doc = _mcp_read_doc();
    let servers = doc
        .get("servers")
        .and_then(|s| s.as_object())
        .cloned()
        .unwrap_or_default();
    let mut out = Vec::new();
    for (name, spec) in servers {
        let transport = spec
            .get("transport")
            .and_then(|v| v.as_str())
            .map(String::from)
            .unwrap_or_else(|| {
                if spec.get("url").is_some() { "http".into() } else { "stdio".into() }
            });
        let url = spec.get("url").and_then(|v| v.as_str()).unwrap_or("").to_string();
        // enabled = NOT (disabled==true OR enabled==false) — the voice loader's rule.
        let disabled = spec.get("disabled").and_then(|v| v.as_bool()).unwrap_or(false);
        let enabled = !(disabled || spec.get("enabled").and_then(|v| v.as_bool()) == Some(false));
        let has_auth = spec
            .get("headers")
            .and_then(|h| h.as_object())
            .map(|h| !h.is_empty())
            .unwrap_or(false);
        let oauth = spec.get("oauth").and_then(|v| v.as_bool()).unwrap_or(false);
        out.push(serde_json::json!({
            "name": name, "transport": transport, "url": url,
            "enabled": enabled, "hasAuth": has_auth, "oauth": oauth,
        }));
    }
    out.sort_by(|a, b| a["name"].as_str().unwrap_or("").cmp(b["name"].as_str().unwrap_or("")));
    Ok(out)
}

#[tauri::command]
fn mcp_set_enabled(name: String, enabled: bool) -> Result<(), String> {
    let mut doc = _mcp_read_doc();
    let servers = doc
        .get_mut("servers")
        .and_then(|s| s.as_object_mut())
        .ok_or("bad mcp.json")?;
    let spec = servers
        .get_mut(&name)
        .and_then(|s| s.as_object_mut())
        .ok_or("server not found")?;
    if enabled {
        spec.remove("disabled");
        spec.remove("enabled");
    } else {
        spec.insert("disabled".into(), serde_json::Value::Bool(true));
    }
    _mcp_write_doc(&doc)
}

#[tauri::command]
fn mcp_remove(name: String) -> Result<(), String> {
    let mut doc = _mcp_read_doc();
    if let Some(servers) = doc.get_mut("servers").and_then(|s| s.as_object_mut()) {
        servers.remove(&name);
    }
    _mcp_write_doc(&doc)
}

#[tauri::command]
fn mcp_add(name: String, url: String, transport: String, token: String) -> Result<(), String> {
    let name = name.trim();
    let url = url.trim();
    if name.is_empty() || url.is_empty() {
        return Err("name and url required".into());
    }
    if !(url.starts_with("http://") || url.starts_with("https://")) {
        return Err("url must be http(s)".into());
    }
    let transport = if transport == "sse" { "sse" } else { "http" };
    let mut spec = serde_json::Map::new();
    spec.insert("transport".into(), serde_json::Value::String(transport.into()));
    spec.insert("url".into(), serde_json::Value::String(url.into()));
    let tok = token.trim();
    if !tok.is_empty() {
        let bearer = if tok.to_lowercase().starts_with("bearer ") {
            tok.to_string()
        } else {
            format!("Bearer {tok}")
        };
        let mut headers = serde_json::Map::new();
        headers.insert("Authorization".into(), serde_json::Value::String(bearer));
        spec.insert("headers".into(), serde_json::Value::Object(headers));
    }
    let mut doc = _mcp_read_doc();
    let servers = doc
        .get_mut("servers")
        .and_then(|s| s.as_object_mut())
        .ok_or("bad mcp.json")?;
    servers.insert(name.to_string(), serde_json::Value::Object(spec));
    _mcp_write_doc(&doc)
}

#[tauri::command]
fn keys_set(provider: String, value: String) -> Result<(), String> {
    let provider = provider.trim().to_string();
    if provider.is_empty() { return Err("empty provider".into()); }
    if !provider.chars().all(|c| c.is_ascii_alphanumeric() || c == '_') {
        return Err("invalid provider name".into());
    }
    let mut map = _keys_read_map();
    let value = value.trim().to_string();
    if value.is_empty() {
        map.remove(&provider);
    } else {
        map.insert(provider, value);
    }
    _keys_write_map(&map)
}

#[tauri::command]
fn keys_clear(provider: String, source: String) -> Result<String, String> {
    // source: "user" → only clear from ~/.jarvis/keys.env
    //         "repo" → also remove from src/voice-agent/.env or cli/.env.local
    //         "all"  → clear from BOTH (keys.env + the repo file)
    let provider = provider.trim().to_string();
    let mut details: Vec<String> = Vec::new();
    let s = source.trim().to_lowercase();

    // Always clear from user keys.env if requested
    if s == "user" || s == "all" {
        let mut map = _keys_read_map();
        if map.remove(&provider).is_some() {
            _keys_write_map(&map)?;
            details.push("removed from ~/.jarvis/keys.env".to_string());
        }
    }
    // Repo .env removal — only when explicitly requested
    if s == "repo" || s == "all" {
        if let Some(path) = _find_repo_key_file(&provider) {
            let removed = _remove_key_from_file(&path, &provider)?;
            if removed {
                let p_str = path.to_string_lossy().to_string();
                details.push(format!("removed from {}", p_str));
            }
        }
    }
    if details.is_empty() {
        return Ok(format!("nothing to clear for {provider}"));
    }
    Ok(details.join("; "))
}

#[tauri::command]
fn keys_restart_agent() -> Result<(), String> {
    // After saving, the user usually wants to apply changes. Restart
    // the voice-agent service so the new keys are loaded.
    restart_voice_agent_cmd()
}

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
/// Accepted values: "idle" | "listening" | "talking" | "booting" | "thinking" | "offline" | "muted".
/// "idle" and "listening" both render green (the mic is live either way).
///
/// `sharing` overlays a magenta outer ring on top of the state tint
/// to indicate the voice-client is publishing the screen-share track.
/// Two-axis indicator: center colour = voice state, ring = sharing on.
#[tauri::command]
fn set_tray_state(state: &str, sharing: bool, tray: State<TrayHandle>) -> Result<(), String> {
    let (w, h, rgba) = tray_image_for(state, sharing);
    let image = Image::new_owned(rgba, w, h);
    let guard = tray.0.lock().map_err(|e| e.to_string())?;
    if let Some(t) = guard.as_ref() {
        t.set_icon(Some(image)).map_err(|e| e.to_string())?;
    }
    Ok(())
}

/// Map a CLI model ID to a short pretty label for the tray.
/// Pretty label for the active CLI/tool model id.
/// 2026-05-18: pruned to the 6 curated entries that match
/// voice_client_tray_config.py CLI_MODELS_AVAILABLE. Dropped IDs
/// (deepseek-*, llama-3.3-70b, llama-4-scout, gpt-oss-120b,
/// gpt-5-nano, gpt-5, gpt-5.1-chat-latest, gpt-4o) fall through to
/// None — the indicator caller renders the raw id in that case,
/// which only fires if state.db has a legacy value.
fn cli_model_pretty(id: &str) -> Option<&'static str> {
    match id {
        "claude-sonnet-4-6"                              => Some("Claude · Sonnet 4.6"),
        "claude-opus-4-7"                                => Some("Claude · Opus 4.7"),
        "claude-haiku-4-5"                               => Some("Claude · Haiku 4.5"),
        "gpt-5.1"                                        => Some("OpenAI · GPT-5.1"),
        "gpt-5-mini"                                     => Some("OpenAI · GPT-5 mini"),
        "qwen/qwen3-32b"                                 => Some("Groq · qwen3-32b"),
        "deepseek-v4-pro"                                => Some("DeepSeek · V4 Pro"),
        _ => None,
    }
}

/// Pretty label for the active speech model id.
/// 2026-05-18: pruned to the 6 curated entries that match
/// voice_client_tray_config.py SPEECH_MODELS_AVAILABLE. Dropped IDs
/// (llama-3.1-8b-instant, llama-3.3-70b-versatile, llama-4-scout,
/// gpt-oss-120b, deepseek-*, gpt-5-nano, gpt-5, gpt-5.1-chat-latest,
/// gpt-4o) fall through to None — the indicator caller renders the
/// raw id in that case.
fn speech_model_pretty(id: &str) -> Option<&'static str> {
    match id {
        "claude-haiku-4-5"                               => Some("Claude · Haiku 4.5"),
        "claude-sonnet-4-6"                              => Some("Claude · Sonnet 4.6"),
        "claude-opus-4-7"                                => Some("Claude · Opus 4.7"),
        "gpt-5-mini"                                     => Some("OpenAI · GPT-5 mini"),
        "gpt-5.1"                                        => Some("OpenAI · GPT-5.1"),
        "qwen/qwen3-32b"                                 => Some("Groq · qwen3-32b"),
        "ollama/qwen3:30b-a3b"                           => Some("Local · Qwen3 30B-A3B"),
        "ollama/gpt-oss:120b"                            => Some("Local · gpt-oss 120B"),
        "deepseek-v4-flash"                              => Some("DeepSeek · V4 Flash"),
        _ => None,
    }
}

/// True if a provider's API key is configured (process env, then
/// ~/.jarvis/keys.env, then repo .env). Used to hide tray model entries
/// whose provider key isn't set — no point offering a model that 401s.
fn provider_key_present(env_var: &str) -> bool {
    if std::env::var(env_var).map(|v| !v.trim().is_empty()).unwrap_or(false) {
        return true;
    }
    if _keys_read_map().get(env_var).map(|v| !v.trim().is_empty()).unwrap_or(false) {
        return true;
    }
    _repo_keys_read_map().get(env_var).map(|v| !v.trim().is_empty()).unwrap_or(false)
}

/// Names of models currently pulled into the local Ollama daemon
/// (GET /api/tags). Empty when Ollama isn't running or nothing is pulled —
/// so local menu entries only appear once a model is actually downloaded.
fn ollama_installed_models() -> std::collections::HashSet<String> {
    let mut set = std::collections::HashSet::new();
    if let Ok(o) = hidden_command("curl")
        .args(["-s", "--max-time", "2", "http://localhost:11434/api/tags"])
        .output()
    {
        if let Ok(json) = serde_json::from_slice::<serde_json::Value>(&o.stdout) {
            if let Some(arr) = json.get("models").and_then(|m| m.as_array()) {
                for m in arr {
                    if let Some(name) = m.get("name").and_then(|n| n.as_str()) {
                        set.insert(name.to_string());
                    }
                }
            }
        }
    }
    set
}

/// True if `target` (e.g. "qwen3:30b-a3b") is among the installed Ollama
/// models, tolerating a trailing ":tag" (":latest" etc.).
fn ollama_has(installed: &std::collections::HashSet<String>, target: &str) -> bool {
    installed.iter().any(|m| m == target || m.starts_with(&format!("{target}:")))
}

/// Switch the active CLI model by POSTing to the voice-client. The
/// voice-client persists the choice in `~/.jarvis/cli-model`; the
/// next run_jarvis_cli call picks it up via env vars. No process
/// restarts needed. Spawned via curl to avoid pulling reqwest.
fn switch_cli_model(app: &tauri::AppHandle, id: &'static str) {
    let body = format!(r#"{{"model":"{id}"}}"#);
    let _ = hidden_command("curl")
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
/// ElevenLabs entries removed 2026-05-01 — see jarvis_agent.py
/// _build_dispatching_tts comment.
const TTS_VOICES: &[(&str, &str)] = &[
    ("groq:troy",   "Groq Orpheus · Troy"),
    ("groq:austin", "Groq Orpheus · Austin"),
];

/// Map a TTS provider:voice spec to a short pretty label for the tray.
/// Mirrors TTS_PROVIDERS_AVAILABLE in jarvis_voice_client.py.
fn tts_provider_pretty(spec: &str) -> Option<&'static str> {
    // Local mode reports "kokoro:<voice>"; show the on-device engine (the
    // specific voice is reflected by the ✓ in the TTS-voice list).
    if spec.starts_with("kokoro:") {
        return Some("Kokoro (local)");
    }
    TTS_VOICES.iter().find(|(s, _)| *s == spec).map(|(_, l)| *l)
}

/// Switch the active TTS voice by POSTing to the voice-client.
/// Voice-client writes `~/.jarvis/tts-provider`; the agent reads it
/// on the next session start (or via _build_tts_chain on each call).
/// No agent restart needed — order shifts on next utterance.
fn switch_tts_provider(app: &tauri::AppHandle, spec: &'static str) {
    let body = format!(r#"{{"provider":"{spec}"}}"#);
    let _ = hidden_command("curl")
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

/// Switch the active speech model.
///
/// Writes the choice directly to `~/.jarvis/voice-model` and bounces
/// both the agent and voice-client via `systemctl --user`. Previously
/// this went through a `curl → /voice-model → write_text` chain which
/// was failing silently when the Tauri process's spawned curl couldn't
/// resolve the binary or reach the local HTTP server in some launch
/// environments (e.g. tray-icon click context), causing the tray pick
/// to never persist and the agent to revert to whatever was last in
/// the file on every restart. The voice-client's POST handler still
/// exists for external callers and as a fallback.
fn switch_speech_model(app: &tauri::AppHandle, id: &'static str) {
    // 1. Authoritative file write.
    let dir = jarvis_home().join(".jarvis");
    let file = dir.join("voice-model");
    let _ = std::fs::create_dir_all(&dir);
    match std::fs::write(&file, format!("{id}\n")) {
        Ok(()) => eprintln!("[tray] wrote voice-model: {id}"),
        Err(e) => eprintln!("[tray] failed to write voice-model: {e}"),
    }

    // 2. Restart the voice stack so it rebuilds the LLM with the new pick.
    //    Linux bounces the agent unit, then the client 4 s later (so the SFU
    //    dispatches a fresh job into the re-registered agent). Windows
    //    re-runs the launcher, which restarts agent + client together.
    #[cfg(not(windows))]
    {
        let _ = std::process::Command::new("systemctl")
            .args(["--user", "restart", "jarvis-voice-agent.service"])
            .spawn();
        std::thread::spawn(|| {
            std::thread::sleep(std::time::Duration::from_secs(4));
            let _ = std::process::Command::new("systemctl")
                .args(["--user", "restart", "jarvis-voice-client.service"])
                .spawn();
        });
    }
    #[cfg(windows)]
    {
        // Run the restart OFF the tray/UI thread — restart_voice_agent_cmd()
        // blocks ~5 s on the stop+start scripts (.output()), which froze the
        // whole UI on every model pick. Fire-and-forget, like the Linux spawn.
        std::thread::spawn(|| {
            let _ = restart_voice_agent_cmd();
        });
    }

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
            // Raw id fallback (same pattern as set_tts_label). The
            // realtime modes report ids outside the curated picker —
            // e.g. "gemini-3.1-flash-live-preview" from :8768 — and
            // an Err here left the line frozen on a stale value.
            None         => format!("Speech: {name}"),
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
/// React calls this whenever the active mode's `/status` reports a new
/// model ID. Empty string = "no choice yet". (The speech line has its
/// own setter above — set_speech_label.)
#[tauri::command]
fn set_provider_label(name: &str, label: State<ProviderLabel>) -> Result<(), String> {
    let text: String = if name.is_empty() {
        "Tool: (loading…)".to_string()
    } else {
        match cli_model_pretty(name) {
            Some(pretty) => format!("Tool: {pretty}"),
            // Raw id fallback — a legacy/unknown id in ~/.jarvis/cli-model
            // should display as itself, not freeze the line via Err.
            None         => format!("Tool: {name}"),
        }
    };
    let guard = label.0.lock().map_err(|e| e.to_string())?;
    if let Some(item) = guard.as_ref() {
        item.set_text(text).map_err(|e| e.to_string())?;
    }
    Ok(())
}

/// Update the "Start / Stop Screen Share" tray entry to reflect the
/// voice-client's current `sharing_screen` state. Called from the
/// React status poll once per /status tick — the label flips between
/// "Stop Screen Share ✓" (active) and "Start Screen Share" (idle) so
/// the user can tell at a glance without curling /status.
#[tauri::command]
fn set_share_label(active: bool, label: State<ShareLabel>) -> Result<(), String> {
    let text = if active { "Stop Screen Share  ✓" } else { "Start Screen Share" };
    let guard = label.0.lock().map_err(|e| e.to_string())?;
    if let Some(item) = guard.as_ref() {
        item.set_text(text).map_err(|e| e.to_string())?;
    }
    Ok(())
}

/// What jarvis-mode is currently driving. Determined by which systemd
/// --user transient unit is active. Mirrors the bash logic in
/// bin/jarvis-mode::current_mode.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum ActiveMode { Jarvis, Local, Gemini, Openai }

fn detect_active_mode() -> ActiveMode {
    // Gemini/OpenAI are separate direct-mode processes (tracked in
    // ~/.jarvis/active-mode on Windows, systemd units on Linux). The
    // JARVIS-Claude pipeline runs in two flavours selected by
    // ~/.jarvis/voice-mode: cloud (Jarvis) or on-device (Local).
    #[cfg(windows)]
    {
        let p = jarvis_home().join(".jarvis").join("active-mode");
        match std::fs::read_to_string(&p).ok().map(|s| s.trim().to_string()).as_deref() {
            Some("gemini") => return ActiveMode::Gemini,
            Some("openai") => return ActiveMode::Openai,
            _ => {}
        }
    }
    #[cfg(not(windows))]
    {
        let is_active = |unit: &str| -> bool {
            std::process::Command::new("systemctl")
                .args(["--user", "is-active", "--quiet", unit])
                .status()
                .map(|s| s.success())
                .unwrap_or(false)
        };
        if is_active("jarvis-gemini-tools.service") {
            return ActiveMode::Gemini;
        }
        if is_active("jarvis-gpt-tools.service") {
            return ActiveMode::Openai;
        }
    }
    if read_voice_mode() == "local" { ActiveMode::Local } else { ActiveMode::Jarvis }
}

/// Exposed to the React webview so the tray-icon poller can pick the
/// right `/status` URL: 8767 for JARVIS-Claude, 8768 for Gemini Live,
/// 8769 for OpenAI Realtime. Cheap enough to call every few seconds —
/// `systemctl --user is-active --quiet` returns in <10 ms on Linux.
///
/// Returns the string "jarvis" / "gemini" / "openai" so the JS side
/// doesn't have to learn the Rust enum tag scheme.
#[tauri::command]
fn get_active_mode() -> &'static str {
    match detect_active_mode() {
        // Local is the JARVIS-Claude pipeline on-device — same :8767 status URL.
        ActiveMode::Jarvis | ActiveMode::Local => "jarvis",
        ActiveMode::Gemini => "gemini",
        ActiveMode::Openai => "openai",
    }
}

/// Repaint the Conversation-mode submenu to reflect the current
/// active mode: rewrites the disabled header line ("Active: …") and
/// adds/removes a "✓ " prefix on the three mode items.
fn refresh_mode_menu(app: &tauri::AppHandle) {
    let (header_text, jarvis_label, local_label, gemini_label, openai_label) = match detect_active_mode() {
        ActiveMode::Jarvis => (
            "Active: JARVIS (cloud)",
            "✓  JARVIS (audio + vision + tools)",
            "Local (audio + vision + tools)",
            "Gemini Live (audio + vision + tools)",
            "OpenAI Realtime (audio + vision + tools)",
        ),
        ActiveMode::Local => (
            "Active: Local (on-device)",
            "JARVIS (audio + vision + tools)",
            "✓  Local (audio + vision + tools)",
            "Gemini Live (audio + vision + tools)",
            "OpenAI Realtime (audio + vision + tools)",
        ),
        ActiveMode::Gemini => (
            "Active: Gemini Live",
            "JARVIS (audio + vision + tools)",
            "Local (audio + vision + tools)",
            "✓  Gemini Live (audio + vision + tools)",
            "OpenAI Realtime (audio + vision + tools)",
        ),
        ActiveMode::Openai => (
            "Active: OpenAI Realtime",
            "JARVIS (audio + vision + tools)",
            "Local (audio + vision + tools)",
            "Gemini Live (audio + vision + tools)",
            "✓  OpenAI Realtime (audio + vision + tools)",
        ),
    };
    if let Some(label_state) = app.try_state::<ModeLabel>() {
        if let Ok(guard) = label_state.0.lock() {
            if let Some(item) = guard.as_ref() {
                let _ = item.set_text(header_text);
            }
        }
    }
    if let Some(items_state) = app.try_state::<ModeItems>() {
        if let Ok(guard) = items_state.0.lock() {
            // Order: [jarvis, local, gemini, openai]
            if let Some(it) = guard.get(0) { let _ = it.set_text(jarvis_label); }
            if let Some(it) = guard.get(1) { let _ = it.set_text(local_label); }
            if let Some(it) = guard.get(2) { let _ = it.set_text(gemini_label); }
            if let Some(it) = guard.get(3) { let _ = it.set_text(openai_label); }
        }
    }
}

// ── Audio device picker (Microphone ▸ / Speaker ▸) ──────────────────────────
//
// The voice-client (:8767) enumerates mic/speaker devices via sounddevice
// (PortAudio) — wired + Bluetooth, on Windows and Linux — at GET /audio-devices.
// The tray lists them; picking one writes ~/.jarvis/audio-<kind>-device (the
// same file the client's _resolve_audio_device reads) and restarts the voice
// stack so it re-opens on the new device. We talk to :8767 with a tiny
// blocking GET rather than pulling in an async HTTP crate.

/// Minimal blocking HTTP/1.1 GET to a localhost port → response body, or None.
fn http_get_local(port: u16, path: &str) -> Option<String> {
    use std::io::{Read, Write};
    use std::time::Duration;
    let mut stream = std::net::TcpStream::connect(("127.0.0.1", port)).ok()?;
    let _ = stream.set_read_timeout(Some(Duration::from_secs(3)));
    let _ = stream.set_write_timeout(Some(Duration::from_secs(3)));
    let req = format!("GET {path} HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n");
    stream.write_all(req.as_bytes()).ok()?;
    let mut buf = Vec::new();
    stream.read_to_end(&mut buf).ok()?;
    let text = String::from_utf8_lossy(&buf).into_owned();
    // Body is a JSON object; find it directly so we don't have to parse the
    // chunked-vs-Content-Length framing.
    let start = text.find('{')?;
    Some(text[start..].to_string())
}

/// (input names, output names, current input, current output) from the
/// voice-client /audio-devices endpoint. Empty/blank on failure.
fn fetch_audio_devices() -> (Vec<String>, Vec<String>, String, String) {
    let empty = (Vec::new(), Vec::new(), String::new(), String::new());
    let Some(body) = http_get_local(8767, "/audio-devices") else { return empty; };
    let val: serde_json::Value = match serde_json::Deserializer::from_str(&body)
        .into_iter::<serde_json::Value>()
        .next()
    {
        Some(Ok(v)) => v,
        _ => return empty,
    };
    let arr = |k: &str| -> Vec<String> {
        val.get(k)
            .and_then(|v| v.as_array())
            .map(|a| a.iter().filter_map(|x| x.as_str().map(String::from)).collect())
            .unwrap_or_default()
    };
    let cur = |k: &str| -> String {
        val.get("current")
            .and_then(|c| c.get(k))
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string()
    };
    (arr("input"), arr("output"), cur("input"), cur("output"))
}

/// Build a "Microphone ▸" / "Speaker ▸" submenu: a "System default" entry plus
/// one item per device, ✓ on the current pick. IDs are "audioin::<name>" /
/// "audioout::<name>" ("__default__" = system default). Items are stashed in
/// AudioItems so the ✓ can be repainted after a pick.
fn build_audio_submenu(
    app: &tauri::AppHandle,
    kind: &str,
    title: &str,
    devices: &[String],
    current: &str,
) -> tauri::Result<Submenu<Wry>> {
    let prefix = if kind == "input" { "audioin::" } else { "audioout::" };
    let mark = |sel: bool, label: &str| if sel { format!("✓  {label}") } else { label.to_string() };

    let mut builder = SubmenuBuilder::new(app, title);
    let mut stored: Vec<(String, MenuItem<Wry>)> = Vec::new();

    let def_item = MenuItemBuilder::with_id(
        format!("{prefix}__default__"),
        mark(current.is_empty(), "System default"),
    ).build(app)?;
    builder = builder.item(&def_item);
    stored.push(("__default__".to_string(), def_item));

    if devices.is_empty() {
        let none = MenuItemBuilder::with_id(format!("{prefix}__none__"), "(no devices detected)")
            .enabled(false)
            .build(app)?;
        builder = builder.item(&none);
    } else {
        builder = builder.item(&PredefinedMenuItem::separator(app)?);
        for d in devices {
            let sel = !current.is_empty() && d == current;
            let item = MenuItemBuilder::with_id(format!("{prefix}{d}"), mark(sel, d)).build(app)?;
            builder = builder.item(&item);
            stored.push((d.clone(), item));
        }
    }

    if let Some(state) = app.try_state::<AudioItems>() {
        if let Ok(mut g) = state.0.lock() {
            if kind == "input" { g.input = stored; } else { g.output = stored; }
        }
    }
    builder.build()
}

/// Move the ✓ to `picked` in the input/output submenu (instant feedback before
/// the stack restart settles).
fn refresh_audio_menu(app: &tauri::AppHandle, kind: &str, picked: &str) {
    if let Some(state) = app.try_state::<AudioItems>() {
        if let Ok(g) = state.0.lock() {
            let items = if kind == "input" { &g.input } else { &g.output };
            for (name, item) in items.iter() {
                let label = if name == "__default__" { "System default" } else { name.as_str() };
                let text = if name == picked { format!("✓  {label}") } else { label.to_string() };
                let _ = item.set_text(text);
            }
        }
    }
}

/// Handle an "audioin::<name>" / "audioout::<name>" tray click: persist the
/// pick to ~/.jarvis/audio-<kind>-device (remove → system default), repaint the
/// ✓, and restart the voice stack so the client re-opens on the new device.
fn audio_device_pick(app: &tauri::AppHandle, id: &str) {
    let (kind, name) = if let Some(n) = id.strip_prefix("audioin::") {
        ("input", n)
    } else if let Some(n) = id.strip_prefix("audioout::") {
        ("output", n)
    } else {
        return;
    };
    if name == "__none__" { return; }
    let dir = jarvis_home().join(".jarvis");
    let _ = std::fs::create_dir_all(&dir);
    let file = dir.join(format!("audio-{kind}-device"));
    if name == "__default__" {
        let _ = std::fs::remove_file(&file);
    } else {
        let _ = std::fs::write(&file, format!("{name}\n"));
    }
    eprintln!("[tray] audio {kind} device -> {name}");
    refresh_audio_menu(app, kind, name);
    // Restart off the UI thread — restart_voice_agent_cmd() blocks ~5 s.
    std::thread::spawn(|| {
        let _ = restart_voice_agent_cmd();
    });
}

// ── Voice-mode (Local / Cloud) state ───────────────────────────────────────
//
// ~/.jarvis/voice-mode selects the JARVIS-Claude pipeline flavour, read by the
// agent at startup (_apply_voice_mode): local = faster-whisper + qwen3 + Kokoro
// (on-device); cloud = Deepgram + Claude + Orpheus. It's written by the
// Conversation-mode "JARVIS" / "Local" items and read here for the active-mode ✓.

fn read_voice_mode() -> &'static str {
    let p = jarvis_home().join(".jarvis").join("voice-mode");
    match std::fs::read_to_string(&p)
        .ok()
        .map(|s| s.trim().to_lowercase())
        .as_deref()
    {
        Some("local") => "local",
        _ => "cloud",
    }
}

/// Read a single-line ~/.jarvis/<name> config, or `default`.
fn read_jarvis_cfg(name: &str, default: &str) -> String {
    let p = jarvis_home().join(".jarvis").join(name);
    std::fs::read_to_string(&p)
        .ok()
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| default.to_string())
}

/// Build the items for a "pick one" list (✓ on `current`) to merge into a
/// submenu (e.g. the Kokoro voices into "TTS voice"). IDs are "<prefix><value>".
fn build_choice_items(
    app: &tauri::AppHandle,
    prefix: &str,
    options: &[(&str, &str)],
    current: &str,
) -> tauri::Result<Vec<(String, MenuItem<Wry>)>> {
    let mut stored: Vec<(String, MenuItem<Wry>)> = Vec::new();
    for (val, label) in options {
        let text = if *val == current { format!("✓  {label}") } else { label.to_string() };
        let item = MenuItemBuilder::with_id(format!("{prefix}{val}"), text).build(app)?;
        stored.push(((*val).to_string(), item));
    }
    Ok(stored)
}

fn refresh_choice_menu(items: &[(String, MenuItem<Wry>)], options: &[(&str, &str)], picked: &str) {
    for (val, item) in items {
        let label = options.iter().find(|(v, _)| v == val).map(|(_, l)| *l).unwrap_or(val.as_str());
        let text = if val == picked { format!("✓  {label}") } else { label.to_string() };
        let _ = item.set_text(text);
    }
}

/// Handle a "kvoice::<voice>" pick: write ~/.jarvis/voice-tts-voice + repaint the
/// ✓. NO restart — the Kokoro adapter reads the voice fresh per-utterance
/// (providers/kokoro_tts.py), so it hot-swaps on the next spoken line.
fn local_choice_pick(app: &tauri::AppHandle, id: &str) {
    let Some(value) = id.strip_prefix("kvoice::") else { return; };
    let dir = jarvis_home().join(".jarvis");
    let _ = std::fs::create_dir_all(&dir);
    let _ = std::fs::write(dir.join("voice-tts-voice"), value);
    eprintln!("[tray] voice-tts-voice -> {value}");
    if let Some(state) = app.try_state::<LocalVoiceItems>() {
        if let Ok(g) = state.0.lock() {
            refresh_choice_menu(&g.voice, KOKORO_VOICE_CHOICES, value);
        }
    }
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

// Shared struct shapes for screen-share sources. Used by both the
// internal tray-menu builder and the Tauri command exposed to JS.
#[derive(Clone, Debug)]
struct MonitorInfo {
    name:    String,
    x:       i64,
    y:       i64,
    w:       i64,
    h:       i64,
    primary: bool,
}

#[derive(Clone, Debug)]
struct WindowInfo {
    id:    String,
    w:     i64,
    h:     i64,
    title: String,
}

#[derive(Clone, Debug, Default)]
struct ScreenSources {
    monitors: Vec<MonitorInfo>,
    windows:  Vec<WindowInfo>,
}

/// Probe X11 for screen-share sources (monitors via xrandr, windows
/// via wmctrl). Returns empty lists on failure — callers decide what
/// to do (the tray menu shows "(no sources)" placeholder; the JS
/// command surfaces the error string).
fn enumerate_screen_sources_internal() -> Result<ScreenSources, String> {
    let xrandr = std::process::Command::new("xrandr")
        .arg("--listmonitors")
        .output()
        .map_err(|e| format!("xrandr not available: {e}"))?;
    if !xrandr.status.success() {
        return Err(format!(
            "xrandr --listmonitors exited {}: {}",
            xrandr.status,
            String::from_utf8_lossy(&xrandr.stderr),
        ));
    }
    let wmctrl = std::process::Command::new("wmctrl")
        .args(["-l", "-G"])
        .output()
        .map_err(|e| format!("wmctrl not available: {e}"))?;
    if !wmctrl.status.success() {
        return Err(format!(
            "wmctrl -l -G exited {}: {}",
            wmctrl.status,
            String::from_utf8_lossy(&wmctrl.stderr),
        ));
    }

    let mut monitors = Vec::<MonitorInfo>::new();
    for line in String::from_utf8_lossy(&xrandr.stdout).lines() {
        let line = line.trim();
        if line.starts_with("Monitors:") || line.is_empty() {
            continue;
        }
        let parts: Vec<&str> = line.split_whitespace().collect();
        if parts.len() < 3 {
            continue;
        }
        let flags_name = parts[1];
        let primary = flags_name.contains('*');
        let name = flags_name
            .trim_start_matches(|c| c == '+' || c == '*')
            .to_string();
        let geom = parts[2];
        let mut nums = geom.split(|c: char| !c.is_ascii_digit()).filter(|s| !s.is_empty());
        let wpx = nums.next().and_then(|s| s.parse::<i64>().ok()).unwrap_or(0);
        let _wmm = nums.next();
        let hpx = nums.next().and_then(|s| s.parse::<i64>().ok()).unwrap_or(0);
        let _hmm = nums.next();
        let x = nums.next().and_then(|s| s.parse::<i64>().ok()).unwrap_or(0);
        let y = nums.next().and_then(|s| s.parse::<i64>().ok()).unwrap_or(0);
        monitors.push(MonitorInfo { name, x, y, w: wpx, h: hpx, primary });
    }

    let denylist = ["xfce4-panel", "Desktop", "jarvis-desktop", "J.A.R.V.I.S."];
    let mut windows = Vec::<WindowInfo>::new();
    for line in String::from_utf8_lossy(&wmctrl.stdout).lines() {
        let cols: Vec<&str> = line.splitn(8, char::is_whitespace).collect();
        if cols.len() < 8 {
            continue;
        }
        let id = cols[0].to_string();
        let desktop = cols[1].parse::<i64>().unwrap_or(-1);
        if desktop < 0 {
            continue;
        }
        let w = cols[4].parse::<i64>().unwrap_or(0);
        let h = cols[5].parse::<i64>().unwrap_or(0);
        let title = line
            .splitn(8, char::is_whitespace)
            .nth(7)
            .unwrap_or("")
            .trim_start()
            .to_string();
        if denylist.iter().any(|d| title.contains(d)) {
            continue;
        }
        if w < 100 || h < 100 {
            continue;
        }
        windows.push(WindowInfo { id, w, h, title });
    }

    Ok(ScreenSources { monitors, windows })
}

/// List screen-share sources available on the user's X11 desktop —
/// monitors (via `xrandr --listmonitors`) and visible windows
/// (via `wmctrl -l -G`). Returns
/// `{ monitors: [{name, x, y, w, h, primary}, ...],
///    windows:  [{id, x, y, w, h, title}, ...] }`.
///
/// X11-only. Returns an error string if either xrandr or wmctrl is
/// missing (both are required for the picker — install them via
/// `apt install x11-xserver-utils wmctrl`).
#[tauri::command]
fn list_screen_sources() -> Result<serde_json::Value, String> {
    let sources = enumerate_screen_sources_internal()?;
    let mons: Vec<_> = sources.monitors.iter().map(|m| serde_json::json!({
        "name": m.name, "x": m.x, "y": m.y, "w": m.w, "h": m.h, "primary": m.primary,
    })).collect();
    let wins: Vec<_> = sources.windows.iter().map(|w| serde_json::json!({
        "id": w.id, "w": w.w, "h": w.h, "title": w.title,
    })).collect();
    Ok(serde_json::json!({"monitors": mons, "windows": wins}))
}


// ── Browser-open helpers ───────────────────────────────────────────────────

/// Probe the standard JARVIS web ports and return the first URL that
/// responds with a JARVIS-shaped response (200 + application/json on
/// /api/conversations). Returns `None` when nothing matches —
/// `JARVIS_WEB_URL` env override is honored before probing.
///
/// Replaces the previous "fall back to a dead URL anyway" behavior:
/// the caller now knows when no live web exists and can show a
/// useful UI instead of opening Chrome to a connection-refused page.
fn probe_jarvis_web() -> Option<String> {
    if let Ok(url) = std::env::var("JARVIS_WEB_URL") {
        if !url.trim().is_empty() {
            return Some(url);
        }
    }
    for port in [3001u16, 3002, 3000, 8765] {
        let Ok(addr) = format!("127.0.0.1:{port}").parse() else { continue };
        let Ok(mut stream) = std::net::TcpStream::connect_timeout(
            &addr,
            std::time::Duration::from_millis(150),
        ) else { continue };
        use std::io::{Read, Write};
        let _ = stream.set_read_timeout(Some(std::time::Duration::from_millis(300)));
        let req = format!(
            // /api/health is in the middleware's PUBLIC_PATHS, so this
            // works when JARVIS_REQUIRE_LOCAL_AUTH=1 (the probe holds no
            // bearer token). /api/conversations — the old target — 401s
            // under auth and the probe wrongly concluded "no web running".
            "GET /api/health HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n",
        );
        let _ = stream.write_all(req.as_bytes());
        let mut buf = [0u8; 256];
        if let Ok(n) = stream.read(&mut buf) {
            let head = String::from_utf8_lossy(&buf[..n]);
            if head.starts_with("HTTP/1.1 200") && head.contains("application/json") {
                return Some(format!("http://127.0.0.1:{port}/"));
            }
        }
    }
    None
}

/// Open `url` in the user's default browser via tauri-plugin-opener
/// (the v2 successor to tauri-plugin-shell::Shell::open). Cross-
/// platform — uses `xdg-open` on Linux, `open` on macOS, `start`
/// on Windows. Logs failures instead of swallowing them.
fn open_in_browser(app: &AppHandle, url: &str) {
    // The second arg picks a SPECIFIC application; None means "system
    // default". The turbofish fixes type inference (Option<&str>).
    if let Err(e) = app.opener().open_url(url, None::<&str>) {
        eprintln!("[JARVIS] open_in_browser failed for {url}: {e}");
    }
}

/// Walk up from the running binary's path to find the project root.
/// Binary lives at `<project>/src/desktop-tauri/src-tauri/target/release/jarvis-desktop`,
/// so 5 ancestors up = `<project>`. Honored override:
/// `JARVIS_PROJECT_ROOT=/path/to/jarvis` (useful for testing).
fn find_project_root() -> Option<std::path::PathBuf> {
    if let Ok(p) = std::env::var("JARVIS_PROJECT_ROOT") {
        let pb = std::path::PathBuf::from(p);
        if pb.exists() {
            return Some(pb);
        }
    }
    let exe = std::env::current_exe().ok()?;
    // jarvis-desktop ← release ← target ← src-tauri ← desktop-tauri ← src ← <project>
    let mut p = exe.as_path();
    for _ in 0..6 {
        p = p.parent()?;
    }
    Some(p.to_path_buf())
}

/// Locate the bun executable. Returns the first match from PATH, or
/// the standard user-install location at `$HOME/.bun/bin/bun`, or
/// None if neither resolves. The tray's PATH (set by systemd-user /
/// .desktop launchers) typically lacks `~/.bun/bin` even though the
/// user installed bun there via the official installer — so a bare
/// `Command::new("bun")` returns ENOENT and the auto-spawn flow
/// silently degrades to the diagnostic window. Resolving here lets
/// `try_spawn_web` find bun regardless of how the tray was started.
fn find_bun_executable() -> Option<std::path::PathBuf> {
    // Executable name + PATH separator differ by platform. On Windows the
    // binary is bun.exe and PATH is ';'-separated (splitting on ':' would
    // break at the drive letter, e.g. "C:\..."), and $HOME is unset (use
    // the cross-platform home helper for the ~/.bun/bin fallback).
    #[cfg(windows)]
    let (exe_name, sep) = ("bun.exe", ';');
    #[cfg(not(windows))]
    let (exe_name, sep) = ("bun", ':');
    let path_var = std::env::var("PATH").unwrap_or_default();
    for dir in path_var.split(sep).filter(|s| !s.is_empty()) {
        let candidate = std::path::PathBuf::from(dir).join(exe_name);
        if candidate.is_file() {
            return Some(candidate);
        }
    }
    let user_bun = jarvis_home().join(".bun").join("bin").join(exe_name);
    if user_bun.is_file() {
        return Some(user_bun);
    }
    None
}

/// Spawn `bun run dev` in src/web as a detached background process.
/// Fire-and-forget — no PID tracking. Matches the launch.sh pattern
/// where backend services (proxy, bridge, voice) survive tray exits.
/// Returns true if the spawn was attempted (bun + web dir present),
/// false otherwise so the caller can fall through to the diagnostic
/// window instead of waiting on a no-op.
fn try_spawn_web() -> bool {
    let Some(root) = find_project_root() else {
        eprintln!("[JARVIS] try_spawn_web: project root not found");
        return false;
    };
    let web_dir = root.join("src/web");
    if !web_dir.is_dir() {
        eprintln!("[JARVIS] try_spawn_web: missing {}", web_dir.display());
        return false;
    }
    // Detect bun by trying to spawn and watching for ENOENT. Spawn
    // happens with stdout/stderr redirected to /tmp/jarvis-web.log
    // (append) so the user can postmortem compile errors.
    // Cross-platform log path — /tmp doesn't exist on Windows, so opening
    // "/tmp/jarvis-web.log" failed there and "Open in Browser" silently
    // returned false. Use the same ~/.jarvis/logs dir the voice stack uses.
    let log_dir = jarvis_home().join(".jarvis").join("logs");
    let _ = std::fs::create_dir_all(&log_dir);
    let log_path = log_dir.join("web.log");
    let log = match std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&log_path)
    {
        Ok(f) => f,
        Err(e) => {
            eprintln!("[JARVIS] try_spawn_web: open log {}: {e}", log_path.display());
            return false;
        }
    };
    let log_clone = match log.try_clone() {
        Ok(f) => f,
        Err(_) => return false,
    };
    let Some(bun) = find_bun_executable() else {
        eprintln!(
            "[JARVIS] try_spawn_web: bun not found in PATH or ~/.bun/bin — install via `curl -fsSL https://bun.sh/install | bash`"
        );
        return false;
    };
    let mut cmd = hidden_command(&bun);
    cmd.arg("run").arg("dev")
        .current_dir(&web_dir)
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::from(log))
        .stderr(std::process::Stdio::from(log_clone));
    match cmd.spawn() {
        Ok(child) => {
            eprintln!(
                "[JARVIS] try_spawn_web: spawned {} run dev (pid={}) in {} — log {}",
                bun.display(),
                child.id(),
                web_dir.display(),
                log_path.display(),
            );
            // Don't wait — Next.js dev compiles for ~5-15 s on first
            // run. The poll loop in handle_open_browser picks up
            // readiness without blocking the spawn itself.
            std::mem::drop(child);
            true
        }
        Err(e) => {
            eprintln!(
                "[JARVIS] try_spawn_web: spawn failed for {}: {e}",
                bun.display()
            );
            false
        }
    }
}

/// Tray "Open in Browser" handler. Big-company pattern (JupyterLab /
/// VS Code Server / Docker Desktop): the click ALWAYS leads to the
/// web opening, even if it wasn't running. Flow:
///   1. Probe — if a JARVIS web is up, open it. (Fast path, no spawn.)
///   2. Otherwise: spawn `bun run dev` in src/web (idempotent —
///      duplicate spawn is harmless, port-collision fail is silent).
///   3. Poll the port for up to 30 s waiting for readiness.
///   4. On readiness: open the browser. On timeout: show the
///      diagnostic window so the user can debug manually.
///
/// `route_suffix` is appended to the resolved base URL so callers can
/// reuse the same probe-spawn-poll machinery to deep-link into a
/// specific page (e.g. "/logs" for the View Logs menu entry).
///
/// Runs on a worker thread so the menu event loop stays responsive
/// during the up-to-30-s wait.
fn handle_open_browser(app: &AppHandle, route_suffix: &'static str) {
    use std::sync::atomic::{AtomicBool, Ordering};
    // Guard: a double-click on "Open in Browser" used to spawn two
    // worker threads that both probed and both called open_in_browser,
    // producing two browser tabs. With this flag, the second click
    // silently no-ops while the first is still polling.
    static OPENING: AtomicBool = AtomicBool::new(false);
    if OPENING.swap(true, Ordering::AcqRel) {
        eprintln!("[JARVIS] handle_open_browser: already in flight, skipping");
        return;
    }
    let app = app.clone();
    std::thread::spawn(move || {
        struct Guard;
        impl Drop for Guard {
            fn drop(&mut self) { OPENING.store(false, Ordering::Release); }
        }
        let _guard = Guard;
        let resolved = |url: String| {
            // The probe returns "http://host:port/" — strip the trailing
            // slash before appending the route so we don't end up with
            // "//logs". When route_suffix is empty (the default Open
            // in Browser), the URL stays as-is.
            if route_suffix.is_empty() {
                url
            } else {
                format!("{}{}", url.trim_end_matches('/'), route_suffix)
            }
        };

        // 1. Fast path: probe finds a live web.
        if let Some(url) = probe_jarvis_web() {
            open_in_browser(&app, &resolved(url));
            return;
        }

        // 2. No live web — kick off a spawn. If we can't (no bun, no
        //    web dir), skip straight to the diagnostic window.
        let spawned = try_spawn_web();
        if !spawned {
            show_web_not_running_window(&app);
            return;
        }

        // 3. Poll for readiness. Next.js cold-compile is typically
        //    5-15 s; 30 s gives margin. Probe interval 500 ms is
        //    cheap (one TCP connect + 256-byte read per try).
        for _ in 0..60 {
            std::thread::sleep(std::time::Duration::from_millis(500));
            if let Some(url) = probe_jarvis_web() {
                open_in_browser(&app, &resolved(url));
                return;
            }
        }

        // 4. Timeout — surface the diagnostic so the user can check
        //    /tmp/jarvis-web.log for compile errors.
        eprintln!("[JARVIS] handle_open_browser: web didn't come up within 30 s");
        show_web_not_running_window(&app);
    });
}

/// Open a small Tauri webview window with a "JARVIS web isn't
/// running" diagnostic + the exact start command. Replaces the
/// old behavior where the tray would open Chrome to a dead URL —
/// now the user sees an explanation and the command to copy.
///
/// Self-contained: HTML is embedded inline as a `data:` URL so this
/// doesn't require new routes in the JS app dist or `npm run build`.
/// The window reuses the same WebviewWindowBuilder pattern as the
/// existing API-keys window (manage_keys handler).
fn show_web_not_running_window(app: &AppHandle) {
    if let Some(w) = app.get_webview_window("web-not-running") {
        let _ = w.show();
        let _ = w.set_focus();
        return;
    }
    // Inline HTML — kept tiny so URL-encoding is cheap. The copy
    // button uses navigator.clipboard which is available in all
    // modern webviews; falls back to selecting the <code> if not.
    let html = r##"<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<title>JARVIS web — not running</title>
<style>
  :root { color-scheme: dark; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
    background: #0a0a0a; color: #e0e0e0;
    margin: 0; padding: 28px 32px; line-height: 1.55;
  }
  h1 { font-size: 17px; font-weight: 600; margin: 0 0 6px; color: #f5f5f5; }
  .sub { color: #888; font-size: 13px; margin: 0 0 18px; }
  .cmd {
    background: #161616; border: 1px solid #262626; border-radius: 8px;
    padding: 12px 14px; font-family: "JetBrains Mono", ui-monospace, monospace;
    font-size: 12.5px; color: #d8d8d8; margin: 0 0 10px;
    word-break: break-all;
  }
  .row { display: flex; gap: 8px; align-items: center; margin-bottom: 16px; }
  button {
    background: #1d1d1d; border: 1px solid #303030; border-radius: 6px;
    color: #e0e0e0; padding: 6px 12px; font-size: 12px; cursor: pointer;
    font-family: inherit;
  }
  button:hover { background: #262626; border-color: #3a3a3a; }
  .ok { color: #34d399; }
  .hint { color: #666; font-size: 12px; }
  a { color: #22d3ee; }
</style></head>
<body>
<h1>JARVIS web isn't running</h1>
<p class="sub">The tray tried ports 3001, 3002, 3000, 8765 — none responded with a JARVIS web. Start it with:</p>
<div class="cmd" id="cmd">cd ~/Documents/Projects/jarvis/src/web &amp;&amp; bun run dev</div>
<div class="row">
  <button id="copy">Copy command</button>
  <span class="hint" id="status"></span>
</div>
<p class="hint">Once it boots, the tray's "Open in Browser" will pick it up automatically. The dev server lands on <a href="http://localhost:3001/">http://localhost:3001/</a>.</p>
<p class="hint">Tip: set <code>JARVIS_WEB_URL=https://your-host</code> to bypass detection and always open a specific URL.</p>
<script>
  document.getElementById("copy").addEventListener("click", async () => {
    const cmd = document.getElementById("cmd").textContent.trim();
    const status = document.getElementById("status");
    try {
      await navigator.clipboard.writeText(cmd);
      status.textContent = "Copied — paste it in your terminal.";
      status.classList.add("ok");
    } catch (e) {
      // Fallback: select the command for manual copy.
      const r = document.createRange();
      r.selectNodeContents(document.getElementById("cmd"));
      const s = window.getSelection();
      s.removeAllRanges(); s.addRange(r);
      status.textContent = "Selected — press Ctrl/Cmd+C to copy.";
    }
  });
</script>
</body></html>"##;

    // Percent-encode for the data: URL. Keep printable ASCII alpha-num
    // and a few safe punctuation chars unencoded; everything else
    // becomes %XX. UTF-8 multi-byte chars are encoded byte-by-byte,
    // which is what the URL spec requires anyway.
    let mut encoded = String::with_capacity(html.len() * 2);
    for &b in html.as_bytes() {
        let safe = b.is_ascii_alphanumeric()
            || matches!(b, b'-' | b'_' | b'.' | b'~');
        if safe {
            encoded.push(b as char);
        } else {
            encoded.push_str(&format!("%{:02X}", b));
        }
    }
    let data_url_str = format!("data:text/html;charset=utf-8,{}", encoded);

    let parsed = match data_url_str.parse() {
        Ok(u) => u,
        Err(e) => {
            eprintln!("[JARVIS] data URL parse failed: {e}");
            return;
        }
    };

    if let Err(e) = tauri::WebviewWindowBuilder::new(
        app,
        "web-not-running",
        tauri::WebviewUrl::External(parsed),
    )
    .title("JARVIS — Web not running")
    .inner_size(500.0, 360.0)
    .resizable(false)
    .visible(true)
    .build()
    {
        eprintln!("[JARVIS] failed to build web-not-running window: {e}");
    }
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
        // Single-instance: a second launch focuses the existing window
        // instead of starting a duplicate app (Windows users were getting
        // two tray icons + two voice controllers). Must be the FIRST plugin.
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            if let Some(w) = app.get_webview_window("main") {
                let _ = w.show();
                let _ = w.set_focus();
            }
        }))
        .plugin(tauri_plugin_opener::init())
        .plugin(
            tauri_plugin_global_shortcut::Builder::new()
                .with_handler(|app, shortcut, event| {
                    use tauri_plugin_global_shortcut::{Code, ShortcutState};
                    if event.state() != ShortcutState::Pressed { return }
                    println!("[JARVIS] global shortcut fired: {:?}", shortcut);
                    // Discriminate by key: Ctrl+Shift+Space → toggle chat;
                    // Ctrl+Shift+K → exit kiosk (idempotent if already off).
                    match shortcut.key {
                        Code::KeyK => {
                            if let Err(e) = crate::kiosk::exit_kiosk(app.clone()) {
                                eprintln!("[JARVIS] global Ctrl+Shift+K exit_kiosk failed: {}", e);
                            }
                        }
                        _ => {
                            if let Some(w) = app.get_webview_window("main") {
                                let _ = w.emit("tray-toggle-chat", ());
                            }
                        }
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
        .manage(ShareLabel(Mutex::new(None)))
        .manage(ModeLabel(Mutex::new(None)))
        .manage(ModeItems(Mutex::new(Vec::new())))
        .manage(AudioItems(Mutex::new(AudioItemsInner::default())))
        .manage(LocalVoiceItems(Mutex::new(LocalVoiceItemsInner::default())))
        .setup(move |app| {
            let window = app.get_webview_window("main").unwrap();
            let window_for_poll = window.clone();

            // Unified-app Phase 0/1: spawn + supervise the voice stack ourselves
            // when JARVIS_DESKTOP_OWNS_AGENT=1; otherwise the systemd service
            // stays in charge (default OFF). The returned Supervisor is held in
            // managed state so the RunEvent::ExitRequested handler can stop it.
            // Spec: docs/superpowers/specs/2026-06-24-unified-local-app-design.md
            {
                let root = repo_root();
                let manifest =
                    root.join("src/desktop-tauri/src-tauri/resources/run-manifest.json");
                let mut env_files = _repo_env_files();
                env_files.push(_keys_file()); // keys.env last = highest priority
                let sup = supervisor::maybe_start_managed_stack(&root, &manifest, &env_files);
                app.manage(Mutex::new(sup));
            }

            // Inject the local API bearer token into the webview as a
            // global. ChatPanel.jsx reads window.__JARVIS_LOCAL_API_TOKEN
            // and adds it to fetch() Authorization headers + WS query
            // string. Empty when token file isn't present — bridge
            // ignores empty tokens unless JARVIS_REQUIRE_LOCAL_AUTH=1.
            //
            // Encode via serde_json::to_string so any future token source
            // (unicode, quotes, backslashes, control chars, embedded
            // </script>) round-trips into a valid JS string literal.
            // Hand-rolled '\\' / '"' escaping was a future-XSS surface —
            // the previous fix's CSP=null context made even hex tokens
            // a one-edit-away exploit if the source ever drifted.
            let bridge_token = local_api_token();
            let token_js = serde_json::to_string(&bridge_token)
                .unwrap_or_else(|_| "\"\"".to_string());
            let _ = window.eval(&format!(
                "window.__JARVIS_LOCAL_API_TOKEN = {};",
                token_js
            ));

            // Full-screen transparent overlay. Pick the LAPTOP screen (smallest
            // monitor by area) rather than primary_monitor() — primary is the
            // external display in a docked setup, but the user expects JARVIS
            // on the laptop screen they're actually looking at.
            //
            // Override path: if JARVIS_DISPLAY is set and matches a monitor's
            // (width)x(height) at startup (e.g. "5120x1440"), use that instead.
            // Lets the user pin to a specific external if they prefer.
            let target_mon = (|| -> Option<(PhysicalSize<u32>, PhysicalPosition<i32>)> {
                let monitors = window.available_monitors().ok()?;
                if monitors.is_empty() {
                    return None;
                }
                if let Ok(spec) = std::env::var("JARVIS_DISPLAY") {
                    if let Some((w, h)) = spec.split_once('x') {
                        if let (Ok(w), Ok(h)) = (w.parse::<u32>(), h.parse::<u32>()) {
                            if let Some(m) = monitors.iter().find(|m| {
                                let s = m.size();
                                s.width == w && s.height == h
                            }) {
                                return Some((*m.size(), *m.position()));
                            }
                        }
                    }
                }
                // Smallest-by-area heuristic = laptop built-in display.
                let m = monitors.iter().min_by_key(|m| {
                    let s = m.size();
                    (s.width as u64) * (s.height as u64)
                })?;
                Some((*m.size(), *m.position()))
            })();

            if let Some((size, pos)) = target_mon {
                println!("[JARVIS] Target monitor (laptop heuristic): {}x{}+{}+{}", size.width, size.height, pos.x, pos.y);
                let _ = window.set_size(PhysicalSize::new(size.width, size.height));
                let _ = window.set_position(PhysicalPosition::new(pos.x, pos.y));
            } else if let Ok(Some(monitor)) = window.primary_monitor() {
                // Last-ditch fallback if available_monitors() failed.
                let size = monitor.size();
                let pos  = monitor.position();
                println!("[JARVIS] Target monitor (primary fallback): {}x{}+{}+{}", size.width, size.height, pos.x, pos.y);
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

            // ── Global hotkeys ──
            // Ctrl+Shift+Space summons/dismisses the chat panel (Ctrl+Space
            // alone conflicts with XFCE/IBus input-method switcher).
            // Ctrl+Shift+K exits kiosk mode (idempotent if not active).
            // Both are routed through the plugin's with_handler closure
            // above, which dispatches by Shortcut.key.
            {
                use tauri_plugin_global_shortcut::{Code, Modifiers, Shortcut, GlobalShortcutExt};
                let handle = app.handle();
                let mods = Modifiers::CONTROL | Modifiers::SHIFT;
                let sc_chat = Shortcut::new(Some(mods), Code::Space);
                match handle.global_shortcut().register(sc_chat) {
                    Ok(_)  => println!("[JARVIS] global shortcut registered: Ctrl+Shift+Space (chat)"),
                    Err(e) => eprintln!("[JARVIS] failed to register Ctrl+Shift+Space: {:?}", e),
                }
                let sc_kiosk_exit = Shortcut::new(Some(mods), Code::KeyK);
                match handle.global_shortcut().register(sc_kiosk_exit) {
                    Ok(_)  => println!("[JARVIS] global shortcut registered: Ctrl+Shift+K (exit kiosk)"),
                    Err(e) => eprintln!("[JARVIS] failed to register Ctrl+Shift+K: {:?}", e),
                }
            }

            // ── System tray ──
            // "Open Chat Panel" tray entry removed 2026-05-10 — the
            // floating chat overlay is no longer the primary surface
            // (web app + voice cover the same surface area). The
            // global shortcut Ctrl+Shift+Space still toggles it for
            // power users; the "open_chat" match arm below stays for
            // that path. To restore the menu entry, add a
            // MenuItemBuilder::with_id("open_chat", "Open Chat Panel")
            // and re-insert it in the MenuBuilder chain.
            // Re-introduced 2026-05-24 (see docs/superpowers/specs/
            // 2026-05-24-tray-chat-panel-design.md). Opens the new
            // VoiceChatPanel that talks directly to the voice agent
            // via :8767 — NOT the bridge-flavored ChatPanel.
            // Tray "Open Chat Panel" now spawns the standalone chat
            // WebviewWindow (decorated, non-transparent — sidesteps the
            // WebKitGTK transparent-overlay ghost-frame bug). The old
            // "open_voice_chat" handler still exists below if we ever
            // need a separate voice-only entry point.
            let voice_chat_item = MenuItemBuilder::with_id(
                "open_chat", "Open Chat Panel",
            ).build(app)?;
            let mute_item    = MenuItemBuilder::with_id("mute",         "Mute / Unmute Voice").build(app)?;

            // The Kokoro voices are built as ITEMS here and merged into the
            // existing "TTS voice ▸" submenu below (one unified voice list).
            // (No STT-model picker — faster-whisper "small" is the default.)
            // ✓ on the active Kokoro voice only in local mode (cloud → no ✓).
            let kvoice_current = if read_voice_mode() == "local" {
                read_jarvis_cfg("voice-tts-voice", "af_heart")
            } else {
                String::new()
            };
            let kvoice_items = build_choice_items(
                &app.handle(), "kvoice::", KOKORO_VOICE_CHOICES, &kvoice_current,
            )?;
            {
                // Clones share the underlying menu items, so ✓-repaint via the
                // state still updates what's shown in the TTS-voice submenu.
                let lvi: State<LocalVoiceItems> = app.state();
                *lvi.0.lock().unwrap() = LocalVoiceItemsInner { voice: kvoice_items.clone() };
            }

            // ── Conversation mode submenu (2026-05-28) ──
            // Three voice-conversation backends, all carrying the same
            // audio + screen vision + tool surface:
            //   - JARVIS-Claude (default STT/LLM/TTS, full tool registry)
            //   - Gemini Live (audio + vision + tools)
            //   - OpenAI Realtime (audio + vision + tools)
            //
            // Click shells out to `<project>/bin/jarvis-mode <name>`,
            // which handles the mute/scope dance. Header line + ✓
            // prefix on the active item are refreshed every 3 s by a
            // background task in app.setup().
            let mode_current_item = MenuItemBuilder::with_id(
                "mode_current", "Active: (checking…)",
            ).enabled(false).build(app)?;
            let mode_header_sep = PredefinedMenuItem::separator(app)?;
            let mode_jarvis_item = MenuItemBuilder::with_id(
                "mode_jarvis", "JARVIS (audio + vision + tools)",
            ).build(app)?;
            // Local = the same JARVIS-Claude pipeline (tools + vision), but with
            // on-device STT (faster-whisper) + LLM (qwen3) + TTS (Kokoro).
            let mode_local_item = MenuItemBuilder::with_id(
                "mode_local", "Local (audio + vision + tools)",
            ).build(app)?;
            let mode_gemini_item = MenuItemBuilder::with_id(
                "mode_gemini", "Gemini Live (audio + vision + tools)",
            ).build(app)?;
            let mode_openai_item = MenuItemBuilder::with_id(
                "mode_openai", "OpenAI Realtime (audio + vision + tools)",
            ).build(app)?;
            let mode_status_item = MenuItemBuilder::with_id(
                "mode_status", "Notify current mode",
            ).build(app)?;
            let mode_sep = PredefinedMenuItem::separator(app)?;
            let mode_submenu = SubmenuBuilder::new(app, "Conversation mode ▸")
                .item(&mode_current_item)
                .item(&mode_header_sep)
                .item(&mode_jarvis_item)
                .item(&mode_local_item)
                .item(&mode_gemini_item)
                .item(&mode_openai_item)
                .item(&mode_sep)
                .item(&mode_status_item)
                .build()?;
            {
                let ml: State<ModeLabel> = app.state();
                *ml.0.lock().unwrap() = Some(mode_current_item.clone());
            }
            {
                let mi: State<ModeItems> = app.state();
                *mi.0.lock().unwrap() = vec![
                    mode_jarvis_item.clone(),
                    mode_local_item.clone(),
                    mode_gemini_item.clone(),
                    mode_openai_item.clone(),
                ];
            }

            // ── Screen-share tray UI removed (2026-06-09) ──
            //
            // The "Share Screen ▸" submenu published a LiveKit track via
            // the voice-client's /screen-share endpoint for the
            // supervisor's set_screen_share tool — which left the tool
            // surface in the 2026-05-20 rebuild (screen vision is
            // computer_use's job now), so the button fed a track with no
            // subscriber. The Gemini/OpenAI realtime modes were never on
            // this path: they capture the screen in-process (mss / frame
            // injection in bin/jarvis-{gemini,gpt}-tools).
            //
            // The backend plumbing stays: POST /screen-share + the
            // ScreenShare ffmpeg publisher in the voice-client, and the
            // list_screen_sources command (legacy React picker). If
            // set_screen_share re-ports into the registry, rebuild the
            // submenu from git history (removed at this commit).

            let sep1         = PredefinedMenuItem::separator(app)?;
            let browser_item = MenuItemBuilder::with_id("open_browser", "Open in Browser").build(app)?;
            let logs_item    = MenuItemBuilder::with_id("open_logs",    "View Logs").build(app)?;
            let keys_item    = MenuItemBuilder::with_id("manage_keys",  "Manage API Keys…").build(app)?;
            let sep_prov     = PredefinedMenuItem::separator(app)?;

            // ── Models submenu ──
            // Two layers of models, surfaced clearly in the menu:
            //
            //   1) SPEECH model (the voice LLM that composes spoken
            //      replies). Switchable below — 6 curated entries
            //      (Anthropic×3, OpenAI×2, Groq×1) matching
            //      SPEECH_MODELS_AVAILABLE in voice_client_tray_config.py;
            //      a pick writes ~/.jarvis/voice-model and restarts the
            //      agent (~5 s amber).
            //
            //   2) TOOL model (run_jarvis_cli's underlying LLM). Live-
            //      switchable, no restart — 6 entries matching
            //      CLI_MODELS_AVAILABLE. IDs must exist in
            //      jarvis_agent.py's CLI_MODELS dict (verified 2026-06-09).
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

            // Detect what's actually usable so the menu only offers working
            // models (user request 2026-06-23): API models are gated on key
            // presence, local models on what Ollama has pulled. Detection runs
            // once at launch — relaunch the desktop to pick up a new key or a
            // freshly pulled Ollama model.
            let have_anthropic = provider_key_present("ANTHROPIC_API_KEY");
            let have_openai    = provider_key_present("OPENAI_API_KEY");
            let have_groq      = provider_key_present("GROQ_API_KEY");
            let have_deepseek  = provider_key_present("DEEPSEEK_API_KEY");
            let ollama_models  = ollama_installed_models();

            // ── SPEECH submenu (nested under Models) ──
            // Switching speech requires an agent restart (~5 s amber).
            // 2026-05-18: curated to 6 entries matching
            // voice_client_tray_config.py SPEECH_MODELS_AVAILABLE.
            // Haiku 4.5 is the default (best TTFT/tool-quality balance,
            // ~0.7s vs gpt-5-mini ~1.34s). Sonnet for tool-heavy work,
            // Opus for extended reasoning. gpt-5-mini / 5.1 are the
            // OpenAI alternatives when Anthropic credit is a concern.
            // qwen3-32b is the strongest Groq option (no API quota).
            let v_claude_haiku  = MenuItemBuilder::with_id("speech_claude-haiku-4-5",  "Use Anthropic · Claude Haiku 4.5  (default, ~0.7s)").build(app)?;
            let v_claude_sonnet = MenuItemBuilder::with_id("speech_claude-sonnet-4-6", "Use Anthropic · Claude Sonnet 4.6  (best tool calling)").build(app)?;
            let v_claude_opus   = MenuItemBuilder::with_id("speech_claude-opus-4-7",   "Use Anthropic · Claude Opus 4.7  (most capable, slowest)").build(app)?;
            let v_gpt_5_mini    = MenuItemBuilder::with_id("speech_gpt-5-mini",        "Use OpenAI · GPT-5 mini (alternative)").build(app)?;
            let v_gpt_5_1       = MenuItemBuilder::with_id("speech_gpt-5.1",           "Use OpenAI · GPT-5.1 (best OpenAI tools)").build(app)?;
            let v_qwen          = MenuItemBuilder::with_id("speech_qwen/qwen3-32b",    "Use Groq · qwen3-32b (no-API-quota option)").build(app)?;
            // DeepSeek re-added 2026-06-23 per user request (daily-driver model).
            // Speech uses v4-flash — the fast one, and the only v4 DeepSeek in
            // providers/llm.py SPEECH_MODELS (v4-pro isn't a speech entry).
            let v_deepseek      = MenuItemBuilder::with_id("speech_deepseek-v4-flash", "Use DeepSeek · V4 Flash (fast)").build(app)?;
            // Local (Ollama) — runs the voice brain fully on-device. qwen3-30b-a3b
            // is the CPU sweet spot (MoE, ~3B active, tool-calling verified);
            // gpt-oss-120b is heavier + slower on CPU. Both are in SPEECH_MODELS.
            let v_local_qwen3   = MenuItemBuilder::with_id("speech_ollama/qwen3:30b-a3b", "Use Local · Qwen3 30B-A3B (Ollama, on-device, fast)").build(app)?;
            let v_local_gptoss  = MenuItemBuilder::with_id("speech_ollama/gpt-oss:120b",  "Use Local · gpt-oss 120B (Ollama, heavy, slow on CPU)").build(app)?;
            let speech_sep_local = PredefinedMenuItem::separator(app)?;
            let mut speech_sb = SubmenuBuilder::new(app, "Speech model ▸");
            if have_anthropic { speech_sb = speech_sb.item(&v_claude_haiku).item(&v_claude_sonnet).item(&v_claude_opus); }
            if have_openai    { speech_sb = speech_sb.item(&v_gpt_5_mini).item(&v_gpt_5_1); }
            if have_groq      { speech_sb = speech_sb.item(&v_qwen); }
            if have_deepseek  { speech_sb = speech_sb.item(&v_deepseek); }
            // Local (Ollama) — only the supported models that are actually pulled.
            let local_qwen3_ok  = ollama_has(&ollama_models, "qwen3:30b-a3b");
            let local_gptoss_ok = ollama_has(&ollama_models, "gpt-oss:120b");
            if local_qwen3_ok || local_gptoss_ok { speech_sb = speech_sb.item(&speech_sep_local); }
            if local_qwen3_ok  { speech_sb = speech_sb.item(&v_local_qwen3); }
            if local_gptoss_ok { speech_sb = speech_sb.item(&v_local_gptoss); }
            let speech_submenu = speech_sb.build()?;

            // ── TTS VOICE submenu (nested under Models) ──
            // Switches the synthesis voice without restarting the agent.
            // Voice-client writes ~/.jarvis/tts-provider; agent's
            // _build_tts_chain reads it on next utterance. Groq Orpheus
            // only as of 2026-05-01 (ElevenLabs removed after live key
            // 401 + fallback chain failure left JARVIS silent mid-turn).

            // Read the current selection from disk so we can pre-mark
            // it with ✓ immediately — no wait for a /status poll.
            let saved_tts = std::fs::read_to_string(
                    jarvis_home().join(".jarvis/tts-provider"))
                .ok()
                .map(|s| s.trim().to_string())
                .unwrap_or_default();

            // ✓ on a cloud Orpheus voice only in cloud mode — in local mode the
            // active TTS is Kokoro, so the ✓ belongs on a Kokoro voice below.
            let tts_cloud_mode = read_voice_mode() != "local";
            let tts_item_label = |spec: &str, label: &str| -> String {
                if tts_cloud_mode && spec == saved_tts.as_str() { format!("✓  {label}") } else { label.to_string() }
            };
            let init_tts_header = tts_provider_pretty(&saved_tts)
                .map(|p| format!("TTS: {p}"))
                .unwrap_or_else(|| "TTS: (loading…)".to_string());

            let tts_current = MenuItemBuilder::with_id("tts_current", &init_tts_header)
                .enabled(false)
                .build(app)?;
            let tts_gr_troy   = MenuItemBuilder::with_id("tts_gr_troy",   &tts_item_label("groq:troy",   "Groq Orpheus · Troy  (cloud)")).build(app)?;
            let tts_gr_austin = MenuItemBuilder::with_id("tts_gr_austin", &tts_item_label("groq:austin", "Groq Orpheus · Austin  (cloud)")).build(app)?;
            // Unified voice list: cloud Orpheus voices + the on-device Kokoro
            // voices merged in below (the Kokoro ones take effect only when
            // Voice brain = Local; they hot-swap with no restart).
            let tts_kokoro_sep = PredefinedMenuItem::separator(app)?;
            let tts_local_hdr = MenuItemBuilder::with_id("tts_local_hdr", "On-device (Kokoro):")
                .enabled(false)
                .build(app)?;
            let mut tts_builder = SubmenuBuilder::new(app, "TTS voice ▸")
                .item(&tts_gr_troy)
                .item(&tts_gr_austin)
                .item(&tts_kokoro_sep)
                .item(&tts_local_hdr);
            for (_, item) in &kvoice_items {
                tts_builder = tts_builder.item(item);
            }
            let tts_submenu = tts_builder.build()?;

            // ── TOOL submenu (nested under Models) ──
            // No restart needed — every run_jarvis_cli call re-reads
            // ~/.jarvis/cli-model and exports JARVIS_PROVIDER+MODEL.
            // 2026-05-18: curated to 6 entries matching
            // voice_client_tray_config.py CLI_MODELS_AVAILABLE.
            // Sonnet 4.6 is the default (τ-bench leader 87.5% for
            // multi-turn tool use). Opus for hardest multi-step work.
            // Haiku for fast single-shot calls. gpt-5.1 / 5-mini are
            // OpenAI alternatives. qwen3-32b is the no-API-quota
            // option. DeepSeek V4 Pro re-added 2026-06-23 per user
            // request — it's their daily-driver CLI model — despite the
            // documented hallucination rate (94% per Artificial Analysis).
            let m_claude_sonnet = MenuItemBuilder::with_id("model_claude-sonnet-4-6", "Use Claude · Sonnet 4.6 (default, best tool calling)").build(app)?;
            let m_claude_opus   = MenuItemBuilder::with_id("model_claude-opus-4-7",   "Use Claude · Opus 4.7  (1M ctx · most capable)").build(app)?;
            let m_claude_haiku  = MenuItemBuilder::with_id("model_claude-haiku-4-5",  "Use Claude · Haiku 4.5  (fastest)").build(app)?;
            let m_gpt_5_1       = MenuItemBuilder::with_id("model_gpt-5.1",           "Use OpenAI · GPT-5.1 (best OpenAI tools)").build(app)?;
            let m_gpt_5_mini    = MenuItemBuilder::with_id("model_gpt-5-mini",        "Use OpenAI · GPT-5 mini (alternative)").build(app)?;
            let m_qwen          = MenuItemBuilder::with_id("model_qwen/qwen3-32b",    "Use Groq · qwen3-32b (no-API-quota option)").build(app)?;
            let m_deepseek      = MenuItemBuilder::with_id("model_deepseek-v4-pro",   "Use DeepSeek · V4 Pro (strong reasoning)").build(app)?;
            let mut tool_sb = SubmenuBuilder::new(app, "Tool model ▸");
            if have_anthropic { tool_sb = tool_sb.item(&m_claude_sonnet).item(&m_claude_opus).item(&m_claude_haiku); }
            if have_openai    { tool_sb = tool_sb.item(&m_gpt_5_1).item(&m_gpt_5_mini); }
            if have_groq      { tool_sb = tool_sb.item(&m_qwen); }
            if have_deepseek  { tool_sb = tool_sb.item(&m_deepseek); }
            let tool_submenu = tool_sb.build()?;

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
                *vi.0.lock().unwrap() = vec![tts_gr_troy, tts_gr_austin];
            }

            let sep2         = PredefinedMenuItem::separator(app)?;
            let quit_item    = MenuItemBuilder::with_id("quit",         "Quit JARVIS").build(app)?;

            // Kiosk submenu: per-monitor entries + exit. Registered + dispatched
            // via the tray_kiosk module (which also retains the per-monitor
            // CheckMenuItems in AppState so set_checked() works later).
            let focus_mode_submenu = crate::tray_kiosk::build_kiosk_submenu(&app.handle())?;

            // Audio device picker — enumerated from the voice-client (:8767).
            // Built once at startup; the device list refreshes on the next
            // launch (a hot-plugged Bluetooth device needs a relaunch to appear).
            let (mic_devs, spk_devs, cur_in, cur_out) = fetch_audio_devices();
            let mic_submenu = build_audio_submenu(&app.handle(), "input",  "Microphone ▸", &mic_devs, &cur_in)?;
            let spk_submenu = build_audio_submenu(&app.handle(), "output", "Speaker ▸",    &spk_devs, &cur_out)?;

            // The Voice-brain toggle + Local STT-model/voice pickers now live
            // INSIDE the Conversation mode submenu (built above), so they're not
            // added to the top level here.
            let menu = MenuBuilder::new(app)
                .item(&voice_chat_item)
                .item(&mute_item)
                .item(&mode_submenu)
                .item(&mic_submenu)
                .item(&spk_submenu)
                .item(&focus_mode_submenu)
                .item(&sep1)
                .item(&browser_item)
                .item(&logs_item)
                .item(&keys_item)
                .item(&sep_prov)
                .item(&provider_submenu)
                .item(&sep2)
                .item(&quit_item)
                .build()?;

            // ShareLabel state stays registered for backward
            // compatibility with any existing set_share_label callers;
            // those calls are no-ops (label is None) now that the
            // screen-share tray UI is gone.

            let chat_open_tray = Arc::clone(&chat_open);

            // Start the tray on the green "idle" indicator — React will
            // push state updates via set_tray_state as soon as the webview
            // boots and the WS reports status.
            let (iw, ih, idle_rgba) = tray_image_for("idle", false);
            let idle_icon = Image::new_owned(idle_rgba, iw, ih);
            let tray = TrayIconBuilder::new()
                .icon(idle_icon)
                .menu(&menu)
                .tooltip("J.A.R.V.I.S.")
                .on_menu_event(move |app, event| {
                    match event.id().as_ref() {
                        "open_chat" => {
                            // Standalone chat window: just open (or focus) it
                            // via the IPC command. No main-overlay manipulation,
                            // no click-through flips — the chat now lives in
                            // its own decorated window.
                            let mut open = chat_open_tray.lock().unwrap();
                            *open = !*open;
                            let is_open_after = *open;
                            drop(open);
                            if is_open_after {
                                if let Err(e) = kiosk::open_chat_window(app.clone()) {
                                    eprintln!("[JARVIS] open_chat_window failed: {}", e);
                                }
                                println!("[JARVIS] Chat opened via tray");
                            } else {
                                if let Err(e) = kiosk::close_chat_window(app.clone()) {
                                    eprintln!("[JARVIS] close_chat_window failed: {}", e);
                                }
                                println!("[JARVIS] Chat closed via tray");
                            }
                        }
                        "open_voice_chat" => {
                            if let Some(w) = app.get_webview_window("main") {
                                let _ = w.emit("tray-toggle-voice-chat", ());
                                println!("[JARVIS] voice-chat toggle requested via tray");
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
                            // Bridge bearer token (required when
                            // JARVIS_REQUIRE_LOCAL_AUTH=1). `local_api_token`
                            // reads env first, falls back to the canonical
                            // ~/.jarvis/local-api-token.env file so this works
                            // even when the binary is launched outside
                            // start-desktop.sh (autostart, double-click).
                            let bridge_token = local_api_token();
                            let mut mute_args: Vec<String> = vec!["-s".into(), "-X".into(), "POST".into()];
                            if !bridge_token.is_empty() {
                                mute_args.push("-H".into());
                                mute_args.push(format!("Authorization: Bearer {}", bridge_token));
                            }
                            mute_args.push("http://127.0.0.1:8765/api/mute".into());
                            let _ = hidden_command("curl").args(&mute_args).spawn();
                            // Toggle the voice-client by POSTing with no
                            // body — the Python handler defaults to "flip
                            // current state" when `mute` is absent.
                            let _ = hidden_command("curl")
                                .args(["-s", "-X", "POST",
                                       "http://127.0.0.1:8767/mute",
                                       "-H", "Content-Type: application/json",
                                       "-d", "{}"])
                                .spawn();
                            // Also interrupt JARVIS's current utterance.
                            // Without this, /mute only silences the user's
                            // mic — if JARVIS is mid-TTS, the user keeps
                            // hearing him until the sentence finishes,
                            // which reads as "mute did nothing." /stop
                            // publishes a data packet the agent handles
                            // via session.interrupt(). On unmute clicks
                            // (or when the agent is idle) /stop is a
                            // no-op — the agent's _on_data handler
                            // swallows the RuntimeError from interrupting
                            // an idle session.
                            let _ = hidden_command("curl")
                                .args(["-s", "-X", "POST",
                                       "http://127.0.0.1:8767/stop"])
                                .spawn();
                            if let Some(w) = app.get_webview_window("main") {
                                let _ = w.emit("tray-toggle-mute", ());
                            }
                        }
                        // ── Conversation mode switches (2026-05-28) ──
                        // Shell out to bin/jarvis-mode <arg>; the script
                        // handles the systemd-scope + JARVIS-mic-mute
                        // dance idempotently.
                        id @ ("mode_jarvis" | "mode_local" | "mode_gemini" | "mode_openai" | "mode_status") => {
                            // mode_jarvis + mode_local are BOTH the JARVIS-Claude
                            // pipeline; they differ only in ~/.jarvis/voice-mode
                            // (cloud vs on-device), read by the agent at startup.
                            // Write it first; both route through "jarvis" (which
                            // also stops any Gemini/OpenAI direct mode).
                            let is_jarvis_pipeline = id == "mode_jarvis" || id == "mode_local";
                            if is_jarvis_pipeline {
                                let dir = jarvis_home().join(".jarvis");
                                let _ = std::fs::create_dir_all(&dir);
                                let vmode = if id == "mode_local" { "local" } else { "cloud" };
                                let _ = std::fs::write(dir.join("voice-mode"), vmode);
                            }
                            let arg = match id {
                                "mode_jarvis" | "mode_local" => "jarvis",
                                "mode_gemini" => "gemini",
                                "mode_openai" => "openai",
                                _             => "status",
                            };
                            let Some(root) = find_project_root() else {
                                eprintln!("[JARVIS] mode switch: project root not found");
                                return;
                            };
                            // Linux: bin/jarvis-mode (bash + systemd transient
                            // units). Windows: bin/jarvis-mode.ps1 (sounddevice
                            // audio + a restart-loop supervisor), launched
                            // detached + hidden via powershell so the supervisor
                            // loop never blocks the tray thread.
                            #[cfg(windows)]
                            {
                                let script = root.join("bin").join("jarvis-mode.ps1");
                                if !script.is_file() {
                                    eprintln!("[JARVIS] mode switch: {} missing", script.display());
                                    return;
                                }
                                let mut cmd = hidden_command("powershell.exe");
                                cmd.args(["-NoProfile", "-ExecutionPolicy", "Bypass", "-File"])
                                    .arg(&script)
                                    .arg(arg);
                                let _ = cmd.spawn();
                                println!("[JARVIS] tray → jarvis-mode.ps1 {arg}");
                            }
                            #[cfg(not(windows))]
                            {
                                let script = root.join("bin").join("jarvis-mode");
                                if !script.is_file() {
                                    eprintln!("[JARVIS] mode switch: {} missing", script.display());
                                    return;
                                }
                                let _ = std::process::Command::new(&script)
                                    .arg(arg)
                                    .spawn();
                                println!("[JARVIS] tray → jarvis-mode {arg}");
                            }
                            // JARVIS-Claude pipeline (jarvis/local): restart the
                            // voice stack so the new voice-mode (cloud/on-device)
                            // takes effect — jarvis-mode only stops direct modes,
                            // it doesn't re-read voice-mode.
                            if is_jarvis_pipeline {
                                std::thread::spawn(|| {
                                    std::thread::sleep(std::time::Duration::from_millis(900));
                                    let _ = restart_voice_agent_cmd();
                                });
                            }
                            // Repaint the submenu shortly after the command
                            // settles (the direct-mode launch takes ~1 s to
                            // register before the new state shows).
                            let app_handle = app.clone();
                            std::thread::spawn(move || {
                                std::thread::sleep(std::time::Duration::from_millis(1200));
                                refresh_mode_menu(&app_handle);
                            });
                        }
                        // Handlers removed 2026-04-30: camera_rgb, camera_ir,
                        // stop_computer_use. The `share_screen` handler
                        // above replaces the previous file-trigger
                        // screen-share path.

                        "open_browser" => {
                            // Big-company pattern (JupyterLab / VS Code Server /
                            // Docker Desktop): click ALWAYS leads to the web
                            // opening, even if it wasn't running. Probe → if up,
                            // open immediately. Else spawn `bun run dev` in
                            // src/web, poll for readiness, open when ready.
                            // Diagnostic window only appears on hard failure
                            // (no bun, no web dir, or 30 s timeout).
                            //
                            // Runs on a worker thread so the menu doesn't
                            // freeze during the cold-compile wait.
                            handle_open_browser(app, "");
                        }
                        "open_logs" => {
                            // Same flow as open_browser, but lands on the
                            // /logs route — a live tail of every JARVIS
                            // service's logs in one page (built on top of
                            // /api/logs/stream SSE).
                            handle_open_browser(app, "/logs");
                        }
                        "manage_keys" => {
                            // Open the API-keys settings window. The
                            // window is a separate WebviewWindow with
                            // its own HTML page (loaded from index.html
                            // ?route=keys); App.jsx routes on the
                            // hash to render the keys form.
                            //
                            // First click → create the window. Subsequent
                            // clicks → focus existing.
                            if let Some(w) = app.get_webview_window("keys") {
                                let _ = w.show();
                                let _ = w.set_focus();
                            } else {
                                let _ = tauri::WebviewWindowBuilder::new(
                                    app,
                                    "keys",
                                    tauri::WebviewUrl::App("index.html?route=keys".into()),
                                )
                                .title("JARVIS — API Keys")
                                .inner_size(560.0, 540.0)
                                .resizable(true)
                                .visible(true)
                                .build();
                            }
                        }
                        // Tool/CLI-model picks (no restart needed)
                        // 2026-05-18: curated to 6 entries matching
                        // CLI_MODELS_AVAILABLE in voice_client_tray_config.py
                        "model_claude-sonnet-4-6"                          => switch_cli_model(app, "claude-sonnet-4-6"),
                        "model_claude-opus-4-7"                            => switch_cli_model(app, "claude-opus-4-7"),
                        "model_claude-haiku-4-5"                           => switch_cli_model(app, "claude-haiku-4-5"),
                        "model_gpt-5.1"                                    => switch_cli_model(app, "gpt-5.1"),
                        "model_gpt-5-mini"                                 => switch_cli_model(app, "gpt-5-mini"),
                        "model_qwen/qwen3-32b"                             => switch_cli_model(app, "qwen/qwen3-32b"),
                        "model_deepseek-v4-pro"                            => switch_cli_model(app, "deepseek-v4-pro"),
                        // Speech-model picks (these trigger an agent restart)
                        // 2026-05-18: curated to 6 entries matching
                        // SPEECH_MODELS_AVAILABLE in voice_client_tray_config.py
                        "speech_claude-haiku-4-5"                          => switch_speech_model(app, "claude-haiku-4-5"),
                        "speech_claude-sonnet-4-6"                         => switch_speech_model(app, "claude-sonnet-4-6"),
                        "speech_claude-opus-4-7"                           => switch_speech_model(app, "claude-opus-4-7"),
                        "speech_gpt-5-mini"                                => switch_speech_model(app, "gpt-5-mini"),
                        "speech_gpt-5.1"                                   => switch_speech_model(app, "gpt-5.1"),
                        "speech_qwen/qwen3-32b"                            => switch_speech_model(app, "qwen/qwen3-32b"),
                        "speech_deepseek-v4-flash"                         => switch_speech_model(app, "deepseek-v4-flash"),
                        "speech_ollama/qwen3:30b-a3b"                      => switch_speech_model(app, "ollama/qwen3:30b-a3b"),
                        "speech_ollama/gpt-oss:120b"                       => switch_speech_model(app, "ollama/gpt-oss:120b"),
                        // TTS-voice picks (no agent restart — file written, read on next utterance)
                        "tts_gr_troy"   => switch_tts_provider(app, "groq:troy"),
                        "tts_gr_austin" => switch_tts_provider(app, "groq:austin"),
                        id if id.starts_with("kiosk_") => {
                            crate::tray_kiosk::handle_kiosk_menu_event(app, id);
                        }
                        "quit" => {
                            // "Quit JARVIS" stops EVERYTHING the user
                            // perceives as JARVIS — not just the overlay.
                            // Live failure 2026-05-11: user clicked Quit
                            // and reported "only the icon stopped, the
                            // services kept running". Two bugs were in
                            // the prior implementation:
                            //
                            //  1. A 60s session-active guard that skipped
                            //     the systemctl stop entirely if a turn
                            //     had happened recently — conservative-
                            //     for-restart logic that wrongly inherited
                            //     into Quit. Quit is explicit user intent;
                            //     they DO want to interrupt the in-flight.
                            //     Removed.
                            //
                            //  2. Only stopped voice-agent + voice-client,
                            //     leaving jarvis-bridge (:8765) and
                            //     jarvis-proxy (:4000) running. Now stops
                            //     all four units.
                            //
                            // Spawn detached so we don't block the tray
                            // event handler. Failure is non-fatal — if
                            // a unit isn't installed via systemd (some
                            // services have a fallback nohup launch path),
                            // systemctl returns non-zero and we still exit.
                            // Only tear down services when this is the last
                            // desktop instance. If the user is restarting
                            // the app (close + reopen), keep voice alive
                            // so the new instance is immediately ready.
                            // Count our own process in the pgrep tally.
                            let count = std::process::Command::new("pgrep")
                                .args(["-cf", "jarvis-desktop"])
                                .output()
                                .map(|o| String::from_utf8_lossy(&o.stdout).trim().parse::<u32>().unwrap_or(1))
                                .unwrap_or(1);
                            if count <= 1 {
                                let _ = std::process::Command::new("systemctl")
                                    .args([
                                        "--user", "stop",
                                        "jarvis-voice-agent",
                                        "jarvis-voice-client",
                                        "jarvis-bridge",
                                        "jarvis-proxy",
                                        // Realtime-mode transient units (systemd-run
                                        // scopes from bin/jarvis-mode). Without these,
                                        // Quit during Gemini/OpenAI mode left the
                                        // session running and the MIC STILL CAPTURING
                                        // — the same "Quit didn't quit" failure class
                                        // as 2026-05-11. Stopping a unit that doesn't
                                        // exist is non-fatal, like bridge/proxy above.
                                        "jarvis-gemini-tools",
                                        "jarvis-gpt-tools",
                                    ])
                                    .spawn();
                            }
                            // Give systemctl ~500 ms to issue SIGTERMs
                            // so the voice-agent has a chance to clean up
                            // SFU room state. Without this, the room can
                            // be left holding stale agent participants
                            // that block re-dispatch on next launch.
                            std::thread::sleep(std::time::Duration::from_millis(500));
                            app.exit(0);
                        }
                        // Audio device picker — dynamic IDs ("audioin::<name>" /
                        // "audioout::<name>") carry the device name, so they
                        // can't be string-literal arms.
                        id if id.starts_with("audioin::") || id.starts_with("audioout::") => {
                            audio_device_pick(app, id);
                        }
                        // Local Kokoro-voice picker (dynamic IDs).
                        id if id.starts_with("kvoice::") => {
                            local_choice_pick(app, id);
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

            // Sync the per-monitor CheckMenuItem checked state from kiosk-changed
            // events (Rust is source of truth for kiosk on/off).
            crate::tray_kiosk::install_kiosk_changed_listener(&app.handle());

            // ── Conversation-mode menu refresh thread (2026-05-28) ──
            // Polls systemctl --user state every 3 s and rewrites the
            // Conversation-mode submenu header + ✓ prefix so the user
            // can see at a glance which mode is active. Two systemctl
            // is-active calls per tick (~10 ms each) — cheap.
            {
                let app_handle = app.handle().clone();
                refresh_mode_menu(&app_handle);
                std::thread::spawn(move || loop {
                    std::thread::sleep(std::time::Duration::from_secs(3));
                    refresh_mode_menu(&app_handle);
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
            set_share_label,
            get_primary_monitor_info,
            list_screen_sources,
            keys_read,
            keys_set,
            keys_clear,
            keys_restart_agent,
            mcp_list,
            mcp_set_enabled,
            mcp_remove,
            mcp_add,
            kiosk::enter_kiosk_on_monitor,
            kiosk::exit_kiosk,
            kiosk::kiosk_state,
            kiosk::get_bridge_token,
            kiosk::mint_livekit_token,
            kiosk::open_chat_window,
            kiosk::close_chat_window,
            get_active_mode,
        ])
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app_handle, event| {
            // Unified-app Phase 0/1: stop any processes the supervisor started
            // (no-op when JARVIS_DESKTOP_OWNS_AGENT was OFF — state holds None).
            if let tauri::RunEvent::ExitRequested { .. } = event {
                if let Some(state) =
                    app_handle.try_state::<Mutex<Option<supervisor::Supervisor>>>()
                {
                    if let Ok(mut guard) = state.lock() {
                        if let Some(sup) = guard.as_mut() {
                            sup.stop_all();
                        }
                    }
                }
            }
        });
}
