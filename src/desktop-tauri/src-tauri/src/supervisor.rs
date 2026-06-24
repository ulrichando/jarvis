//! Desktop-owned process supervisor (Phase 0/1 of the unified-app design).
//!
//! std-only: spawns + health-gates child processes directly, no new crates —
//! matches the lean Cargo.toml and the codebase's existing `sqlite3`/`systemctl`
//! shell-out style. Everything here is inert unless `JARVIS_DESKTOP_OWNS_AGENT=1`
//! (default OFF: the systemd `jarvis-voice-agent.service` stays in charge).
//!
//! Spec: docs/superpowers/specs/2026-06-24-unified-local-app-design.md
//! Plan: docs/superpowers/plans/2026-06-24-unified-app-phase01-supervisor.md
use serde::Deserialize;
use std::net::{TcpStream, ToSocketAddrs};
use std::process::{Child, Command};
use std::time::{Duration, Instant};

#[derive(Debug, Clone, Deserialize)]
pub struct ManagedProcess {
    pub name: String,
    pub command: String,
    #[serde(default)]
    pub args: Vec<String>,
    /// Working dir, relative to repo root (resolved by `maybe_start_managed_stack`).
    #[serde(default)]
    pub cwd: Option<String>,
    /// "host:port" polled for readiness before the next process starts.
    /// None = no port gate (e.g. the voice-agent, whose real health is a smoke
    /// turn proven manually — not a TCP port).
    #[serde(default)]
    pub health_addr: Option<String>,
    /// Start order, ascending.
    pub order: u32,
}

#[derive(Debug, Clone, Deserialize)]
pub struct RunManifest {
    pub processes: Vec<ManagedProcess>,
}

impl RunManifest {
    pub fn from_json(s: &str) -> Result<Self, String> {
        serde_json::from_str(s).map_err(|e| e.to_string())
    }

    /// Processes sorted by ascending `order`.
    pub fn ordered(&self) -> Vec<ManagedProcess> {
        let mut v = self.processes.clone();
        v.sort_by_key(|p| p.order);
        v
    }
}

/// Parse simple `KEY=VALUE` env files (skips blanks, `#` comments, and a
/// leading `export `; strips surrounding quotes). Later files override earlier
/// ones, so callers pass the higher-priority file last. Missing files are
/// skipped silently — mirrors how start-desktop.sh sources what exists.
pub fn load_env_files(paths: &[std::path::PathBuf]) -> Vec<(String, String)> {
    let mut map: std::collections::BTreeMap<String, String> = std::collections::BTreeMap::new();
    for p in paths {
        let Ok(text) = std::fs::read_to_string(p) else {
            continue;
        };
        for line in text.lines() {
            let line = line.trim();
            let line = line.strip_prefix("export ").unwrap_or(line);
            if line.is_empty() || line.starts_with('#') {
                continue;
            }
            if let Some((k, v)) = line.split_once('=') {
                let v = v.trim().trim_matches('"').trim_matches('\'');
                map.insert(k.trim().to_string(), v.to_string());
            }
        }
    }
    map.into_iter().collect()
}

/// Poll `addr` ("host:port") until a TCP connection succeeds or `timeout`
/// elapses. Pure std — no HTTP-client dependency. Used to gate process start
/// order (e.g. don't start the brain until the SFU's port accepts).
pub fn wait_for_port(addr: &str, timeout: Duration) -> bool {
    let deadline = Instant::now() + timeout;
    loop {
        if let Ok(mut addrs) = addr.to_socket_addrs() {
            if let Some(sock) = addrs.next() {
                if TcpStream::connect_timeout(&sock, Duration::from_millis(500)).is_ok() {
                    return true;
                }
            }
        }
        if Instant::now() >= deadline {
            return false;
        }
        std::thread::sleep(Duration::from_millis(250));
    }
}

#[derive(Debug, Clone)]
#[allow(dead_code)] // building block for the crash-monitor (deferred — see note below)
pub struct RestartPolicy {
    pub max_restarts: usize,
    pub window: Duration,
}

