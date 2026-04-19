# misty-core

Standalone AI-native OS-brain service for **Misty Scone** (spec lives outside the repo at `~/.claude/plans/i-want-to-build-misty-scone.md`; see [docs/superpowers/plans/](../../docs/superpowers/plans/) for the decomposed per-plan implementation docs).

## What it does (Plan 2 scope)

- Starts a local HTTP server on `$MISTY_PORT` (default 8765).
- Accepts `POST /api/think` with `{messages}`, runs a Groq-backed agent loop with one tool (`bash`), returns the transcript.
- Low-risk bash runs automatically; high-risk bash (sudo, rm -rf, offensive network tools, reverse shells, port scans, etc.) is auto-denied with an informative error. Plan 3+ adds voice/HUD approval so high-risk can be confirmed.

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
providers/   LLM clients (Groq via Anthropic SDK; OpenAI/Gemini/DeepSeek stubbed)
agent/       Agent loop + tool registry
  tools/     Tool implementations (bash)
risk/        Risk-tier classifier and gate
config/      Env loading, typed Config
test/        bun:test suite
scripts/     Plan 1 VM provisioning (bash; unrelated to the Bun daemon)
docs/        Plan 1 runbook + packages.md
```

## What's next

Plans 3-7 add Hyprland integration, a Linux screen observer, voice (STT + TTS + wake word + mode switcher), the proactive controller, a HUD widget, and a voice-driven approval flow that unblocks high-risk tool calls. See the per-plan implementation docs at [docs/superpowers/plans/](../../docs/superpowers/plans/).
