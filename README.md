# J.A.R.V.I.S.

**Just A Rather Very Intelligent System**

An autonomous AI agent that lives on your machine. Reads your files, runs your commands, writes your code, searches the web, and learns from every interaction. Works with any LLM — cloud or local.

```bash
curl -fsSL https://ulrichando.github.io/jarvis/install.sh | bash
```

---

## What JARVIS Can Do

### Talk to You
Ask anything. JARVIS uses the best available AI model — Claude, GPT-4, DeepSeek, or a local model via Ollama. Responses stream in real-time.

```
> what time is it in Tokyo
  Bash TZ=Asia/Tokyo date
  Fri Apr  3 06:30:00 JST 2026

It's 6:30 AM in Tokyo right now.
```

### Read & Write Code
JARVIS reads your files, understands your codebase, and writes complete working code. Not stubs — real code.

```
> create a REST API server with user authentication
  Write src/server.py (142 lines)
  Write src/auth.py (89 lines)
  Write src/models.py (56 lines)
  Write requirements.txt (8 lines)

Done. Run with: python src/server.py
```

### Review & Fix Code
Point JARVIS at any file or project. It reads the code, finds issues, and proposes fixes before applying them.

```
> review src/checkpoints.py
  Read src/checkpoints.py

## Issues Found:
1. Race condition in snapshot() — two edits in same microsecond collide
2. Silent exception swallowing in _load_index()
3. Redo files never cleaned up

Want me to apply these fixes?
```

### Run System Commands
JARVIS has full terminal access. Update packages, check processes, scan networks, manage services.

```
> update my system
  Bash sudo apt update && sudo apt upgrade -y
  [full apt output]

Updated 23 packages. No errors.
```

### Search the Web
Real-time web search and page fetching. JARVIS finds information and synthesizes it.

```
> what's the latest on Rust 2026 edition
  Search web Rust 2026 edition release
  Fetch https://blog.rust-lang.org/...

The Rust 2026 edition ships with...
```

### Deep Research
Multi-step research with iterative search refinement. Searches → reads sources → identifies gaps → searches deeper → synthesizes a report.

```
> deep search about quantum computing breakthroughs 2026

Round 1: 5 queries, 8 sources read
Round 2: 3 refined queries, 4 new sources
Round 3: Final synthesis

# Deep Research: Quantum Computing Breakthroughs 2026
*18 facts from 12 sources across 3 search rounds*
...
```

### Create Projects
Full multi-file project scaffolding. Chrome extensions, web apps, APIs, CLI tools.

```
> create a chrome extension that connects to localhost:8765
  Bash mkdir -p /tmp/jarvis-ext
  Write manifest.json (26 lines)
  Write popup.html (43 lines)
  Write popup.js (95 lines)
  Write background.js (103 lines)

Extension created at /tmp/jarvis-ext/. Load it in chrome://extensions.
```

### Self-Modify
JARVIS can create new capabilities for itself — plugins, skills, and tools from natural language.

```
> create a plugin that monitors my battery level
Creating new plugin: monitor battery level...
Done. File: ~/.jarvis/plugins/monitor_battery.py
Plugin loaded — try it now.
```

### Observe Your Screen
With a vision model (moondream), JARVIS can see and describe what's on your screen.

```
> what's on my screen
Looking at your screen...

You're in VS Code editing src/brain.py. The file has a function
called think_stream on line 470. There's a terminal panel open
at the bottom showing pytest output — all 121 tests passing.
```

### Voice Input
Speak to JARVIS using Whisper-based transcription.

```
> v
🎤 Listening... (speak now, 5 seconds)
You said: scan my network for open ports
  Bash nmap -sT 192.168.1.0/24
...
```

---

## Architecture

```
User Input (CLI / Web / Desktop / Chrome / Voice)
    ↓
Brain (src/brain.py)
    ├─ Plugins        → instant response (no LLM)
    ├─ Skills         → prompt templates for agent loop
    ├─ Agent Loop     → LLM + tools (read/write/bash/search/web)
    └─ Standard Chat  → LLM conversation (no tools)
    ↓
Provider Registry (Claude, OpenAI, DeepSeek, Ollama, xAI, OpenRouter, custom)
    ↓
Response → streamed to terminal / web / desktop
```

### Core Components

