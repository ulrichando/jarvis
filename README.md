# JARVIS

A voice-first AI assistant. Real-time speech in, real-time speech out, with direct tools for desktop / browser / multi-step coding work. Runs on Linux as a multi-process LiveKit Agents Python worker, fronted by a Tauri desktop UI, a Next.js web app, and a Claude-Code-shaped CLI.

## Install

> **JARVIS lives in a private repo.** You need GitHub access to clone it. The install patterns below assume you have it. Anonymous `curl … | bash` one-liners (the kind public repos can ship) are not possible because raw.githubusercontent.com returns 404 to unauthenticated requests on private repos.

### Option A — recommended: GitHub CLI (`gh`)

Cross-platform, secure, no token in your shell history. One-time setup:

```bash
# Install + authenticate gh (do this once per machine)
brew install gh                 # macOS
sudo apt install gh             # Debian / Ubuntu / Kali
winget install GitHub.cli       # Windows
gh auth login                   # log in via browser
```

Then install JARVIS:

| Platform | Shell | Command |
|---|---|---|
| Linux / macOS | bash | `gh repo clone ulrichando/jarvis ~/Documents/Projects/jarvis && cd ~/Documents/Projects/jarvis && ./install.sh` |
| Windows | PowerShell | `gh repo clone ulrichando/jarvis "$env:LOCALAPPDATA\jarvis\jarvis"; & "$env:LOCALAPPDATA\jarvis\jarvis\install.ps1"` |
| Windows | CMD | `gh repo clone ulrichando/jarvis "%LOCALAPPDATA%\jarvis\jarvis" && "%LOCALAPPDATA%\jarvis\jarvis\install.cmd"` |

### Option B — Personal Access Token (PAT)

If you can't use `gh` CLI: create a fine-grained PAT at https://github.com/settings/tokens/?type=beta with **Contents: Read** scope on `ulrichando/jarvis`, then:

```bash
# Linux / macOS — token from env
export GH_TOKEN=ghp_yourtokenhere
curl -fsSL -H "Authorization: token $GH_TOKEN" \
  https://raw.githubusercontent.com/ulrichando/jarvis/master/install.sh | \
  GH_TOKEN=$GH_TOKEN bash
```

```powershell
# Windows PowerShell — token from env
$env:GH_TOKEN = "ghp_yourtokenhere"
$Headers = @{ Authorization = "token $env:GH_TOKEN" }
(Invoke-WebRequest -UseBasicParsing -Headers $Headers `
  -Uri "https://raw.githubusercontent.com/ulrichando/jarvis/master/install.ps1").Content | Invoke-Expression
```

Both install scripts (`install.sh` / `install.ps1`) honor `GH_TOKEN` (also accepted as `GITHUB_TOKEN`) and pass it to the `git clone` step, so the private repo clones with one token export.

### Option C — fully manual

```bash
# Linux / macOS
git clone https://github.com/ulrichando/jarvis.git ~/Documents/Projects/jarvis
cd ~/Documents/Projects/jarvis
./install.sh
```

```powershell
# Windows PowerShell
git clone https://github.com/ulrichando/jarvis.git "$env:LOCALAPPDATA\jarvis\jarvis"
Set-Location "$env:LOCALAPPDATA\jarvis\jarvis"
.\install.ps1
```

Your existing `git` credentials (SSH key, OS keychain, Git Credential Manager) handle the auth. This is the most-explicit option — no token plumbing.

> **Windows status (Phase 1, 2026-05-24):** CLI + Desktop UI fully supported. The installer ships PortableGit so JARVIS's terminal/bash tool works out of the box, uses `uv` (Astral) for Python provisioning + venv, and installs to `%LOCALAPPDATA%\jarvis` (proper Windows app-data). The voice-agent's Python deps install cleanly, but **the voice-agent service install is deferred to Phase 2** — the agent currently imports Linux-only modules (PipeWire echo-cancel, systemd `sdnotify`, `xdotool` / X11). Phase 2 will refactor those behind platform-abstraction layers so `install.ps1` can also register + start the voice services. Until then, on Windows: use the CLI and Desktop natively, or run the voice agent under WSL2 with the Linux installer. See [docs/superpowers/specs/2026-05-23-windows-install-phase1-design.md](docs/superpowers/specs/2026-05-23-windows-install-phase1-design.md) for the Phase 1 / Phase 2 split and the pattern-adoption notes.

### What the installer does

1. Install `uv` (Astral) for fast Python provisioning if missing.
2. Install Python 3.13 via `uv` if absent (no admin required).
3. Detect existing Git, or download PortableGit to `%LOCALAPPDATA%\jarvis\git` so the bash tool / terminal tool finds `bash.exe` on Windows.
4. Detect or install Node.js (portable zip preferred over winget MSI to avoid UAC).
5. Clone the repo. Linux: `~/Documents/Projects/jarvis`. Windows: `%LOCALAPPDATA%\jarvis\jarvis`. User data + memories: `~/.jarvis` on every platform.
6. Install dependencies for all four channels (CLI via Bun, Web via Bun, Desktop via npm, voice-agent via `uv pip install`).
7. Build the Tauri desktop binary (`npm run build` + `cargo build --release`).
8. **Linux:** install + enable the `jarvis-voice-agent.service` systemd unit — **not started**, so you can configure `.env` first. **Windows:** voice-agent service registration deferred to Phase 2.
9. Generate a bridge auth token + write a `.env` template at the repo root.

**Linux / macOS skip flags:** `JARVIS_SKIP_CLI=1` / `JARVIS_SKIP_VOICE=1` / `JARVIS_SKIP_DESKTOP=1` / `JARVIS_SKIP_WEB=1`. To use system `pip` instead of `uv`: `JARVIS_NO_UV=1`. Re-running the script is idempotent.

**Windows skip flags:** `-SkipCli` / `-SkipVoice` / `-SkipDesktop` / `-SkipWeb` / `-SkipCdp` / `-NoVenv` / `-SkipSetup` / `-DryRun` / `-AutoInstall`. Pin a release: `-Branch <branch>`, `-Tag <tag>`, or `-Commit <sha>` (precedence: Commit > Tag > Branch). Programmatic drivers: `-Manifest` / `-Stage <name>` / `-NonInteractive` / `-Json` (see the stage protocol section in `install.ps1`).

Want to verify your prereqs + detected install dir before committing to the 5–10 min Tauri build? Linux: `JARVIS_DRY_RUN=1`. Windows: `-DryRun`.

