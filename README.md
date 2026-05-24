# JARVIS

A voice-first AI assistant. Real-time speech in, real-time speech out, with direct tools for desktop / browser / multi-step coding work. Runs on Linux as a multi-process LiveKit Agents Python worker, fronted by a Tauri desktop UI, a Next.js web app, and a Claude-Code-shaped CLI.

## Install

One-shot install of all four channels (CLI + Voice Agent + Desktop + Web). Pick the row that matches your shell.

| Platform | Shell | Command |
|---|---|---|
| Linux / macOS | bash | `curl -fsSL https://raw.githubusercontent.com/ulrichando/jarvis/master/install.sh \| bash` |
| Windows | PowerShell | `iex (irm https://raw.githubusercontent.com/ulrichando/jarvis/master/install.ps1)` |
| Windows | CMD | `curl -fsSL https://raw.githubusercontent.com/ulrichando/jarvis/master/install.cmd -o install.cmd && install.cmd && del install.cmd` |

> **Note (2026-05-24):** the repo is currently private during the cross-platform refactor — the anonymous one-liners above return 404 right now. They will Just Work the moment the repo flips public. While private, authorized users can clone via `gh repo clone ulrichando/jarvis` (after `gh auth login`) or plain `git clone` with their SSH key / Git Credential Manager, then run `./install.sh` (Linux/macOS) or `.\install.ps1` (Windows) from inside the checkout. No script changes are needed for the visibility flip.

> **Windows status (Phase 3, 2026-05-24):** CLI + Desktop UI + voice-agent fully supported. The installer ships PortableGit so JARVIS's terminal/bash tool works out of the box, uses `uv` (Astral) for Python provisioning + venv, installs to `%LOCALAPPDATA%\jarvis` (proper Windows app-data), and **registers the voice services via [nssm](https://nssm.cc) (auto-downloaded, SHA256-verified)** the same way the Linux installer registers user systemd units. Voice-service registration requires running install.ps1 from an elevated PowerShell; without elevation the dep install still completes and you get a clear hint for re-running elevated. Pass `-StartServices` to launch the services immediately after install; default is "configure `.env` first, start manually". Phase 2 landed the audio/service-control/desktop-control platform-abstraction shims, and Phase 3 wired the installer. See [docs/superpowers/specs/2026-05-23-windows-install-phase1-design.md](docs/superpowers/specs/2026-05-23-windows-install-phase1-design.md) for the cross-platform refactor design.

### What the installer does

1. Install `uv` (Astral) for fast Python provisioning if missing.
2. Install Python 3.13 via `uv` if absent (no admin required).
3. Detect existing Git, or download PortableGit to `%LOCALAPPDATA%\jarvis\git` so the bash tool / terminal tool finds `bash.exe` on Windows.
4. Detect or install Node.js (portable zip preferred over winget MSI to avoid UAC).
5. Clone the repo. Linux: `~/Documents/Projects/jarvis`. Windows: `%LOCALAPPDATA%\jarvis\jarvis`. User data + memories: `~/.jarvis` on every platform.
6. Install dependencies for all four channels (CLI via Bun, Web via Bun, Desktop via npm, voice-agent via `uv pip install`).
7. Build the Tauri desktop binary (`npm run build` + `cargo build --release`).
8. **Linux:** install + enable the `jarvis-voice-agent.service` systemd unit — **not started**, so you can configure `.env` first. **Windows:** download nssm 2.24 (SHA256-verified) to `%LOCALAPPDATA%\jarvis\bin\nssm.exe` and register `jarvis-voice-agent` + `jarvis-voice-client` as Windows services (Auto-Start, restart-on-crash). Same "not started" stance — pass `-StartServices` to launch immediately, or `Start-Service jarvis-voice-agent` after editing `.env`. Requires elevated PowerShell.
9. Generate a bridge auth token + write a `.env` template at the repo root.

**Linux / macOS skip flags:** `JARVIS_SKIP_CLI=1` / `JARVIS_SKIP_VOICE=1` / `JARVIS_SKIP_DESKTOP=1` / `JARVIS_SKIP_WEB=1`. To use system `pip` instead of `uv`: `JARVIS_NO_UV=1`. Re-running the script is idempotent.

**Windows skip flags:** `-SkipCli` / `-SkipVoice` / `-SkipDesktop` / `-SkipWeb` / `-SkipCdp` / `-NoVenv` / `-SkipSetup` / `-DryRun` / `-AutoInstall` / `-StartServices` (auto-start voice services after registration; default off so you can configure `.env` first). Pin a release: `-Branch <branch>`, `-Tag <tag>`, or `-Commit <sha>` (precedence: Commit > Tag > Branch). Programmatic drivers: `-Manifest` / `-Stage <name>` / `-NonInteractive` / `-Json` (see the stage protocol section in `install.ps1`).

Want to verify your prereqs + detected install dir before committing to the 5–10 min Tauri build? Linux: `JARVIS_DRY_RUN=1`. Windows: `-DryRun`.

