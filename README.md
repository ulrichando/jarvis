# JARVIS

A voice-first AI assistant. Real-time speech in, real-time speech out, with direct tools for desktop / browser / multi-step coding work. Runs on Linux as a multi-process LiveKit Agents Python worker, fronted by a Tauri desktop UI, a Next.js web app, and a Claude-Code-shaped CLI.

## Install

One-shot install of all four channels (CLI + Voice Agent + Desktop + Web):

```bash
curl -fsSL https://raw.githubusercontent.com/ulrichando/jarvis/master/install.sh | bash
```

This will:

1. Clone the repo to `~/Documents/Projects/jarvis` (override with `JARVIS_INSTALL_DIR=…`).
2. Install dependencies for all four channels.
3. Build the Tauri desktop binary (`npm run build` + `cargo build --release`).
4. Install + enable systemd units (`jarvis-voice-agent.service`, `jarvis-hub.service`) — **not started**, so you can configure `.env` first.
5. Create a `.env` template at the repo root with empty API-key entries.

Skip a channel: `JARVIS_SKIP_CLI=1` / `JARVIS_SKIP_VOICE=1` / `JARVIS_SKIP_DESKTOP=1` / `JARVIS_SKIP_WEB=1`. Re-running the script is idempotent.

Want to verify your prereqs + detected install dir before committing to the 5–10 min Tauri build? Run with `JARVIS_DRY_RUN=1` — exits after the prereq check without touching anything.

### Manual install (if you don't trust curl-pipes)

```bash
git clone https://github.com/ulrichando/jarvis.git ~/Documents/Projects/jarvis
cd ~/Documents/Projects/jarvis
./install.sh
```

### Prerequisites

| Tool | Why | Install if missing |
|---|---|---|
| `git`, `curl`, `python3` (≥ 3.11) | core | system package manager |
| `bun` | CLI + Web dep install | `curl -fsSL https://bun.sh/install \| bash` |
| `node`, `npm` | Desktop frontend, web | system package manager / nvm |
| `cargo` (Rust) | Desktop backend | `curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \| sh` |
| `systemd --user` | Voice agent + hub services | already present on most Linux distros |

External services: a LiveKit SFU (the binary is bundled in the repo and `install.sh` registers a user systemd unit `livekit-server.service` + auto-generates `~/.jarvis/livekit-keys.yaml`) and Redis. Redis is system-level — `install.sh` tries `sudo systemctl enable --now redis-server.service`; if your sudo prompts for a password, you'll have to run that one line manually.

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

### 2. Start the SFU, hub, and voice agent

```bash
# Redis (system-level; one-time, if install.sh couldn't auto-enable it)
sudo systemctl enable --now redis-server.service

# LiveKit SFU (user-level, bundled binary)
systemctl --user start livekit-server.service

# JARVIS hub (talks to Redis)
systemctl --user start jarvis-hub.service

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
