# Runbook — deploy the JARVIS Hub Gateway (sub-project 1)

Stands up the CLI's `:4000` proxy as the VPS **Hub Gateway** at
`https://0wlan.com/hub`. After this, the gateway is live but **no client points
at it yet** (sub-projects 2–5 do that). Provider keys already live on the VPS;
this changes nothing local.

**Prereqs:** the code is merged/present on the VPS checkout (commits `93bb7821`
OpenAI ingress, `e2262821` `/config`, `3443c30a` `Dockerfile.hub`, `<task4>` the
compose/Caddy wiring). Run everything from `src/web` on the VPS host.

---

## 1. Drop the stale Groq key (cleanup)

The Groq eradication left a dead `GROQ_API_KEY=` in `.env.production` — nothing
reads it. Remove the line:

```bash
sed -i '/^GROQ_API_KEY=/d' src/web/.env.production
```

## 2. Confirm the VPS env has what the hub needs

```bash
grep -oE '^(JARVIS_PROXY_JWT_SECRET|[A-Z]+_API_KEY)' src/web/.env.production | sort -u
```
Expect `JARVIS_PROXY_JWT_SECRET` plus `ANTHROPIC_/DEEPSEEK_/GOOGLE_/KIMI_/OPENAI_API_KEY`.
No action if present (verified locally 2026-06-29) — the hub container inherits
them via `env_file: .env.production`.

## 3. Cloudflare Access — exclude `/hub` (MANUAL, do this BEFORE traffic)

Hub clients send the login JWT in the `Authorization` header; they do **not**
carry the Cloudflare Access cookie. Without a bypass, Access 302-redirects every
hub request to the login page and all clients fail.

In the Cloudflare dashboard → **Zero Trust → Access → Applications**:
- Either add a **path bypass** for `/hub/*` on the existing `0wlan.com` app,
- **or** create a second self-hosted application scoped to `0wlan.com/hub` with a
  single **Bypass** policy (Action: Bypass, Include: Everyone).

This mirrors the existing `/install.sh` + `/releases` exclusions. The hub is not
unprotected — it enforces its own JWT gate (`JARVIS_PROXY_AUTH_REQUIRED=1`); the
bypass only removes the *cookie-based* Access wall so the *token-based* gate can
do its job.

## 4. Deploy

```bash
cd src/web
docker compose --env-file .env.production up -d --build hub
docker compose --env-file .env.production up -d caddy   # reload with the /hub route
```

## 5. Health

```bash
docker compose ps hub                 # State: healthy
docker compose logs hub | tail -n 20  # expect: "inbound auth: REQUIRED (login token)"  and  "Ready — provider: …"
```

## 6. End-to-end smoke — BOTH shapes (through Caddy + Cloudflare)

Get a valid login JWT (`jarvis auth login`, or read the current one from
`~/.claude/.credentials.json`), then:

```bash
TOKEN=…   # a valid login JWT

# Diagnostic — provider key-presence on the VPS:
curl -s https://0wlan.com/hub/config -H "Authorization: Bearer $TOKEN" | head -c 300; echo

# OpenAI shape (the voice/web client path):
curl -sS https://0wlan.com/hub/v1/chat/completions -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"model":"deepseek-v4-flash","stream":false,"messages":[{"role":"user","content":"say OK"}]}' | head -c 300; echo

# Anthropic shape (the CLI client path):
curl -sS https://0wlan.com/hub/v1/messages -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -H 'anthropic-version: 2023-06-01' \
  -d '{"model":"deepseek-v4-flash","max_tokens":64,"messages":[{"role":"user","content":"say OK"}]}' | head -c 300; echo

# Negative — no token must be rejected:
curl -s -o /dev/null -w 'no-token status: %{http_code}\n' https://0wlan.com/hub/config
```
Expect: `/hub/config` → JSON with `providers.*` true; both shapes → assistant
text; the no-token request → `401`.

## 7. Latency probe (go/no-go input for voice, sub-project 3)

Time first-token through the hub vs a direct provider call, from the box that
runs voice:

```bash
# through the hub:
time curl -sS https://0wlan.com/hub/v1/chat/completions -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"model":"deepseek-v4-flash","stream":true,"messages":[{"role":"user","content":"hi"}]}' >/dev/null
# direct (baseline):
time curl -sS https://api.deepseek.com/v1/chat/completions -H "Authorization: Bearer $DEEPSEEK_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"model":"deepseek-chat","stream":true,"messages":[{"role":"user","content":"hi"}]}' >/dev/null
```
Record both numbers here. If the hub adds more than a few hundred ms, reconsider
routing **voice** through it (the hybrid fallback in the spec) before doing
sub-project 3 — web + CLI are latency-tolerant regardless.

---

## Rollback

```bash
cd src/web
docker compose --env-file .env.production stop hub
# revert Caddy: git revert the compose/Caddy commit (or comment the handle_path /hub block) + `docker compose up -d caddy`
```
Nothing local depends on the hub yet, so stopping it is safe — only the public
`/hub` route goes dark.

## Security notes

- The `hub` service has **no `ports:`** mapping — it is reachable only by Caddy
  on the compose network, never directly from the internet.
- It binds `0.0.0.0` *inside the container* via the deliberate
  `JARVIS_ALLOW_PUBLIC_BIND=1` + `JARVIS_PROXY_AUTH_REQUIRED=1` posture.
- Keys now sit only on the VPS. **Do not** empty the local `~/.jarvis/keys.env`
  provider keys until sub-projects 2–5 repoint voice/CLI/web — they still read
  the local keys today; removing them now breaks both immediately.
- Key rotation (post-cutover): edit `.env.production`, `docker compose
  --env-file .env.production up -d hub`.
