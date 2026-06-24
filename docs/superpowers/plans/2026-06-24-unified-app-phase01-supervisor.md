# Unified Local App — Phase 0/1: desktop-owned process supervisor (flag-gated, default OFF)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the Tauri desktop the ability to spawn, health-gate, supervise, and stop the voice-agent + LiveKit SFU itself — behind `JARVIS_DESKTOP_OWNS_AGENT=1`, default OFF so systemd stays in charge and nothing breaks.

**Architecture:** A new std-only Rust module `supervisor.rs` in `src-tauri/src/`. It reads a JSON run-manifest, spawns each managed process in order (cwd + env from `keys.env`/`.env`), TCP-health-gates the ones that expose a port (the SFU), tracks the `Child` handles, and kills them in reverse on shutdown. `main.rs` calls one entrypoint behind the flag; the existing systemctl path is untouched and remains the default. The brain's Python is never modified — the supervisor invokes the *existing in-tree venv* exactly as systemd does today.

**Tech Stack:** Rust (Tauri v2), `std::process`/`std::net` only (no new crates — matches the lean Cargo.toml + frozen-tray caution), `serde`/`serde_json` (already deps). Spec: `docs/superpowers/specs/2026-06-24-unified-local-app-design.md`.

**Scope (per `.claude/rules/regression-prevention.md`):**
```
SCOPE:  src/desktop-tauri/src-tauri/src/supervisor.rs        (NEW)
        src/desktop-tauri/src-tauri/src/main.rs              (mod decl + one setup hook + shutdown)
        src/desktop-tauri/src-tauri/resources/run-manifest.json (NEW)
OUT:    src/voice-agent/** (brain UNCHANGED), src/cli/** (off-limits),
        the frozen tray indicator in main.rs (tray_image_for / ring / poll / states / icon),
        setup/systemd/** (kept as-is; still the default)
WHY OUT: the safety premise is the brain's behaviour doesn't change; the tray is locked (desktop-tauri.md).
```

**Verification baseline:** `cd src/desktop-tauri/src-tauri && cargo test` is the per-task gate. Release re-embed (`npm run build && cargo build --release`) is only needed for the Phase-0 live run in Task 8, not per-task.

---

### Task 1: Run-manifest types + ordering

**Files:**
- Create: `src/desktop-tauri/src-tauri/src/supervisor.rs`

- [ ] **Step 1: Write the module with the manifest types + a failing test**

```rust
//! Desktop-owned process supervisor (Phase 0/1 of the unified-app design).
//! std-only: shells out / spawns directly, no new crates — matches the lean
//! Cargo.toml and the codebase's existing `sqlite3`/`systemctl` shell-out style.
use serde::Deserialize;

#[derive(Debug, Clone, Deserialize)]
pub struct ManagedProcess {
    pub name: String,
    pub command: String,
    #[serde(default)]
    pub args: Vec<String>,
    /// Working dir, relative to repo root (resolved by the caller).
    #[serde(default)]
    pub cwd: Option<String>,
    /// "host:port" polled for readiness before the next process starts.
    /// None = no port gate (e.g. the voice-agent, whose real health is a
    /// smoke turn, proven manually in Task 8 — not a TCP port).
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
}
```

- [ ] **Step 2: Make the module compile by declaring it in main.rs**

Add this line near the other `mod` declarations at the top of `src/desktop-tauri/src-tauri/src/main.rs` (read the file to place it next to existing `mod` lines, e.g. above `fn main`):

```rust
mod supervisor;
```

- [ ] **Step 3: Run the test — expect PASS (types compile, parse works)**

Run: `cd src/desktop-tauri/src-tauri && cargo test supervisor::tests::manifest_parses_and_orders`
Expected: `test result: ok. 1 passed`

- [ ] **Step 4: Commit**

```bash
git add src/desktop-tauri/src-tauri/src/supervisor.rs src/desktop-tauri/src-tauri/src/main.rs
git commit -m "feat(desktop): run-manifest types for the process supervisor"
```

---

### Task 2: Env-file loader

**Files:**
- Modify: `src/desktop-tauri/src-tauri/src/supervisor.rs`

- [ ] **Step 1: Write the failing test (append inside `mod tests`)**

