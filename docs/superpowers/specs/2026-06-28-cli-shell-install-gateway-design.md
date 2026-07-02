# CLI shell-install + model gateway — design

> Sub-project **A** of the "leverage the 0wlan.com domain" decomposition.
> Brainstormed + verified 2026-06-28. Status: implementing.

## Goal

`curl -fsSL https://0wlan.com/install.sh | bash` installs the `jarvis` CLI on any
Linux/macOS box. `jarvis auth login` points it at the self-hosted deployment.
All model calls route through an authenticated **LLM gateway** fronted by
`0wlan.com` — using the **operator's** provider keys, never the client's. This is
the claude.ai-style "install anywhere and it just works" model.

## Verified context (don't re-derive)

- **The gateway already exists**: `src/cli/src/proxy/server.ts` is an
  Anthropic-native (`/v1/messages`) multi-provider router with fallback chains,
  crash-guards, request logging, a **deliberate public-bind path**
  (`JARVIS_PROXY_HOST=0.0.0.0` + `JARVIS_ALLOW_PUBLIC_BIND=1`), and **offline
  HS256 JWT auth** (`JARVIS_PROXY_AUTH_REQUIRED=1` + shared
  `JARVIS_PROXY_JWT_SECRET`). Its own comments call out "deliberate public
  deployments." Config-only — no proxy code change.
- **The JWT contract is real + mirrored**: web mints (`src/web/src/lib/bridge/proxyJwt.ts`
  + `POST /api/bridge/proxy-token`, 30-day HS256), proxy verifies offline. The
  shared secret is already set in the VPS `.env.production`.
- **`jarvis auth login` is already remote-capable**: `resolveServerRoot` reads
  `JARVIS_BRIDGE_BASE_URL`; login signs into the web app, mints the proxy token,
  persists `JARVIS_PROXY_TOKEN`, and warns on non-HTTPS roots.
- **Industry-standard convention** (confirmed via research): Claude-Code clients
  use `ANTHROPIC_BASE_URL` (where requests go — must speak `/v1/messages`) +
  `ANTHROPIC_AUTH_TOKEN` (the Bearer to the gateway, **not** `ANTHROPIC_API_KEY`).
  This is exactly how LiteLLM / TrueFoundry / agentgateway front Claude Code.

## Architecture

```
 remote box                 Cloudflare              Hetzner VPS (compose)
 jarvis (binary) ─curl|bash─► 0wlan.com  ─tunnel─► web: /install.sh + /releases
                 ─auth login─► (Access)             + POST /api/bridge/proxy-token (mint)
   ANTHROPIC_BASE_URL ──────► proxy.0wlan.com ────► gateway service (proxy/server.ts,
   + ANTHROPIC_AUTH_TOKEN     (NO Access; JWT gate)   Bun, public-bind, JWT-auth,
                                                      provider keys server-side)
```

**Enterprise properties:** single source of truth for routing (one proxy, not
duplicated into the web app); dedicated gateway tier (decoupled from web-app
uptime; gateway verifies tokens **offline** so a web blip doesn't break inference);
keys server-side only (clients hold a **revocable 30-day JWT**, never provider
keys); HS256-strict verify (alg-confusion blocked, constant-time compare);
least-privilege exposure (gateway reachable only via the tunnel, token-gated, not
behind the human email-OTP wall which would break programmatic calls).

## The gaps (new work)

1. **web** — `POST /api/bridge/proxy-token` also returns `gatewayUrl`
   (`process.env.JARVIS_GATEWAY_PUBLIC_URL`, else `null`) so the CLI learns the
   gateway endpoint. Additive, back-compatible.
2. **cli/auth** — `applyJarvisToken` / `mintProxyToken` capture `gatewayUrl` from
   the mint response and persist it to `keys.env` (`JARVIS_GATEWAY_URL`).
3. **cli/bootstrap** — the compiled binary never runs `run-cli.mjs`. Add a TS
   `bootstrapProxyEnv()` (mirrors `run-cli.mjs:76-93`) called at the top of the
   entrypoint, BEFORE the API client builds: from env→`keys.env`, set
   `ANTHROPIC_BASE_URL` (= persisted `JARVIS_GATEWAY_URL`, fallback
   `http://localhost:${JARVIS_PROXY_PORT:-4000}`), `ANTHROPIC_API_KEY=jarvis-proxy`,
   and `ANTHROPIC_AUTH_TOKEN`=`JARVIS_PROXY_TOKEN`. A parity test asserts it stays
   in lockstep with `run-cli.mjs` (like the proxyJwt mirror test).
4. **gateway deploy** — run `proxy/server.ts` as a dedicated **Bun container** in
   `src/web/docker-compose.yml` (host has no bun); env: `JARVIS_PROXY_HOST=0.0.0.0`,
   `JARVIS_ALLOW_PUBLIC_BIND=1`, `JARVIS_PROXY_AUTH_REQUIRED=1`,
   `JARVIS_PROXY_JWT_SECRET` (shared with web), provider keys. Add `proxy.0wlan.com`
   as a 2nd tunnel hostname, **Access-excluded**.
5. **installer activation** — `build-binary.sh` on Moon → publish to the VPS
   `JARVIS_RELEASES_DIR` (a volume-mounted host dir) → `JARVIS_INSTALL_BASE=https://0wlan.com`
   in the web env → Cloudflare Access **bypass** for `/install.sh` + `/releases/*`.

## Task plan (each verified before the next)

- **T1 web/gatewayUrl** — edit the route; `tsc` clean; unit-assert the field.
- **T2 cli/persist** — capture+persist `JARVIS_GATEWAY_URL`; `bun build --no-bundle`.
- **T3 cli/bootstrap** — new module + call site + parity test; `bin/jarvis -p "say OK"` returns.
- **T4 gateway container** — compose service + Dockerfile/entry; `docker compose config` valid.
- **T5 VPS deploy** — bring up the gateway; `proxy.0wlan.com` tunnel + Access exclusion.
- **T6 installer** — build+publish binary; `JARVIS_INSTALL_BASE`; Access bypass.
- **T7 e2e** — from a 2nd machine: `curl …/install.sh|bash` → `jarvis auth login` → a real turn.

## Open dependencies (operator)

- A Cloudflare API token (for `proxy.0wlan.com` Access exclusion + the
  `/install.sh`,`/releases` bypass).
- Provider keys reachable by the gateway on the VPS (ties to the keys task).
- A 2nd machine for the e2e test.
