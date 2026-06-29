# JARVIS local install/uninstall toolkit + web VPS-primary/local-fallback — design

**Date:** 2026-06-28
**Status:** approved (iterative), ready to implement

## Goal

A clean, symmetric way to **install and uninstall** the LOCAL JARVIS stack
(CLI · Voice Agent · Desktop · Web-fallback) — globally or per-component — plus
**VPS-primary / local-fallback** for the web UI so it keeps working when
`0wlan.com` is unavailable.

## What already exists (don't rebuild)

- `install.sh` (1033 lines, + `install.cmd`/`install.ps1`): one-shot installer
  for CLI + Voice + Desktop + Web. Idempotent; channel skips via
  `JARVIS_SKIP_CLI/VOICE/DESKTOP/WEB`; functions `install_cli`, `install_web`,
  `install_voice_agent` (uv/pip venv), `install_playwright_chromium`,
  `install_systemd_units`.
- **No uninstaller anywhere** — this is the real gap.
- Desktop "Open in Browser" → `main.rs::probe_jarvis_web()`: honors
  `JARVIS_WEB_URL` (currently `https://0wlan.com` via `~/.jarvis/desktop.env`),
  else probes localhost ports. Reads the env at call time (no rebuild to change
  the URL — but the failover logic below DOES need a rebuild).
- Web is deployed on the VPS (`0wlan.com`). The local web has its **own
  database** (separate from the VPS's).

## Components (local only)

CLI · Voice Agent · Desktop · **Web (as local fallback standby)**.
(The VPS web deployment is a separate remote; this toolkit never touches it.)

## Part A — Install / Uninstall

### `install.sh` (enhance)
- Default install = CLI + Voice + Desktop + Web (web included as the fallback
  standby).
- Add symmetric flags `--cli | --voice | --desktop | --web | --all` (default
  `--all`). Keep `JARVIS_SKIP_*` working for back-compat.

### `uninstall.sh` (new, Linux-first)
- Flags `--cli | --voice | --desktop | --web | --all` (default `--all`).
- **Tiered scope (safe default):**
  - *default* → **software only**: stop+disable+rm the per-component systemd
    units, rm `.venv` (voice), rm `~/.local/bin` symlinks (`jarvis`,
    `jarvis-desktop`), rm desktop `target/` build, stop+rm docker containers
    (`kokoro-tts`; honcho stack via its compose), rm web `node_modules`+`.next`,
    rm `.desktop` entries (menu + autostart). **Keeps data + repo.**
  - `--purge` → also rm `~/.jarvis` + `~/.local/share/jarvis` (keys, memories,
    conversations, telemetry, logs, models).
  - `--nuke` → also rm the repo (`INSTALL_DIR`); **refuses if `git status` shows
    uncommitted changes unless `--force`**.
  - `--yes` → non-interactive; otherwise confirm before any destructive step.
- `bin/jarvis-uninstall` thin wrapper.
- Windows `.cmd`/`.ps1` uninstallers **deferred** (mirror `install.ps1` when
  Windows voice support lands).

### Per-component artifact map (what each channel owns)
- **cli:** `~/.local/bin/jarvis` symlink (+ installed binary if present).
- **voice:** `.venv`, units `jarvis-voice-agent / voice-client /
  livekit-server` + the timers/services (computer-use, backups, evolution,
  cron, curator, health, key-age, log-rotate, …), `kokoro-tts` + honcho docker
  containers; models (`--purge` only).
- **desktop:** `src-tauri/target` build, `jarvis-desktop` symlink, `.desktop`
  entries (menu + autostart).
- **web:** `src/web` `node_modules` + `.next` + the local web auto-start; local
  Postgres data is data → `--purge` only.

## Part B — Web failover (VPS primary, local fallback)

- `main.rs::probe_jarvis_web()`: **health-check `JARVIS_WEB_URL` via a PUBLIC
  route** (`GET {origin}/install.sh` → 200; the app root is behind the Access
  OTP so it can't be the probe) before returning it. On failure, fall through
  to the existing local probe. (Rust change → `cargo build --release`.)
- Local web = **hot standby**: `~/.jarvis/desktop.env`
  `JARVIS_WEB_AUTO_START=true` (re-enabled) so the fallback is running +
  instant. `JARVIS_WEB_URL=https://0wlan.com` stays the preferred. Net: opens
  `0wlan.com` when up, localhost only when the VPS is down.
- **Caveat:** the local fallback uses the local DB (separate from the VPS);
  two-way sync is out of scope.

## Data policy
Default uninstall = software only (protects memories/keys/repo). `--purge` =
data. `--nuke` = repo (guarded on uncommitted changes).

## Out of scope
- VPS ↔ local data sync.
- Windows `.cmd`/`.ps1` uninstallers (deferred).
- Touching the VPS deployment (this toolkit is local-only).

## Verification
- `install.sh --cli` on a clean container → `jarvis` works.
- `uninstall.sh --voice` → units gone, data intact (memories/keys present).
- `uninstall.sh --nuke` with a dirty tree → refuses without `--force`.
- `main.rs`: VPS up → `0wlan.com`; simulate VPS down (block `/install.sh`
  reachability) → localhost.
