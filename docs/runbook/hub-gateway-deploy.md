# Runbook — deploy the JARVIS Hub Gateway (sub-project 1)

Runs the CLI's `:4000` proxy as the VPS gateway behind **`proxy.0wlan.com`**, with
the new **OpenAI-shaped ingress** (`/v1/chat/completions`) + **`/config`**. This
is the endpoint your clients point at (`JARVIS_GATEWAY_URL`).

**Deployed shape (as of 2026-06-29):** the gateway is a **docker-compose container**
(`hub`) publishing **host `127.0.0.1:4000`**; the host's **cloudflared tunnel**
already routes `proxy.0wlan.com → http://localhost:4000` (see
`/etc/cloudflared/config.yml`). It **replaced** the older `jarvis-gateway.service`
host binary (now stopped + disabled). Caddy and `0wlan.com/hub` are **not** in the
gateway path — `proxy.0wlan.com` is a dedicated subdomain straight through the
tunnel.

> Why not `0wlan.com/hub` via Caddy? That was the original design, but the box
> already had `proxy.0wlan.com → localhost:4000` wired for the gateway, so the
> container simply takes over that loopback port. One endpoint, no Caddy route,
> and `proxy.0wlan.com` is **not** behind Cloudflare Access (no bypass needed).

**Prereqs:** SSH as root to the VPS; repo at `/opt/jarvis`; provider keys +
`JARVIS_PROXY_JWT_SECRET` in `src/web/.env.production` (verified present). Run from
`/opt/jarvis/src/web`.

---

## 1. Get the new proxy code + compose onto the box

The hub needs the new `src/cli/src/proxy/{server.ts,hubGateway.ts}`,
`src/cli/Dockerfile.hub`, `src/cli/.dockerignore`, and the `hub` service in
`src/web/docker-compose.yml`. Deliver by whichever path matches your setup:

- **git:** `cd /opt/jarvis && git fetch && git checkout origin/cli-feature-unlock -- \
  src/cli/src/proxy/server.ts src/cli/src/proxy/hubGateway.ts \
  src/cli/Dockerfile.hub src/cli/.dockerignore` then add the `hub` service to
  `src/web/docker-compose.yml` (see the committed version on that branch).
- **scp** the five files directly (what the 2026-06-29 deploy did, since the box's
  `docker-compose.yml`/`Caddyfile` are hand-modified and a `git pull` would clobber
  them). Back up first: `cp docker-compose.yml docker-compose.yml.pre-hub.bak`.

The `hub` service must publish host loopback `:4000`:
```yaml
  hub:
    build: { context: ../cli, dockerfile: Dockerfile.hub }
    restart: unless-stopped
    env_file: [{ path: .env.production, required: false }]
    environment:
      JARVIS_PROXY_HOST: "0.0.0.0"
      JARVIS_ALLOW_PUBLIC_BIND: "1"
      JARVIS_PROXY_AUTH_REQUIRED: "1"
      JARVIS_PROXY_PORT: "4000"
      JARVIS_PROVIDER: "${JARVIS_PROVIDER:-deepseek}"
    expose: ["4000"]
    ports: ["127.0.0.1:4000:4000"]   # cloudflared tunnel reaches it here
    healthcheck:
      test: ["CMD","bun","-e","fetch('http://127.0.0.1:4000/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"]
      interval: 10s
      timeout: 5s
      retries: 5
```

## 2. Cut over from the old binary gateway to the container

The old `jarvis-gateway.service` (a compiled binary) owns `:4000`. Swap it:
```bash
cd /opt/jarvis/src/web
systemctl stop jarvis-gateway          # frees host:4000 (~5-10s of proxy.0wlan.com downtime)
sleep 2
docker compose --env-file .env.production up -d --build hub
sleep 5
# GUARD — if the container isn't answering, roll back immediately:
curl -sf http://127.0.0.1:4000/health >/dev/null 2>&1 \
  && echo "container on :4000 ✓" \
  || { docker compose --env-file .env.production stop hub; systemctl start jarvis-gateway; echo "ROLLED BACK"; }
systemctl disable jarvis-gateway       # only after the container is confirmed — prevents a reboot :4000 conflict
```

## 3. Smoke `proxy.0wlan.com`

Mint a token with the real VPS secret (inside the container), then hit the tunnel:
```bash
TOKEN=$(docker exec web-hub-1 bun -e 'import {signProxyToken} from "/app/src/proxy/proxyJwt.ts"; console.log(signProxyToken({sub:"smoke",ttlSeconds:300}, process.env.JARVIS_PROXY_JWT_SECRET))')
# Local (bypasses Cloudflare — the definitive backend check):
curl -s  http://127.0.0.1:4000/config -H "Authorization: Bearer $TOKEN"        # 200 + providers{}
curl -s  http://127.0.0.1:4000/v1/chat/completions -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"model":"deepseek-v4-flash","stream":false,"messages":[{"role":"user","content":"OK"}]}'  # 200 completion
# Through the tunnel (real client path):
curl -s -o /dev/null -w '%{http_code}\n' https://proxy.0wlan.com/health                            # 200
```
Expect: local `:4000` returns 200 with a real completion; `proxy.0wlan.com/health` → 200.

> **Cloudflare bot challenge:** `proxy.0wlan.com` is open (not behind Access), but
> the zone has bot/rate protection. A burst of headless `curl`s from one IP can
> trip a "Just a moment…" challenge (HTTP 403) that a browser passes and that
> clears on its own. Don't judge liveness by hammering it with `curl` — check
> `http://127.0.0.1:4000` locally (authoritative) or `proxy.0wlan.com` from a
> browser. If real headless clients get challenged at normal volume, add a
> Cloudflare WAF/Bot-Fight skip rule for `proxy.0wlan.com` (dashboard).

## Rollback

```bash
cd /opt/jarvis/src/web
docker compose --env-file .env.production stop hub
systemctl enable --now jarvis-gateway   # old binary back on proxy.0wlan.com
# restore the compose backup if needed: cp docker-compose.yml.pre-hub.bak docker-compose.yml
```

## Security notes

- The `hub` container publishes **`127.0.0.1:4000` only** — never the public
  interface; only the host's cloudflared reaches it. Same posture as the old
  binary (loopback + tunnel + the JWT gate).
- `JARVIS_PROXY_AUTH_REQUIRED=1` + `JARVIS_ALLOW_PUBLIC_BIND=1` are set in the
  service env; every request needs a valid login JWT (`verifyProxyToken` checks
  signature + `exp` + `aud`/`iss`).
- Keys live only in the VPS `.env.production`; `.dockerignore` excludes `.env*` so
  nothing secret is baked into the image (verified: rebuilt image is `.env`-free).
- Key rotation: edit `.env.production`, then `docker compose --env-file
  .env.production up -d hub`.
