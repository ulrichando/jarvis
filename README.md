# JARVIS

A voice-first AI assistant. Real-time speech in, real-time speech out, with direct tools for desktop / browser / multi-step coding work. Runs on Linux as a multi-process LiveKit Agents Python worker, fronted by a Tauri desktop UI, a Next.js web app, and a Claude-Code-shaped CLI.

## Install

One-shot install of all four channels (CLI + Voice Agent + Desktop + Web).

### Linux / macOS (bash)

```bash
curl -fsSL https://raw.githubusercontent.com/ulrichando/jarvis/master/install.sh | bash
```

### Windows (PowerShell)

```powershell
iwr -useb https://raw.githubusercontent.com/ulrichando/jarvis/master/install.ps1 | iex
```

> **Windows status (Phase 1, 2026-05-23):** CLI + Desktop UI fully supported. The voice-agent's Python deps install cleanly, but **the voice-agent service install is deferred to Phase 2** — the agent currently imports Linux-only modules (PipeWire echo-cancel, systemd `sdnotify`, `xdotool` / X11). Phase 2 will refactor those behind platform-abstraction layers so `install.ps1` can also register + start the voice services. Until then, on Windows: use the CLI and Desktop natively, or run the voice agent under WSL2 with the Linux installer. See [docs/superpowers/specs/2026-05-23-windows-install-phase1-design.md](docs/superpowers/specs/2026-05-23-windows-install-phase1-design.md) for the full Phase-1 / Phase-2 split.

### What the installer does

1. Clone the repo to `~/Documents/Projects/jarvis` on Linux/macOS or `%USERPROFILE%\Documents\Projects\jarvis` on Windows.
2. Install dependencies for all four channels.
3. Build the Tauri desktop binary (`npm run build` + `cargo build --release`).
4. **Linux:** install + enable the `jarvis-voice-agent.service` systemd unit — **not started**, so you can configure `.env` first. **Windows:** voice-agent service registration deferred to Phase 2.
5. Create a `.env` template at the repo root with empty API-key entries.

**Linux / macOS skip flags:** `JARVIS_SKIP_CLI=1` / `JARVIS_SKIP_VOICE=1` / `JARVIS_SKIP_DESKTOP=1` / `JARVIS_SKIP_WEB=1`. Re-running the script is idempotent.

**Windows skip flags:** `-SkipCli` / `-SkipVoice` / `-SkipDesktop` / `-SkipWeb` (and `-DryRun` / `-AutoInstall`).

Want to verify your prereqs + detected install dir before committing to the 5–10 min Tauri build? Linux: `JARVIS_DRY_RUN=1`. Windows: `-DryRun`.

### Manual install (if you don't trust curl-pipes)

**Linux / macOS:**

```bash
git clone https://github.com/ulrichando/jarvis.git ~/Documents/Projects/jarvis
cd ~/Documents/Projects/jarvis
./install.sh
```

**Windows:**

```powershell
git clone https://github.com/ulrichando/jarvis.git "$env:USERPROFILE\Documents\Projects\jarvis"
Set-Location "$env:USERPROFILE\Documents\Projects\jarvis"
.\install.ps1
```

### Prerequisites

| Tool | Why | Install if missing (Linux/macOS) | Install if missing (Windows) |
|---|---|---|---|
| `git`, `curl`, `python3` (≥ 3.11) | core | system package manager | `winget install Git.Git Python.Python.3.13` |
| `bun` | CLI + Web dep install | `curl -fsSL https://bun.sh/install \| bash` | `winget install Oven-sh.Bun` (or `irm bun.sh/install.ps1 \| iex`) |
| `node`, `npm` | Desktop frontend, web | system package manager / nvm | `winget install OpenJS.NodeJS.LTS` |
| `cargo` (Rust) | Desktop backend | `curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \| sh` | `winget install Rustlang.Rustup` |
| MSVC Build Tools | Tauri 2 backend (Windows only) | — | `winget install Microsoft.VisualStudio.2022.BuildTools` |
| `systemd --user` (Linux) | Voice agent service | already present on most Linux distros | n/a (Phase 2: Task Scheduler) |

`install.ps1` will auto-install missing prereqs via `winget` if you pass `-AutoInstall`.

External services: a LiveKit SFU (the Linux binary is bundled in the repo and `install.sh` registers a user systemd unit + auto-generates `~/.jarvis/livekit-keys.yaml`). On Windows, grab `livekit-server.exe` from the LiveKit GitHub releases page, or use the Linux binary under WSL2.

## After install

### 1. Configure API keys

Edit `~/Documents/Projects/jarvis/.env` and fill in real values:

```bash
GROQ_API_KEY=...
DEEPSEEK_API_KEY=...
GOOGLE_API_KEY=...
OPENAI_API_KEY=...
ANTHROPIC_API_KEY=...
KIMI_API_KEY=...
```

All four subprojects read this file (consolidated 2026-05-15 — no more duplicate keys across subproject `.env.local` files). Subproject-specific vars (`LIVEKIT_*`, `NEXT_PUBLIC_*`, `DATABASE_URL`) stay in their respective `src/<sub>/.env.local`. The Tauri tray UI writes user overrides to `~/.jarvis/keys.env`, which always wins.

### 2. Start the SFU and voice agent

```bash
# LiveKit SFU (user-level, bundled binary)
systemctl --user start livekit-server.service

# Voice agent — the brain. Tail logs to confirm it connected.
systemctl --user start jarvis-voice-agent.service
journalctl --user -u jarvis-voice-agent.service -f
```

### 3. Use a channel

| Channel | Command |
|---|---|
| **CLI** | `jarvis` (or `jarvis groq` / `jarvis deepseek` for a specific provider) |
| **Desktop (Tauri)** | `jarvis-desktop`, or run the binary directly: `~/Documents/Projects/jarvis/src/desktop-tauri/src-tauri/target/release/jarvis` |
| **Web (Next.js)** | `cd ~/Documents/Projects/jarvis/src/web && bun dev` (defaults to `http://localhost:3000`) |

## Architecture (one paragraph)

The brain is a Python LiveKit Agents worker (`src/voice-agent/jarvis_agent.py`). It runs the supervisor LLM (Anthropic Claude Sonnet 4.6 by default, tray-switchable across Groq / DeepSeek / OpenAI / Anthropic / Kimi) plus a pipeline of sanitizers, monkey-patches, and a turn router that picks an LLM and TTS based on intent class. Desktop control (`computer_use`), browsing (`browser_task`), and multi-step work run as direct tools the supervisor calls itself. The Tauri desktop UI gives you a tray icon, model picker, and barge-in mic. The Next.js app is a web dashboard / chat front-end. The Claude-Code-shaped CLI is a separate engineering agent that routes through the same Anthropic-shaped proxy.

For load-bearing operational rules and architecture details, see [CLAUDE.md](CLAUDE.md).

## License

MIT — see [LICENSE](src/voice-agent/LICENSE) (the repo-wide license; the voice-agent dir hosts the canonical copy).
