# misty-core

Standalone AI-native OS-brain service for **Misty Scone** (spec lives outside the repo at `~/.claude/plans/i-want-to-build-misty-scone.md`; see [docs/superpowers/plans/](../../docs/superpowers/plans/) for the decomposed per-plan implementation docs).

## What it does (Plans 2-5)

- Starts a local HTTP server on `$MISTY_PORT` (default 8765).
- Accepts `POST /api/think` with `{messages}`, runs a Groq-backed agent loop with tools:
  - **bash** — execute shell commands. Low-risk runs auto; high-risk (sudo, rm -rf, offensive network tools, reverse shells, port scans) auto-denies unless an approval callback is attached.
  - **hyprland** — Hyprland window-manager control via its IPC socket (focus/spawn/move_to_workspace/list_windows/dispatch). Requires Hyprland running (VM only).
  - **screen** — capture the focused monitor via `grim` and describe it via a vision-capable provider (default: Gemini 2.0 Flash). Requires `GEMINI_API_KEY` and `grim` binary (Wayland only).
- Additional endpoints:
  - `POST /api/speak { text, voice? }` — Groq Orpheus TTS, returns WAV/MP3/etc bytes.
  - `POST /api/transcribe` — multipart audio upload → `{ text }` via Groq Whisper.
  - `POST /api/think?interactive=1` — high-risk tool calls pause via the confirmation queue instead of auto-denying.
  - `POST /api/confirmation/:id { decision }` — resolve a pending confirmation.
  - `GET /api/confirmation` — list pending confirmations.
- The [`hud/`](hud/) subtree contains an eww GTK layer-shell widget that polls `/api/confirmation`, shows pending high-risk requests, and provides Accept/Deny buttons — see [hud/README.md](hud/README.md).

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
bridge/      HTTP routes (/health, /api/models, /api/think, /api/speak, /api/transcribe, /api/confirmation)
providers/   LLM clients: Groq (text), Gemini (vision); OpenAI/Ollama/DeepSeek stubbed
agent/       Agent loop + tool registry (loop accepts optional confirm callback)
  tools/     bash, hyprland, screen
hyprland/    UNIX-socket IPC client + high-level actions
screen/      grim-based capture helper
voice/       TTS (Groq Orpheus), STT (Groq Whisper), confirmation queue
risk/        Risk-tier classifier + async gate
config/      Env loading, typed Config
test/        bun:test suite
hud/         eww HUD widget (yuck + scss) + fetch/confirm scripts + install helper
scripts/     Plan 1 VM provisioning (bash; unrelated to the Bun daemon)
docs/        Plan 1 runbook + packages.md
```

## What's next

Plans 6-7 add an audio client (hotkey + mic capture + TTS playback), a wake-word daemon, and a proactive controller that watches the screen and suggests actions. See the per-plan implementation docs at [docs/superpowers/plans/](../../docs/superpowers/plans/).