| Component | What it does |
|-----------|-------------|
| **Brain** (`src/brain.py`) | Central orchestrator — routes input, manages state |
| **Agent Loop** (`src/agent/loop.py`) | LLM → tool calls → execute → feed back → repeat |
| **Tools** (`src/agent/tools.py`) | bash, read_file, write_file, edit_file, search_files, web_search, web_fetch, think, dispatch |
| **Providers** (`src/reasoning/providers.py`) | Multi-model routing — Anthropic, OpenAI, DeepSeek, Ollama, xAI, OpenRouter |
| **Memory** (`src/memory/`) | Neural lattice + SQLite conversation log + holographic + associative |
| **Commands** (`src/commands/`) | 146 slash commands across 9 categories |
| **DeepSearch** (`src/agent/deepsearch.py`) | Multi-step iterative web research |
| **Swarm** (`src/agent/swarm.py`) | Parallel agent orchestration |
| **Self-Modify** (`src/evolution/self_modify.py`) | Creates plugins, skills, tools from description |
| **Skill Library** (`src/evolution/skill_library.py`) | Auto-extracts reusable skills from successful tasks |
| **Reflector** (`src/evolution/reflector.py`) | Learns from failures, injects lessons into future tasks |

### Interfaces

| Shell | Description |
|-------|-------------|
| **CLI** (`src/cli/`) | Terminal interface with JARVIS-style UX |
| **Web** (`src/server/` + `shells/web/frontend/`) | React + Tailwind frontend with Three.js holographic sphere |
| **Desktop** (`src/desktop/`) | Transparent GTK overlay with arc reactor |
| **Chrome** (`shells/chrome-extension-jarvis/`) | Browser extension with popup chat |

---

## AI Providers

JARVIS works with any LLM. Configure in `~/.jarvis/providers.json`.

| Provider | Models | Type | Pricing |
|----------|--------|------|---------|
| **Anthropic** | Claude Haiku 4.5, Sonnet 4 | Cloud | $1-15/M tokens |
| **OpenAI** | GPT-4o, GPT-4o-mini | Cloud | $2.50-10/M tokens |
| **DeepSeek** | V3, R1 (reasoner) | Cloud | $0.27-2.19/M tokens |
| **xAI** | Grok-3, Grok-3-mini | Cloud | Varies |
| **OpenRouter** | All models via one key | Cloud | Varies |
| **Ollama** | Qwen, Llama, DeepSeek, Mistral | Local | Free |
| **Custom** | Any OpenAI-compatible server | Local/Cloud | Any |

---

## Install

### One-liner

```bash
curl -fsSL https://ulrichando.github.io/jarvis/install.sh | bash
```

### Manual

```bash
git clone https://github.com/ulrichando/jarvis.git
cd jarvis
pip install .
jarvis
```

### Requirements

- Python 3.10+
- At least one AI provider (Ollama for free local, or any cloud API key)

---

## Usage

```bash
jarvis                          # Interactive session
jarvis -p "list all python files"  # One-shot query
jarvis -c                       # Continue last session
jarvis -r my-project            # Resume named session
jarvis -m agent                 # Start in agent mode
jarvis-web                      # Start web server (port 8765)
```

### Slash Commands (146)

```
/help           Show all commands
/status         Model, mode, session info
/model          Switch AI model
/review         Static code analysis
/troubleshoot   Find and fix bugs
/commit         AI-generated git commit
/deepsearch     Deep web research
/swarm          Parallel agent execution
/memory         Memory stats
/learn          Teach JARVIS a fact
/plugins        List plugins
/doctor         Check installation health
```

### Keyboard Shortcuts

```
v               Voice input
?               Show shortcuts
!command        Run shell command
!!command       Run + analyze output
/command        Slash command
Ctrl+C          Cancel operation
Ctrl+D          Exit
```

---

## Memory System

JARVIS remembers across sessions:

- **Conversation Log** — SQLite, every exchange stored
- **Neural Lattice** — Knowledge graph with spreading activation
- **Holographic Memory** — Distributed vector representations
- **Associative Memory** — Cross-domain pattern matching
- **ACT-R Activation** — Cognitive retrieval model

---

## Self-Improvement

JARVIS gets smarter over time:

- **Skill Library** — Auto-extracts reusable skills from successful tasks
- **Reflector** — Learns from failures, stores lessons for next time
- **Evolution Engine** — Analyzes usage patterns, generates optimizations
- **Reinforcement Learning** — Learns which response strategies work best
- **Curiosity Engine** — Asks questions to fill knowledge gaps

---

## Project Structure

```
src/                Python intelligence engine
  agent/            Agent loop, tools, swarm, deepsearch
  commands/         146 slash commands (9 categories)
  evolution/        Self-modification, skill library, reflector
  intelligence/     NLU, RL, curiosity, autonomous thinking
  memory/           Neural lattice, holographic, associative
  reasoning/        Multi-provider LLM routing
  speech/           Whisper STT, Piper TTS, wake word
  vision/           Screen observation, camera, recognition
  cli/              Terminal interface
  server/           aiohttp web server
  desktop/          GTK transparent overlay
shells/
  web/frontend/     React + Three.js frontend
core/               Rust gRPC server
os/                 Custom Linux OS (kernel + systemd)
test/               373 tests
```

---

## License

MIT

---

*Built by Ulrich.*
