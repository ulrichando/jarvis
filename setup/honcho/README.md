# Honcho — self-hosted cross-session memory (optional)

[Honcho](https://github.com/plastic-labs/honcho) is the backend behind JARVIS's
`recall` tool: it auto-syncs every message, builds a user model, and serves
semantic cross-session recall. It is the only memory-provider backend
(the inert mirror stubs were removed 2026-06-23).

**It is OPTIONAL.** JARVIS's file-backed memory (`USER.md` / `MEMORY.md` /
`PROCEDURES.md`, written via the `memory` tool and injected into the prompt)
works without honcho. Enable honcho only if you want auto-capturing, semantic
cross-session recall — and accept its cost.

## Cost / footprint

- A Docker stack: honcho **api** (`:8000`) + **deriver** + **pgvector Postgres**
  + **redis**. The Postgres/redis host ports are remapped to **5433 / 6380** so
  they don't collide with a system Postgres(5432)/redis(6379).
- **Ongoing OpenAI spend**: the deriver (`gpt-5.4-mini`) + embeddings
  (`text-embedding-3-small`) run per message. This is background (off the voice
  turn path) but it is real recurring cost.

## Install

```bash
# Standalone (idempotent):
JARVIS_REPO=/path/to/jarvis setup/honcho/setup-honcho.sh

# Or via the main installer (opt-in):
JARVIS_INSTALL_HONCHO=1 ./install.sh
```

The script: clones the honcho server (pinned to the tag matching the
`honcho-ai` client in the voice venv — server **v3.0.9** ↔ client **2.1.2**),
remaps the conflicting host ports, writes honcho's `.env` (auth off, your
`OPENAI_API_KEY` for the deriver), builds + starts the stack, waits for health,
and wires `JARVIS_MEMORY_PROVIDER=honcho` + `HONCHO_BASE_URL` + `HONCHO_API_KEY`
into `src/voice-agent/.env`. Then restart the voice agent.

Containers use `restart: unless-stopped`, so with Docker enabled on boot the
stack survives reboots.

## Verify

```bash
curl -s http://127.0.0.1:8000/health        # -> 200
# in the voice agent: the `recall` tool is armed (was inert) and
# pipeline.memory_provider.active_provider() returns HonchoMemoryProvider.
```

## Disable / pause

```bash
cd ~/honcho && docker compose down                       # stop the stack
# and remove JARVIS_MEMORY_PROVIDER=honcho from src/voice-agent/.env, then
systemctl --user restart jarvis-voice-agent.service      # recall goes inert;
                                                         # file memory unaffected
```