/// True if fewer than `max_restarts` restarts happened within the trailing
/// `window` ending at `now`. Pure → unit-testable with injected instants.
///
/// NOTE: this is the tested decision logic; the background monitor thread that
/// detects a child crash and CALLS this is deferred to a follow-up plan (it
/// touches live process state — exactly the kind of risk this first cut avoids).
/// Annotated `#[allow(dead_code)]` like the existing `voice_session_within_60s`
/// building block in main.rs until that monitor lands.
#[allow(dead_code)]
pub fn restart_allowed(history: &[Instant], now: Instant, policy: &RestartPolicy) -> bool {
    let recent = history
        .iter()
        .filter(|t| now.duration_since(**t) <= policy.window)
        .count();
    recent < policy.max_restarts
}

/// Owns the child processes the desktop started. `stop_all` kills them in
/// reverse start order.
pub struct Supervisor {
    children: Vec<(String, Child)>,
}

impl Supervisor {
    pub fn new() -> Self {
        Self { children: Vec::new() }
    }

    /// Spawn one managed process: set cwd + env, launch, and (if it declares a
    /// `health_addr`) block until that port accepts or `health_timeout` elapses.
    /// Returns Err(name + reason) on spawn failure or health timeout so the
    /// caller can surface a clear error instead of a silent hang.
    pub fn spawn_one(
        &mut self,
        p: &ManagedProcess,
        env: &[(String, String)],
        health_timeout: Duration,
    ) -> Result<(), String> {
        let mut cmd = Command::new(&p.command);
        cmd.args(&p.args);
        if let Some(cwd) = &p.cwd {
            cmd.current_dir(cwd);
        }
        for (k, v) in env {
            cmd.env(k, v);
        }
        let child = cmd
            .spawn()
            .map_err(|e| format!("{}: spawn failed: {}", p.name, e))?;
        self.children.push((p.name.clone(), child));
        if let Some(addr) = &p.health_addr {
            if !wait_for_port(addr, health_timeout) {
                return Err(format!(
                    "{}: not healthy on {} within {:?}",
                    p.name, addr, health_timeout
                ));
            }
        }
        Ok(())
    }

    /// Kill everything we started, in reverse start order. Best-effort.
    pub fn stop_all(&mut self) {
        while let Some((_name, mut child)) = self.children.pop() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }

    /// Names of currently-tracked children (oldest first).
    pub fn running(&self) -> Vec<String> {
        self.children.iter().map(|(n, _)| n.clone()).collect()
    }
}

impl Default for Supervisor {
    fn default() -> Self {
        Self::new()
    }
}

