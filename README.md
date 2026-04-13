# J.A.R.V.I.S.

**Just A Rather Very Intelligent System** — An autonomous AI agent that lives on your machine.

```bash
curl -fsSL https://raw.githubusercontent.com/ulrichando/jarvis/master/install.sh | bash
```

Reads your files. Runs your commands. Writes your code. Searches the web. Listens to your voice. Works with any LLM — cloud or fully local.

---

## Interfaces

| Interface | Description |
|-----------|-------------|
| **CLI** | Terminal REPL with rich rendering, voice input, and tool visualization |
| **Web** | React chat UI with holographic arc reactor sphere — access from any browser |
| **Desktop** | Transparent Tauri overlay — reactor floats on your desktop, chat slides in on demand |
| **Chrome Extension** | Browser sidebar — talk to JARVIS while you browse |

---

## What It Can Do

### Agent with real tools
```
> refactor auth.py to use JWT tokens

  Reading src/auth.py (312 lines)...
  Reading src/models/user.py...
  Writing src/auth.py
  Writing src/middleware/jwt.py
  Running pytest test/test_auth.py...

  All 24 tests pass. JWT auth is live.
```

### Deep web research
```
> deep search quantum computing breakthroughs 2026

  Round 1 — 5 queries, 8 sources
  Round 2 — 3 refined queries, 4 new sources
  Synthesizing 12 sources...

  # Quantum Computing in 2026
  ...
```

### Voice — talk to it, interrupt it
```
> v
🎤 Listening...

You: scan my network for open ports
JARVIS: Running nmap 192.168.1.0/24...

You: stop      ← interrupts mid-sentence
JARVIS: Stopped.
```

### Sees your screen
```
> what's on my screen
  Observing screen...

  VS Code, editing brain.py. Terminal below shows pytest — all 121 passing.
```

### Learns from every session
- **Skill Library** — extracts reusable skills from successful tasks
- **Reflector** — learns from failures, applies lessons next time
- **Neural Lattice** — knowledge graph that grows with use

### Extends itself
```
> create a plugin that monitors my battery level

  Creating plugin...
  Saved to ~/.jarvis/plugins/battery_monitor.py
  Plugin active. Try: "battery status"
```

---

## AI Providers

Supports any LLM. Configure in `~/.jarvis/providers.json`.

| Provider | Models | Notes |
|----------|--------|-------|
| **Anthropic** | Claude Haiku 4.5, Sonnet 4 | Best reasoning |
| **OpenAI** | GPT-4o, GPT-4o-mini | Broad capability |
| **DeepSeek** | V3, R1 | Best value |
| **xAI** | Grok-3, Grok-3-mini | Fast |
| **OpenRouter** | 100+ models | One API key for everything |
| **Ollama** | Qwen, Llama, Mistral, DeepSeek | Fully local, free |
| **Groq** | Llama 3.3, Mixtral | Ultra-fast inference |

Smart routing selects the best provider per task — code, tool use, reasoning, speed.

---

## Install

### One-liner
```bash
curl -fsSL https://raw.githubusercontent.com/ulrichando/jarvis/master/install.sh | bash
```

### Manual
```bash
git clone https://github.com/ulrichando/jarvis.git
cd jarvis
pip install -e .
jarvis
```

**Requirements:** Python 3.10+ · One AI provider (Ollama works offline for free)

---

## Usage

```bash
# CLI
jarvis                          # Interactive session
jarvis -p "summarize README.md" # One-shot
jarvis -c                       # Continue last session
jarvis -m agent                 # Start in agent mode

# Server + UI
jarvis-web                      # Web server on :8765
./scripts/start-jarvis.sh       # Full stack (server + desktop overlay)
```

### Slash Commands
```
/help           All commands
/model          Switch LLM
/review         Code review
/commit         AI git commit message
/deepsearch     Deep web research
/swarm          Parallel agents
/memory         Memory stats
/learn          Teach a fact
/doctor         Health check
```

### Keyboard
```
v               Voice input
Ctrl+H          Show/hide desktop overlay
Ctrl+C          Cancel
Ctrl+D          Exit
```

---

## Architecture

```
Input (CLI / Web / Desktop / Chrome Extension / Voice)
        ↓
    Brain (src/brain.py)
        ├─ Plugins          → instant, no LLM
        ├─ Skills           → prompt templates
        ├─ Agent Loop       → LLM + tools, iterative
        └─ Standard Chat    → direct LLM
        ↓
    Provider Router
    (Claude / GPT / DeepSeek / Ollama / Groq / xAI)
        ↓
    Response → stream to interface
```

### Core modules

| Module | Role |
|--------|------|
| `src/brain.py` | Central orchestrator |
| `src/agent/loop.py` | Iterative tool-calling loop (40 iter limit) |
| `src/agent/tools.py` | bash, read, write, edit, search, web, think, dispatch |
| `src/reasoning/providers.py` | Multi-provider routing with smart selection |
| `src/memory/` | Neural lattice · SQLite log · holographic · associative |
| `src/commands/` | 146 slash commands (9 categories) |
| `src/speech/` | Whisper STT · Edge/Groq TTS · barge-in interrupt |
| `src/desktop-tauri/` | Transparent overlay (Rust + React + Three.js) |
| `src/server/` | aiohttp HTTP + WebSocket server |

---

## Memory

JARVIS remembers across sessions:

- **Conversation Log** — SQLite, every exchange
- **Neural Lattice** — knowledge graph with spreading activation
- **Holographic Memory** — distributed vector representations
- **Associative Memory** — cross-domain pattern matching

---

## Project Layout

```
src/
  agent/          Agent loop, tools, swarm, deepsearch
  commands/       146 slash commands
  evolution/      Self-modification, skill library, reflector
  memory/         Neural lattice, holographic, associative
  reasoning/      Multi-provider LLM routing
  speech/         STT, TTS, wake word, barge-in
  vision/         Screen observation, face recognition
  cli/            Terminal interface
  server/         Web server + React frontend
  desktop-tauri/  Desktop overlay (Tauri + Three.js)
extensions/
  jarvis-screen/  Chrome extension
scripts/
  start-jarvis.sh Full stack launcher
  stop-jarvis.sh  Clean shutdown
test/             Test suite
```

---

## License

MIT · Built by [Ulrich](https://github.com/ulrichando)
