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

## Use JARVIS in your IDE (ACP)

JARVIS implements the [Agent Client Protocol](https://github.com/zed-industries/agent-client-protocol), so any ACP-compatible IDE (Zed today; Cursor / VS Code / JetBrains as their ACP clients ship) can drive JARVIS as a coding agent — in addition to voice + CLI + Desktop. The ACP adapter shares its tool registry, memory, and skills with the rest of the system.

### Zed setup

Edit `~/.config/zed/settings.json` (Linux / macOS) or `%APPDATA%\Zed\settings.json` (Windows):

```json
{
  "agent_servers": {
    "JARVIS": {
      "command": "<INSTALL_DIR>/bin/jarvis-acp",
      "args": []
    }
  }
}
```

Replace `<INSTALL_DIR>` with the absolute path to your JARVIS checkout (Linux default: `~/Documents/Projects/jarvis`). Then open Zed's chat pane and pick "JARVIS" from the agent picker.

### What you get

- Streaming assistant replies in the IDE chat pane.
- File edits (`write_file`, `patch`) round-trip through Zed's edit-approval UI — you see the diff and click approve/deny before any write lands.
- Terminal commands gate through the IDE's permission dialog so nothing runs without your OK.
- The same supervisor LLM + tools the voice agent uses (Anthropic-primary with Groq + DeepSeek fallback), so memory you add via voice is visible in the IDE and vice-versa.

Skip approvals entirely (headless soak tests, power-user sessions) with `JARVIS_ACP_PERMISSIONS=permissive` in the environment Zed spawns the adapter under.

### Discovery manifest

JARVIS ships [`src/voice-agent/acp_registry/agent.json`](src/voice-agent/acp_registry/agent.json) — IDEs that auto-discover ACP agents (Zed's registry feature, etc.) read this file.

## Prerequisites

- **Linux with X11** — the Tauri desktop UI and the `computer_use` tool (screen-reading + GUI automation) require X11. Wayland is not supported.
- **Audio stack** — a working ALSA/PulseAudio/PipeWire setup with a microphone. The voice agent uses Silero VAD + Deepgram Nova-3 (streaming) / Groq Whisper (fallback) for STT and Groq Orpheus for TTS.
- **API keys** — at minimum one LLM provider key (Anthropic recommended for lowest latency via prompt caching) + a LiveKit server URL/key/secret (local `livekit-server` works). `DEEPGRAM_API_KEY` is strongly recommended for STT-confirmed barge-in; without it the system degrades to Whisper-only (no interim transcripts). Full env-var reference: [`docs/env-reference.md`](docs/env-reference.md).

## Architecture

JARVIS is a multi-process system: a Python LiveKit Agents worker (`src/voice-agent/`) runs the supervisor LLM (Anthropic Sonnet 4.6, with Groq/DeepSeek/OpenAI fallbacks), a VAD/STT/TTS pipeline, and a self-registering tool registry (computer use, browser control, terminal, file/code tools, memory, web search, and more). The Tauri desktop (`src/desktop-tauri/`) and Next.js web app (`src/web/`) connect to the voice agent over LiveKit. The TypeScript/Bun CLI (`src/cli/`) is a separate Claude-Code-shaped coding agent. A local bridge (`127.0.0.1:8765`) brokers requests between the desktop and a Chrome extension. See [`ARCHITECTURE.md`](ARCHITECTURE.md) for the full diagram and data-flow description.

## Repository layout

| Path | What it is |
|---|---|
| `src/voice-agent/` | Python LiveKit Agents brain — supervisor LLM, VAD/STT/TTS pipeline, tool registry, memory, sanitizers, resilience |
| `src/desktop-tauri/` | Tauri (Rust + React/TS) desktop UI — system tray, voice controls, Blender face animator |
| `src/web/` | Next.js / React web app — 3-column chat UI, workbench, ACP interface |
| `src/cli/` | TypeScript/Bun CLI agent — Claude-Code-shaped, multi-provider, bridge server |
| `src/android/` | Kotlin/Compose + NDK on-device Android app |
| `bin/` | Launchers, soak scripts, auto-mod tooling |
| `docs/` | Design specs, runbooks, env reference |

## License

Apache-2.0 — see [LICENSE](LICENSE).

