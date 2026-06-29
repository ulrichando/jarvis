# Jarvis CLI

A Claude-Code-shaped AI coding assistant (TypeScript / Bun). It runs **locally**
and talks to your self-hosted **JARVIS gateway** for LLM access, with
multi-provider support: DeepSeek, Anthropic, OpenAI, Gemini, Kimi, and Ollama.

It's a *client* of the JARVIS deployment — one `jarvis auth login` signs this
machine in to your gateway (e.g. `proxy.0wlan.com`), and the gateway holds the
provider keys. See the [root README](../../README.md) for the whole system.

## Install

### Prebuilt binary (recommended)

```
curl -fsSL https://0wlan.com/install.sh | bash
jarvis auth login
```

Drops the `jarvis` binary into `~/.local/bin`; `jarvis auth login` opens your
gateway in the browser to sign in. No clone, no Bun, no build.

**Uninstall:** `jarvis uninstall` (or `curl -fsSL https://0wlan.com/uninstall.sh | bash`).

### From source (development)

```
git clone https://github.com/ulrichando/jarvis.git   # private — clone via gh / SSH
cd jarvis
bin/jarvis                                            # source-run launcher (Bun)
```

`bin/jarvis` is the launcher (`scripts/start.sh` → `bun … cli.tsx`); it starts a
local proxy and the agent. Per-provider dev shells:
`npm run dev:deepseek` / `dev:gemini` / `dev:openai` / `dev:ollama`.

## Usage

```
jarvis                              # interactive session
jarvis -p "fix the failing test"   # headless one-shot (prints, then exits)
jarvis auth login                  # sign in to your gateway
jarvis auth logout                 # sign out (clears the local token only)
```

In a session: `/model` switches the active model, `/help` lists commands. Your
default model is persisted to `~/.jarvis/cli-model`.

## Providers

DeepSeek · Anthropic · OpenAI · Gemini (Google) · Kimi (Moonshot) · Ollama (local).
The model registry is `src/utils/model/jarvisModelRegistry.ts`. Provider keys
live on your **gateway** — a logged-in client never holds them. For a standalone
/ from-source run without a gateway, set keys in `~/.jarvis/keys.env`:

```
DEEPSEEK_API_KEY=…
OPENAI_API_KEY=…
ANTHROPIC_API_KEY=…
GEMINI_API_KEY=…
```

## License

Apache-2.0 — see [LICENSE](LICENSE).
