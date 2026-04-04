# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build & Run Commands

```bash
# Install in dev mode
pip install -e .

# Run CLI
jarvis                          # or: python -m src.cli.jarvis_cli

# Run web server (port 8765)
jarvis-web                      # or: python -m src.server.web_server

# Run desktop overlay (starts server if needed, then GTK+WebKit window)
python -c "from src.desktop.app import main; main()"

# Full stack (fixes audio, starts Ollama, web server, desktop)
./scripts/start-jarvis.sh

# Frontend (React + Vite + Tailwind)
cd src/server/frontend && npm install && npm run build   # production
cd src/server/frontend && npm run dev                     # dev server

# Tests
python -m pytest test/ -q                                 # all tests
python -m pytest test/test_command_registry.py -q         # single file
python -m pytest test/test_tools.py::TestTools::test_name # single test

# Frontend lint
cd src/server/frontend && npm run lint
```

## Architecture

### Message Flow

```
User Input → CLI/Web/Desktop shell
  ↓
Brain.think() or Brain.think_stream()
  ├─ Plugins (short-circuit if matched)
  ├─ Evolved shortcuts (learned patterns)
  ├─ Slash commands → CommandRegistry.dispatch()
  ├─ Auto-matched skills (model_invocable=true)
  ├─ _needs_agent_loop() classifier
  │   ├─ YES → agent_loop() with tools (read/write/bash/search/web/dispatch)
  │   └─ NO  → _standard_response() (plain LLM query, no tools)
  ↓
Response → shell renders (markdown→ANSI for CLI, SSE/WS for web)
```

### Brain (`src/brain.py`)
Central orchestrator. Owns memory, reasoning, agent loop, screen observer, permissions, hooks, plugins, skills, MCP, evolution. All LLM interactions flow through `Brain.think()` or `Brain.think_stream()`. Modes: normal, agent, plan (read-only), berbon (autonomous), cli, mobile.

### Agent Loop (`src/agent/loop.py`)
Iterative tool-calling loop. Sends messages+tools to LLM, executes returned tool_calls, appends results, repeats until LLM returns no tool_calls or hits iteration limit (40 across parent+children). Sub-agents via `dispatch` tool: scout (read-only), worker (full access), planner (analysis).

### Command Registry (`src/commands/registry.py`)
Decorator-based: `@command(name, aliases, description, category, permission)`. 146 commands across 9 categories (core, session, memory, agent, task, mcp, plugin, git, security). Handlers in `src/commands/handlers/*.py`. Each returns `CommandResult(text, success, action, data)`.

### Provider System (`src/reasoning/providers.py`)
Multi-provider LLM backend. Configured via `~/.jarvis/providers.json`. Supports Ollama, Groq, OpenAI, Anthropic, xAI, Together, OpenRouter. Smart routing: `get_active_providers(prefer_code, prefer_tool_calling, prefer_smart)`. Prompt-based tool calling fallback for models without native function calling (parses `CALL: tool_name {"args"}` from text).

### Memory (`src/memory/`)
Three layers: SQLite conversation log (append-only WAL), Neural Lattice (knowledge graph with nodes/synapses/spreading activation), and enhanced layers (holographic, associative, ACT-R activation). `store.py` is the unified API: `add_turn()`, `recall_as_context()`, `get_history()`.

### Tools (`src/agent/tools.py`)
11 core tools: bash, read_file, write_file, edit_file, search_files, web_search, web_fetch, think, dispatch, plus dynamic MCP tool proxies. Path validation blocks sensitive paths. Bash has blocked command patterns. Output truncated to 16K (bash) / 3K (other tools).

### Hooks (`src/hooks/manager.py`)
PreToolUse/PostToolUse/Stop lifecycle hooks. Configured in `~/.jarvis/hooks.yaml` or `.jarvis/hooks.yaml`. Types: command (shell, exit code controls allow/block) or prompt (LLM evaluation).

### Shells
- **CLI** (`src/cli/jarvis_cli.py`): ANSI terminal with braille spinner, markdown rendering, tool call visualization.
- **Web** (`src/server/web_server.py`): aiohttp HTTP+WebSocket server. React frontend at `src/server/frontend/`. TTS via Edge TTS. Audio transcription via Whisper.
- **Desktop** (`src/desktop/app.py`): GTK3+WebKit2 transparent overlay. Loads React UI with `?desktop=1`. Client coordination API prevents dual reactor display.

### Extensibility
- **Plugins**: Python files in `~/.jarvis/plugins/` exporting `handle(query) → str|None`. Run before LLM.
- **Skills**: Markdown+YAML frontmatter in `~/.jarvis/skills/`. Prompt templates with `{{args}}`. Can be user-invocable (`/skillname`) or model-invocable (auto-matched).
- **MCP**: External tool servers configured in `~/.jarvis/mcp.json`. Tools dynamically added to agent loop.

## Key Configuration Paths

- `~/.jarvis/` — User home (override with `JARVIS_HOME` env)
- `~/.jarvis/providers.json` — LLM provider config
- `~/.jarvis/mcp.json` — MCP server definitions
- `~/.jarvis/hooks.yaml` — Global hooks
- `~/.jarvis/plugins/` — User plugins
- `~/.jarvis/skills/` — User skills
- `.jarvis/settings.json` — Per-project config (created by `/init`)
- `.env` — Auto-loaded env vars (API keys)

## Development Rules

- Python 3.10+ required
- Always run `python -m pytest test/ -q` after changes
- No external LLM dependency for core features — local models via Ollama as fallback
- Command handlers are async: `async def cmd_name(ctx: CommandContext) -> CommandResult`
- Tool execution goes through permissions check → hooks → checkpoint → execute → hooks
- JARVIS is both MCP client (consumes external tools) and server (exposes its capabilities)
- Desktop/browser coordination: server tracks active clients, desktop hides when browser opens
- Frontend builds to `src/server/frontend/dist/`, served as static files by aiohttp
- All source code lives in `src/` — no `brain/` folder
