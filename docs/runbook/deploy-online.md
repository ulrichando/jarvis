# Deploy the web app online (Cloudflare in front)

> ⚠️ **The web app controls a machine.** It exposes `/api/workspace/[id]/exec`
> (arbitrary shell) + remote CLI control. Deployed to a server it controls
> **that server**, not your laptop. Treat it as a **single-user, locked-down**
> instance behind layered auth — never a multi-user public product without a
> real productization pass (strip/rework the local-control + RCE routes first).
> If what you actually want is "reach my laptop's JARVIS remotely", a server
> deploy does NOT do that — that's a tunnel.

## 0. Host
Cloudflare **Pages/Workers can't run this** (it needs a long-running Node
process + Postgres + the `/code` pty-server + containers — none of which fit
serverless/edge). You need a **VPS/server** (Hetzner / DO / Fly machine / your
own box). Cloudflare is only the DNS + SSL + Access + WAF layer in front of it.

## 1. Rotate the leaked credentials FIRST  (blocker #2)
Old real keys live in **git history** (pre-2026-06-11 revisions). Current files
are clean, but the *values* are still recoverable from history, and per the
2026-06-11 review they were never rotated. Rotating makes the leaked values
dead — do this before anything is reachable:

Run **`docs/runbook/credential-rotation.md`** — rotate at each provider
dashboard (Groq, DeepSeek, Google) + the Postgres password (its §6).
Then (optional cleanup, only with a clean tree / no concurrent agent sessions):
`docs/runbook/git-history-scrub.md`.

## 2. Production env  (blocker #3 — the RCE gate)
On the server, in `src/web/.env.local` (or the process env):

```
NODE_ENV=production
JARVIS_REQUIRE_LOCAL_AUTH=1                 # turns ON the /api/* bearer gate
JARVIS_LOCAL_API_TOKEN=<openssl rand -hex 32>   # the bearer (same value the bridge uses)
JARVIS_WEB_ALLOWED_HOSTS=jarvis.yourdomain.com  # REQUIRED — else every /api/* 403s (host allowlist)
BETTER_AUTH_URL=https://jarvis.yourdomain.com   # login cookies + canonical host
JARVIS_CANONICAL_HOST=jarvis.yourdomain.com
DATABASE_URL=postgresql://jarvis:<rotated-pw>@localhost:5432/jarvis
# + the rotated provider keys (GROQ/DEEPSEEK/OPENAI/GOOGLE/KIMI/ANTHROPIC), from keys.env
```

`JARVIS_AUTH_DISABLED` must be **unset** (it bypasses the login gate — dev only).

## 3. Build + run (containerized — the enterprise path)
The stack ships as compose: **caddy** (origin TLS) → **web** (Next + pty-server,
non-root) → **docker-proxy** (restricted Docker API) + **postgres**.
```
cd src/web
cp .env.production.example .env.production   # fill it in (rotated secrets!)
# Drop the origin cert/key into ./certs (Cloudflare Origin Certificate, or a
# self-signed pair for a no-CF/local bring-up). The caddy service mounts it.
mkdir -p certs   # → certs/origin.crt, certs/origin.key
# --env-file is REQUIRED: compose interpolates ${POSTGRES_PASSWORD},
# ${NEXT_PUBLIC_PTY_URL}, ${JARVIS_WORKSPACES_ROOT}, etc. from its dotenv file,
# which defaults to .env (NOT .env.production). Without this they'd be empty and
# the postgres ${POSTGRES_PASSWORD:?} guard aborts the up.
docker compose --env-file .env.production build
docker compose --env-file .env.production up -d
docker compose --env-file .env.production exec web npm run db:migrate
```
- The web app talks to Docker **only** through `tecnativa/docker-socket-proxy`
  (container/exec/network ops; no host-level reach) — never the raw socket.
- Sandbox containers run on the host daemon, so `JARVIS_WORKSPACES_ROOT` must be
  a **host path** mounted at the *same* path in `web` (compose does this).

### 3a. Sandbox egress firewall (do this on the host)
The hardened sandboxes (cap-drop ALL, no-new-privs, isolated `jarvis-sandbox`
bridge, opt-in read-only/gVisor — `scripts/lib/docker.mjs`) still have internet
egress by default. Block cloud-metadata + the host from them via DOCKER-USER:
```bash
# Block the sandbox bridge from the cloud metadata endpoint + host/RFC1918.
SUBNET=$(docker network inspect jarvis-sandbox -f '{{(index .IPAM.Config 0).Subnet}}')
sudo iptables -I DOCKER-USER -s "$SUBNET" -d 169.254.169.254 -j DROP
sudo iptables -I DOCKER-USER -s "$SUBNET" -d 10.0.0.0/8     -j DROP
sudo iptables -I DOCKER-USER -s "$SUBNET" -d 172.16.0.0/12  -j DROP
sudo iptables -I DOCKER-USER -s "$SUBNET" -d 192.168.0.0/16 -j DROP
# Persist with iptables-persistent. For ZERO egress instead: JARVIS_SANDBOX_NETWORK=none.
# For kernel-level isolation of untrusted code: install gVisor + JARVIS_SANDBOX_RUNTIME=runsc.
```

