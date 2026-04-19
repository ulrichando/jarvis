# misty-core

Standalone AI-native OS-brain service for **Misty Scone** (spec lives outside the repo at `~/.claude/plans/i-want-to-build-misty-scone.md`; see [docs/superpowers/plans/](../../docs/superpowers/plans/) for the decomposed per-plan implementation docs).

## What it does (Plans 2-3)

- Starts a local HTTP server on `$MISTY_PORT` (default 8765).
- Accepts `POST /api/think` with `{messages}`, runs a Groq-backed agent loop with tools:
  - **bash** — execute shell commands. Low-risk runs auto; high-risk (sudo, rm -rf, offensive network tools, reverse shells, port scans) auto-denies with an informative error.
  - **hyprland** — Hyprland window-manager control via its IPC socket (focus/spawn/move_to_workspace/list_windows/dispatch). Requires Hyprland running (VM only).
  - **screen** — capture the focused monitor via `grim` and describe it via a vision-capable provider (default: Gemini 2.0 Flash). Requires `GEMINI_API_KEY` and `grim` binary (Wayland only).
- Plan 4+ adds voice/HUD approval so high-risk bash can be confirmed instead of auto-denied.

## Running

```bash
cd src/os/desktop
cp .env.example .env
$EDITOR .env                     # set GROQ_API_KEY
bun install
bun run start                    # or: bun run dev (auto-restart on change)
```

Sanity check:

```bash
curl -sS http://127.0.0.1:8765/health
curl -sS http://127.0.0.1:8765/api/models
curl -sS -X POST http://127.0.0.1:8765/api/think \
  -H 'content-type: application/json' \
  -d '{"messages":[{"role":"user","content":"show me what'"'"'s in /tmp"}]}'
```

## Running in the provisioned VM

See [docs/01-vm-baseline.md](docs/01-vm-baseline.md) for the VM provisioning workflow (Plan 1). Once the VM is up and this repo is cloned inside it:

```bash
cd ~/jarvis/src/os/desktop
cp .env.example .env && $EDITOR .env
bun install
bun run start
```

A later plan (3+) will package this as a systemd user service (`misty-core.service`).

## Development

- `bun test` — run the full test suite (no network required; real Groq calls are not exercised in tests).
- `bun run typecheck` — strict TypeScript check.

Code layout:

```
bridge/      HTTP routes (/health, /api/models, /api/think)
providers/   LLM clients: Groq (text), Gemini (vision); OpenAI/Ollama/DeepSeek stubbed
agent/       Agent loop + tool registry
  tools/     bash, hyprland, screen
hyprland/    UNIX-socket IPC client + high-level actions
screen/      grim-based capture helper
risk/        Risk-tier classifier and gate
config/      Env loading, typed Config
test/        bun:test suite
scripts/     Plan 1 VM provisioning (bash; unrelated to the Bun daemon)
docs/        Plan 1 runbook + packages.md
```

## What's next

Plans 4-7 add voice (STT + TTS + wake word + mode switcher), the proactive controller, a HUD widget, and a voice-driven approval flow that unblocks high-risk tool calls. See the per-plan implementation docs at [docs/superpowers/plans/](../../docs/superpowers/plans/).