/// If `JARVIS_DESKTOP_OWNS_AGENT=1`, load the manifest, spawn the managed stack
/// (ordered, health-gated), and return the live `Supervisor` (held by the caller
/// for shutdown). Otherwise return `None` — the legacy systemd path stays in
/// charge, which is the DEFAULT. On any partial-start failure it stops what it
/// started and returns `None` (never leaves a half-started stack).
pub fn maybe_start_managed_stack(
    repo_root: &std::path::Path,
    manifest_path: &std::path::Path,
    env_files: &[std::path::PathBuf],
    default_on: bool,
) -> Option<Supervisor> {
    // Explicit env wins; otherwise default ON when bundled (the installed .deb)
    // and OFF in dev. So the installed app owns the voice stack out of the box,
    // while the dev binary stays hands-off (systemd keeps running it).
    let enabled = match std::env::var("JARVIS_DESKTOP_OWNS_AGENT").as_deref() {
        Ok("1") => true,
        Ok("0") => false,
        _ => default_on,
    };
    if !enabled {
        return None;
    }
    let text = std::fs::read_to_string(manifest_path).ok()?;
    let manifest = RunManifest::from_json(&text).ok()?;
    let env = load_env_files(env_files);
    let mut sup = Supervisor::new();
    for mut p in manifest.ordered() {
        if let Some(cwd) = &p.cwd {
            p.cwd = Some(repo_root.join(cwd).to_string_lossy().into_owned());
        }
        // Resolve a repo-relative command (e.g. "./src/voice-agent/.venv/bin/python").
        if p.command.starts_with("./") {
            p.command = repo_root.join(&p.command).to_string_lossy().into_owned();
        }
        if let Err(e) = sup.spawn_one(&p, &env, Duration::from_secs(60)) {
            eprintln!("[supervisor] {e}");
            sup.stop_all();
            return None;
        }
    }
    Some(sup)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn manifest_parses_and_orders() {
        let json = r#"{"processes":[
            {"name":"b","command":"x","order":2},
            {"name":"a","command":"y","order":1,"health_addr":"127.0.0.1:7880"}
        ]}"#;
        let m = RunManifest::from_json(json).expect("parse");
        let ordered = m.ordered();
        assert_eq!(ordered[0].name, "a");
        assert_eq!(ordered[1].name, "b");
        assert_eq!(ordered[0].health_addr.as_deref(), Some("127.0.0.1:7880"));
        assert!(ordered[1].args.is_empty());
    }

    #[test]
    fn load_env_files_parses_and_last_wins() {
        let dir = std::env::temp_dir().join(format!("jv-env-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let a = dir.join("a.env");
        let b = dir.join("b.env");
        std::fs::write(&a, "# c\nexport FOO=1\nBAR=\"two\"\n").unwrap();
        std::fs::write(&b, "FOO=override\n\n").unwrap();
        let env = load_env_files(&[a, b]);
        let map: std::collections::BTreeMap<_, _> = env.into_iter().collect();
        assert_eq!(map.get("FOO").map(String::as_str), Some("override")); // b wins
        assert_eq!(map.get("BAR").map(String::as_str), Some("two")); // quotes stripped
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn wait_for_port_true_when_listening_false_when_not() {
        // True: a live listener is connectable.
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let addr = listener.local_addr().unwrap().to_string();
        assert!(wait_for_port(&addr, Duration::from_secs(2)));
        // False: port 1 (tcpmux) can't be bound without root, so nothing in a
        // user-session test ever listens there → connection refused → false.
        // (A just-freed ephemeral port is unreliable here: under the full suite
        // a parallel test can grab the same port number before this runs.)
        assert!(!wait_for_port("127.0.0.1:1", Duration::from_millis(400)));
    }

    #[test]
    fn restart_allowed_respects_window_and_cap() {
        let now = Instant::now();
        let policy = RestartPolicy { max_restarts: 3, window: Duration::from_secs(60) };
        let hist = vec![now - Duration::from_secs(5), now - Duration::from_secs(10)];
        assert!(restart_allowed(&hist, now, &policy));
        let hist3 = vec![
            now - Duration::from_secs(1),
            now - Duration::from_secs(2),
            now - Duration::from_secs(3),
        ];
        assert!(!restart_allowed(&hist3, now, &policy));
        let old = vec![now - Duration::from_secs(120); 3];
        assert!(restart_allowed(&old, now, &policy));
    }

    #[cfg(unix)]
    #[test]
    fn spawn_tracks_child_and_stop_clears() {
        let mut sup = Supervisor::new();
        let p = ManagedProcess {
            name: "dummy".into(),
            command: "sleep".into(),
            args: vec!["30".into()],
            cwd: None,
            health_addr: None,
            order: 1,
        };
        sup.spawn_one(&p, &[], Duration::from_secs(1)).expect("spawn");
        assert_eq!(sup.running(), vec!["dummy".to_string()]);
        sup.stop_all();
        assert!(sup.running().is_empty());
    }

    #[test]
    fn maybe_start_gate_respects_flag_and_default() {
        // dev (default_on=false) + flag unset → stays off
        std::env::remove_var("JARVIS_DESKTOP_OWNS_AGENT");
        assert!(maybe_start_managed_stack(
            std::path::Path::new("/nonexistent-repo"),
            std::path::Path::new("/nonexistent-manifest.json"),
            &[], false).is_none());
        // bundled (default_on=true) but explicit "0" → explicit wins, stays off
        std::env::set_var("JARVIS_DESKTOP_OWNS_AGENT", "0");
        assert!(maybe_start_managed_stack(
            std::path::Path::new("/nonexistent-repo"),
            std::path::Path::new("/nonexistent-manifest.json"),
            &[], true).is_none());
        std::env::remove_var("JARVIS_DESKTOP_OWNS_AGENT");
    }
}