## 4. Cloudflare (the part you asked about)
Your domain is already on Cloudflare, so:

1. **DNS** → add a record: `A  jarvis  <your server IP>`, **Proxied** (orange
   cloud ON). That alone connects the domain + gives you Cloudflare's SSL/CDN/WAF.
2. **SSL/TLS → Overview** → set mode **Full (strict)** (get a real cert on the
   server — Cloudflare Origin Certificate or Let's Encrypt).
3. **Zero Trust → Access → Applications → Add** → self-hosted, hostname
   `jarvis.yourdomain.com` → **policy: Allow, your email only** (or your IdP).
   *This is non-negotiable here* — it authenticates **before** a request ever
   reaches the RCE surface. The app's own login is your second layer.
4. **Security → WAF** → leave managed rules on; consider rate-limiting `/api/*`.
5. (Optional, stronger) **Cloudflare Tunnel** instead of opening a port: keeps
   the server's origin private. You said no tunnel — so just make sure the
   server firewall only allows Cloudflare's IP ranges to reach :443.

## 5. Verify
- `https://jarvis.yourdomain.com` → Cloudflare Access prompt → your email → JARVIS login → app.
- An unauthenticated `curl https://jarvis.yourdomain.com/api/health` from outside → blocked by Access.
- A request with a wrong/absent bearer to a real `/api/*` route → `401 auth required`.
- A request with a forged Host → `403 host not allowed`.

## 6. Continuous deploy — push to master, the box self-updates

Live since 2026-07-02. A systemd timer on the VPS polls `origin/master` every
5 minutes and, when it moves: ff-only pull → `docker compose build` + `up -d`
(only when `src/web` or `src/cli` changed; Caddyfile changes also restart caddy)
→ health gate (hub `/health` + web HTTP + container states) → **automatic
rollback** to the previous SHA on failure. The script lives in the repo
([scripts/vps/deploy-poll.sh](../../scripts/vps/deploy-poll.sh)) so it
self-updates with master; the units are one-time copies.

Install (once, as root on the box):
```bash
cp /opt/jarvis/scripts/vps/jarvis-deploy-poll.{service,timer} /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now jarvis-deploy-poll.timer
# optional off-box alerts:
echo 'JARVIS_DEPLOY_NOTIFY_URL=https://ntfy.sh/<topic>' > /etc/jarvis-deploy.env
```

Rules the script enforces:
- **ff-only** — a dirty/diverged `/opt/jarvis` refuses to deploy (loud failure,
  no clobber). Box-local tweaks belong in `src/web/docker-compose.override.yml`
  (gitignored, compose auto-merges it) or `.env.production` — never in tracked
  files on the box.
- **Migrations are MANUAL** — a push touching `src/web/drizzle/` skips the
  deploy + alerts. Apply the schema per §3, then clear the latch:
  `rm -f /var/lib/jarvis-deploy/failed-sha`.
- **Failed-SHA latch** — a failed deploy is not retried every 5 minutes; it
  waits for a new push to move master (or a manual latch clear).

Ops: log at `/var/log/jarvis-deploy.log` · pause:
`systemctl disable --now jarvis-deploy-poll.timer` · manual rollback:
`git -C /opt/jarvis reset --hard <sha>` then rebuild per §3.

## The pty terminal — now authenticated at the socket
The `/code` terminal (`scripts/pty-server.mjs`, port 8772) is a raw PTY shell.
It is fronted by Caddy `/pty` (TLS + Cloudflare Access + app login) and bound to
0.0.0.0 only inside the container (never published to the host) — **and** it now
requires a per-session **HS256 token** in its `init` frame:

- The browser mints one from `POST /api/workspace/[id]/pty-token` (behind the
  `/api/*` gate) right before each (re)connect; the token is scoped to that
  workspace + a 10-min TTL and signed with `JARVIS_PROXY_JWT_SECRET`.
- The sidecar verifies it OFFLINE (`scripts/lib/pty-auth.mjs`) before spawning a
  shell. Enforcement auto-engages whenever the socket is bound off-loopback, and
  is set explicitly via `JARVIS_PTY_REQUIRE_AUTH=1` in compose. It **fails
  closed**: if the signing secret is absent, every connection is rejected.

So Access + app login + the socket token are three independent layers. Set
`JARVIS_PROXY_JWT_SECRET` (a stable `openssl rand -base64 32`) in the prod env so
it survives container recreates, and `NEXT_PUBLIC_PTY_URL=wss://<domain>/pty` at
**build** time so the browser uses the routed path, not the raw port.

## Residual risk (accept it explicitly)
Even done right, this publishes an arbitrary-code-execution surface. Access +
the bearer + login are strong, but the blast radius if any layer fails is the
whole server (and on a box with passwordless sudo, root). Don't reuse a server
that holds anything else, and don't run it on a machine you care about.