```rust
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
        assert_eq!(map.get("BAR").map(String::as_str), Some("two"));      // quotes stripped
        std::fs::remove_dir_all(&dir).ok();
    }
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `cd src/desktop-tauri/src-tauri && cargo test supervisor::tests::load_env_files`
Expected: FAIL — `cannot find function load_env_files`

- [ ] **Step 3: Implement `load_env_files` (add to supervisor.rs, above `mod tests`)**

```rust
/// Parse simple `KEY=VALUE` env files (skips blanks, `#` comments, and a
/// leading `export `; strips surrounding quotes). Later files override earlier
/// ones, so callers pass the higher-priority file last. Missing files are
/// skipped silently — mirrors how start-desktop.sh sources what exists.
pub fn load_env_files(paths: &[std::path::PathBuf]) -> Vec<(String, String)> {
    let mut map: std::collections::BTreeMap<String, String> = std::collections::BTreeMap::new();
    for p in paths {
        let Ok(text) = std::fs::read_to_string(p) else { continue };
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
```

- [ ] **Step 4: Run the test — expect PASS**

Run: `cd src/desktop-tauri/src-tauri && cargo test supervisor::tests::load_env_files`
Expected: `ok. 1 passed`

- [ ] **Step 5: Commit**

```bash
git add src/desktop-tauri/src-tauri/src/supervisor.rs
git commit -m "feat(desktop): env-file loader for supervised processes"
```

---

### Task 3: TCP health-check (`wait_for_port`)

**Files:**
- Modify: `src/desktop-tauri/src-tauri/src/supervisor.rs`

- [ ] **Step 1: Write the failing test (append inside `mod tests`)**

```rust
    #[test]
    fn wait_for_port_true_when_listening_false_when_not() {
        use std::time::Duration;
        // A bound listener → connectable → true fast.
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let addr = listener.local_addr().unwrap().to_string();
        assert!(wait_for_port(&addr, Duration::from_secs(2)));
        // An unbound port → not connectable → false after the timeout.
        drop(listener);
        assert!(!wait_for_port(&addr, Duration::from_millis(400)));
    }
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `cd src/desktop-tauri/src-tauri && cargo test supervisor::tests::wait_for_port`
Expected: FAIL — `cannot find function wait_for_port`

- [ ] **Step 3: Implement `wait_for_port`**

```rust
use std::net::{TcpStream, ToSocketAddrs};
use std::time::{Duration, Instant};

/// Poll `addr` ("host:port") until a TCP connection succeeds or `timeout`
/// elapses. Pure std — no HTTP-client dependency. Used to gate process start
/// order (e.g. don't start the brain until the SFU's port is accepting).
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
```

- [ ] **Step 4: Run the test — expect PASS**

Run: `cd src/desktop-tauri/src-tauri && cargo test supervisor::tests::wait_for_port`
Expected: `ok. 1 passed`

- [ ] **Step 5: Commit**

```bash
git add src/desktop-tauri/src-tauri/src/supervisor.rs
git commit -m "feat(desktop): TCP health-gate for supervised process start order"
```

---

### Task 4: Bounded restart policy (pure)

**Files:**
- Modify: `src/desktop-tauri/src-tauri/src/supervisor.rs`

- [ ] **Step 1: Write the failing test (append inside `mod tests`)**

```rust
    #[test]
    fn restart_allowed_respects_window_and_cap() {
        use std::time::{Duration, Instant};
        let now = Instant::now();
        let policy = RestartPolicy { max_restarts: 3, window: Duration::from_secs(60) };
        // 2 recent restarts < cap of 3 → allowed.
        let hist = vec![now - Duration::from_secs(5), now - Duration::from_secs(10)];
        assert!(restart_allowed(&hist, now, &policy));
        // 3 recent restarts == cap → NOT allowed.
        let hist3 = vec![now - Duration::from_secs(1),
                         now - Duration::from_secs(2),
                         now - Duration::from_secs(3)];
        assert!(!restart_allowed(&hist3, now, &policy));
        // 3 restarts but all OUTSIDE the window → allowed again.
        let old = vec![now - Duration::from_secs(120); 3];
        assert!(restart_allowed(&old, now, &policy));
    }
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `cd src/desktop-tauri/src-tauri && cargo test supervisor::tests::restart_allowed`
Expected: FAIL — `cannot find type RestartPolicy`

- [ ] **Step 3: Implement the policy**

```rust
#[derive(Debug, Clone)]
#[allow(dead_code)] // building block for the crash-monitor (deferred — see note below)
pub struct RestartPolicy {
    pub max_restarts: usize,
    pub window: Duration,
}

/// True if fewer than `max_restarts` restarts happened within the trailing
/// `window` ending at `now`. Pure → unit-testable with injected instants.
/// Past the cap, the supervisor stops respawning and surfaces the failure
/// (no infinite respawn loop — mirrors the evolution watchdog's bounded retry).
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
```

- [ ] **Step 4: Run the test — expect PASS**

Run: `cd src/desktop-tauri/src-tauri && cargo test supervisor::tests::restart_allowed`
Expected: `ok. 1 passed`

- [ ] **Step 5: Commit**

```bash
git add src/desktop-tauri/src-tauri/src/supervisor.rs
git commit -m "feat(desktop): bounded restart policy for the supervisor"
```

---

### Task 5: Supervisor spawn / stop / track

**Files:**
- Modify: `src/desktop-tauri/src-tauri/src/supervisor.rs`

- [ ] **Step 1: Write the failing test (append inside `mod tests`) — unix-only (dev/CI is Linux)**

```rust
    #[cfg(unix)]
    #[test]
    fn spawn_tracks_child_and_stop_clears() {
        use std::time::Duration;
        let mut sup = Supervisor::new();
        let p = ManagedProcess {
            name: "dummy".into(),
            command: "sleep".into(),
            args: vec!["30".into()],
            cwd: None,
            health_addr: None, // no port gate for the dummy
            order: 1,
        };
        sup.spawn_one(&p, &[], Duration::from_secs(1)).expect("spawn");
        assert_eq!(sup.running(), vec!["dummy".to_string()]);
        sup.stop_all();
        assert!(sup.running().is_empty());
    }
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `cd src/desktop-tauri/src-tauri && cargo test supervisor::tests::spawn_tracks_child`
Expected: FAIL — `cannot find type Supervisor`

- [ ] **Step 3: Implement the `Supervisor`**

```rust
use std::process::{Child, Command};

/// Owns the child processes the desktop started. Drop-safe via `stop_all`.
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
```

- [ ] **Step 4: Run the test — expect PASS**

Run: `cd src/desktop-tauri/src-tauri && cargo test supervisor::tests::spawn_tracks_child`
Expected: `ok. 1 passed`

- [ ] **Step 5: Commit**

```bash
git add src/desktop-tauri/src-tauri/src/supervisor.rs
git commit -m "feat(desktop): Supervisor spawn/stop/track for managed processes"
```

---

### Task 6: Flag-gated entrypoint `maybe_start_managed_stack`

**Files:**
- Modify: `src/desktop-tauri/src-tauri/src/supervisor.rs`

- [ ] **Step 1: Write the failing test (append inside `mod tests`)**

```rust
    #[test]
    fn maybe_start_returns_none_when_flag_unset() {
        // Default OFF: with the flag unset, the supervisor must NOT take over —
        // the legacy systemd path stays in charge. (We only assert the disabled
        // branch here; the enabled branch is the live Task 8 verification.)
        std::env::remove_var("JARVIS_DESKTOP_OWNS_AGENT");
        let none = maybe_start_managed_stack(
            std::path::Path::new("/nonexistent-repo"),
            std::path::Path::new("/nonexistent-manifest.json"),
            &[],
        );
        assert!(none.is_none());
    }
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `cd src/desktop-tauri/src-tauri && cargo test supervisor::tests::maybe_start_returns_none`
Expected: FAIL — `cannot find function maybe_start_managed_stack`

- [ ] **Step 3: Implement the entrypoint**

```rust
/// If `JARVIS_DESKTOP_OWNS_AGENT=1`, load the manifest, spawn the managed
/// stack (ordered, health-gated), and return the live `Supervisor` (held by the
/// caller for shutdown). Otherwise return `None` — the legacy systemd path stays
/// in charge, which is the DEFAULT. On any partial-start failure it stops what it
/// started and returns `None` (never leaves a half-started stack).
pub fn maybe_start_managed_stack(
    repo_root: &std::path::Path,
    manifest_path: &std::path::Path,
    env_files: &[std::path::PathBuf],
) -> Option<Supervisor> {
    if std::env::var("JARVIS_DESKTOP_OWNS_AGENT").as_deref() != Ok("1") {
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
```

- [ ] **Step 4: Run the test — expect PASS, then run the whole supervisor suite**

Run: `cd src/desktop-tauri/src-tauri && cargo test supervisor`
Expected: all supervisor tests pass (6 tests: manifest, env, port, restart, spawn, maybe_start)

- [ ] **Step 5: Commit**

```bash
git add src/desktop-tauri/src-tauri/src/supervisor.rs
git commit -m "feat(desktop): flag-gated maybe_start_managed_stack entrypoint (default OFF)"
```

---

### Task 7: Wire into `main.rs` (one setup hook + shutdown), default OFF

**Files:**
- Modify: `src/desktop-tauri/src-tauri/src/main.rs`

- [ ] **Step 1: Read the Tauri builder/setup region**

Run: `grep -n "tauri::Builder\|\.setup(\|\.run(\|on_window_event\|RunEvent\|ExitRequested" src/desktop-tauri/src-tauri/src/main.rs`
Note the line numbers of the `.setup(|app| {` closure and the run/exit handling — that's where the two hooks go.

- [ ] **Step 2: Add the start hook inside the `.setup(...)` closure**

Inside the `setup` closure (use the line from Step 1), add — storing the Supervisor in Tauri-managed state so it lives for the app's lifetime:

```rust
// Unified-app Phase 0/1: if JARVIS_DESKTOP_OWNS_AGENT=1, the desktop spawns +
// supervises the voice stack itself; otherwise the systemd service stays in
// charge (default). See docs/superpowers/specs/2026-06-24-unified-local-app-design.md
{
    let manifest = repo_root().join("src/desktop-tauri/src-tauri/resources/run-manifest.json");
    let env_files = {
        let mut v = _repo_env_files();
        v.push(_keys_file()); // keys.env last = highest priority
        v
    };
    let sup = supervisor::maybe_start_managed_stack(&repo_root(), &manifest, &env_files);
    app.manage(std::sync::Mutex::new(sup));
}
```

- [ ] **Step 3: Add the shutdown hook**

In the run/exit handling identified in Step 1 (the `RunEvent::ExitRequested` arm, or a `.on_window_event` close handler — whichever the file already uses), stop the managed stack:

```rust
// Stop any processes the supervisor started (no-op when the flag was OFF).
if let Some(state) = app_handle.try_state::<std::sync::Mutex<Option<supervisor::Supervisor>>>() {
    if let Ok(mut guard) = state.lock() {
        if let Some(sup) = guard.as_mut() {
            sup.stop_all();
        }
    }
}
```

(If the existing exit handler exposes the handle under a different name than `app_handle`, use that name — read the surrounding code.)

- [ ] **Step 4: Build to confirm the wiring compiles**

Run: `cd src/desktop-tauri/src-tauri && cargo build`
Expected: compiles clean (warnings ok). If `app.manage`/`try_state` types mismatch, align the `Mutex<Option<Supervisor>>` type between Step 2 and Step 3.

- [ ] **Step 5: Run the full desktop test suite (no regressions)**

Run: `cd src/desktop-tauri/src-tauri && cargo test`
Expected: all tests pass, including the pre-existing `voice_session_within_60s` tests.

- [ ] **Step 6: Commit**

```bash
git add src/desktop-tauri/src-tauri/src/main.rs
git commit -m "feat(desktop): wire flag-gated process supervisor into app lifecycle (default OFF)"
```

---

### Task 8: Run-manifest resource + Phase-0/1 live verification

**Files:**
- Create: `src/desktop-tauri/src-tauri/resources/run-manifest.json`

- [ ] **Step 1: Derive the real SFU config path + confirm the venv/entrypoint**

Run these to read the live values (machine-specific — fill them into the manifest, don't guess):
```bash
pgrep -fa livekit-server.bin     # → the exact --config path the SFU uses
ls src/voice-agent/.venv/bin/python src/voice-agent/jarvis_agent.py   # confirm entrypoint exists
ss -ltnp | grep 7880             # confirm the SFU port
```

- [ ] **Step 2: Write the manifest with the derived values**

Create `src/desktop-tauri/src-tauri/resources/run-manifest.json` (replace `<CONFIG_PATH>` with the `--config` value from Step 1; if the SFU takes no `--config`, drop the `args`):

```json
{
  "processes": [
    {
      "name": "livekit-sfu",
      "command": "./src/voice-agent/livekit-server.bin",
      "args": ["--config", "<CONFIG_PATH>"],
      "cwd": "src/voice-agent",
      "health_addr": "127.0.0.1:7880",
      "order": 1
    },
    {
      "name": "voice-agent",
      "command": "./src/voice-agent/.venv/bin/python",
      "args": ["jarvis_agent.py", "start"],
      "cwd": "src/voice-agent",
      "health_addr": null,
      "order": 2
    }
  ]
}
```

- [ ] **Step 3: Commit the manifest**

```bash
git add src/desktop-tauri/src-tauri/resources/run-manifest.json
git commit -m "feat(desktop): run-manifest for the supervised voice stack"
```

- [ ] **Step 4: Phase-0 live proof — desktop spawns the stack + smoke turn**

Stop the systemd service so the desktop is the only owner, then launch the desktop with the flag on:
```bash
systemctl --user stop jarvis-voice-agent.service
cd src/desktop-tauri && npm run build && cargo build --release
JARVIS_DESKTOP_OWNS_AGENT=1 ./src-tauri/target/release/jarvis-desktop &
```
Verify (within ~60s) — the required gate is the startup log, which is definitively correct:
```bash
ss -ltnp | grep 7880                 # SFU up (started by the desktop)
pgrep -fa "jarvis_agent.py start"    # brain up (child of the desktop, not systemd)
# Required gate: the brain started healthy under the desktop-supplied env —
# look for a successful worker registration / startup line and NO traceback:
tail -n 60 ~/.local/share/jarvis/logs/voice-agent.log
```
Expected: SFU listening, brain running as a **child of the desktop** (not systemd), and the log shows a clean startup + SFU/worker registration with no traceback.
**If the log shows a traceback or missing-key error, STOP** — the spawned env is wrong (likely a key absent because `keys.env` wasn't passed/ordered right); fix the manifest/env ordering before continuing.

Optional stronger proof (a full turn): exercise the same smoke-turn the watchdog uses — see how `pipeline/automod/watchdog.py` invokes `pipeline/automod/selftest.py`, and run it that way. A clean log + a real interaction both confirm Phase 0.

- [ ] **Step 5: Phase-1 parity proof — flag OFF falls back cleanly to systemd**

```bash
pkill -f "target/release/jarvis-desktop"     # quit the flag-on desktop (stop_all kills its children)
sleep 2
pgrep -fa "jarvis_agent.py start" || echo "children stopped cleanly ✓"
systemctl --user start jarvis-voice-agent.service   # legacy path back
./src-tauri/target/release/jarvis-desktop &         # NO flag → supervisor returns None
pgrep -fa "jarvis_agent.py start"                   # the systemd-owned brain, untouched
```
Expected: with the flag OFF, the desktop does not spawn the brain; the systemd service runs it exactly as before. **This proves nothing breaks for the default path.**

- [ ] **Step 6: Record the result**

Append a one-line outcome to the spec's bottom (`docs/superpowers/specs/2026-06-24-unified-local-app-design.md`): date, "Phase 0/1 live-verified: desktop-spawn smoke turn OK; flag-OFF systemd parity OK", and commit it with an explicit pathspec.

---

## Notes for the executor
- **No `git add -A`** — the repo carries concurrent uncommitted work from a parallel session; every commit step above uses explicit pathspecs. Verify each with `git show --stat HEAD`.
- **No Co-Authored-By / attribution trailers** (CLAUDE.md).
- **Don't restart `jarvis-voice-agent.service` within 60s of a live turn** — Task 8 stops it deliberately for the proof; check `turn_telemetry.db` first and tell the user if a session is active.
- **Do not touch the tray indicator** in main.rs (frozen per desktop-tauri.md) — the supervisor work is unrelated to it.
- This plan is Phase 0/1 only. Phase 2 (bundling the venv+SFU into the installable app) and Phase 3 (portable Python + delta updates) are separate plans, written after this is live-verified.
