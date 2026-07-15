# Honcho semantic recall for the cloud voice agent (VPS)

Phase 2 of cloud voice memory. Phase 0–1 gave the LiveKit voice worker
durable turns (`/api/voice-memory` → web Postgres) and curated memory
(`/api/memory`). This phase adds **semantic cross-session recall** backed by
a self-hosted [honcho](https://github.com/plastic-labs/honcho) stack
co-located on the VPS.

**Architecture — the web app is the ONLY thing that talks to honcho:**

```
voice worker (/opt/jarvis/voice-agent-lk)
  │  recall tool (LLM-chosen, multi-second)      POST /api/recall {mode:"query"}
  │  post-turn sync (fire-and-forget)            POST /api/recall {mode:"sync"}
  │  auth: service proxy JWT, sub="voice-agent"
  ▼
web app (src/web, /api/recall route)
  │  honcho v3 REST, HONCHO_BASE_URL=http://honcho:8000
  ▼  private docker network `jarvis-honcho` (internal:true, alias `honcho`)
honcho api :8000 ──► deriver (background) ──► pgvector Postgres + redis
```

Honcho is **never exposed**: every host port binds `127.0.0.1`
(api `:8000`, its Postgres `:5433`, redis `:6380`), and the web↔honcho hop
rides an `internal: true` compose network. Only the honcho `api` container
joins that network — the web stack's postgres/docker-proxy stay unreachable
from honcho and vice versa.

**Fail-soft contract:** while `HONCHO_BASE_URL` is unset (or honcho is
down), `/api/recall` answers `{text:""}` for queries and no-ops syncs —
always HTTP 200. The voice path never breaks because honcho is absent.
Turns and curated memory live in the **web Postgres**, not honcho; honcho
only ever holds a derived copy.

## Costs + license — read before bringing it up

- **Ongoing OpenAI spend.** Honcho's deriver (`gpt-5.4-mini`) + embeddings
  (`text-embedding-3-small`) run per synced message, and each `recall` query
  runs a server-side dialectic LLM call. Background (off the voice turn
  path) but real recurring cost, proportional to how much you talk.
- **AGPL-3.0.** Honcho is AGPL. We run the **unmodified upstream image**
  built from a pinned tag and expose it to no one (loopback + internal
  network; only our own web app calls it) — no source-offer obligation
  arises from that. If you ever **modify** honcho and let others interact
  with it over a network (even indirectly), AGPL §13 obliges you to offer
  the modified source. Keep the checkout unpatched; box-local config
  belongs in honcho's `.env` and the repo-owned override, not in tracked
  upstream files.

## Prerequisites

- Web stack deployed + up per `docs/runbook/deploy-online.md`
  (`/opt/jarvis/src/web`, `docker compose --env-file .env.production up -d`).
  Its compose declares + creates the `jarvis-honcho` network — honcho's
  override joins it as `external`, so **order matters: web stack first**.
- `OPENAI_API_KEY` filled in `/opt/jarvis/src/web/.env.production`
  (the setup script copies it into honcho's `.env` for the deriver).
- ~2 GB free disk for images + the pgvector volume.

## 1. Bring-up (on the VPS)

```bash
# 0. web stack already up (creates the jarvis-honcho network):
cd /opt/jarvis/src/web && docker compose --env-file .env.production up -d

# 1. provision honcho — clone pinned v3.0.9 → /opt/honcho, remap loopback
#    ports (5433/6380), install the network override, write .env (deriver
#    key from web .env.production, AUTH_USE_AUTH=false), build + start,
#    wait for /health, and append HONCHO_BASE_URL/HONCHO_API_KEY to the
#    web .env.production if absent. Idempotent — safe to re-run.
sudo /opt/jarvis/setup/honcho/setup-honcho-vps.sh

# 2. recreate the web container so it picks up the new env
#    (env_file changes require a recreate; `up -d` detects the config change)
cd /opt/jarvis/src/web && docker compose --env-file .env.production up -d web
```

The voice worker needs **no change**: it already posts turns to
`/api/recall {mode:"sync"}` fire-and-forget (silently skipped server-side
until honcho exists) and carries the `recall` tool.

### What the script wrote

| Where | What |
|---|---|
| `/opt/honcho` | upstream checkout pinned `v3.0.9` (the tag the `/v3` wire shapes were verified against) |
| `/opt/honcho/docker-compose.yml` | upstream example with host ports remapped: PG `127.0.0.1:5433`, redis `127.0.0.1:6380`, api `127.0.0.1:8000` |
| `/opt/honcho/docker-compose.override.yml` | copy of `setup/honcho/docker-compose.vps.yml` — joins `api` to `jarvis-honcho` under alias `honcho`. Repo-owned, re-copied on every setup run |
| `/opt/honcho/.env` (600) | `AUTH_USE_AUTH=false`, internal `DB_CONNECTION_URI`, `LLM_OPENAI_API_KEY=<your OPENAI_API_KEY>` |
| `src/web/.env.production` | appended `HONCHO_BASE_URL=http://honcho:8000` + `HONCHO_API_KEY=local` (placeholder — with auth off honcho ignores the header) |

### Honcho keying (fixed by the `/api/recall` route)

| Honcho entity | Value |
|---|---|
| workspace | `default` (`HONCHO_WORKSPACE_ID` to override) |
| user peer | `user-{web user_id}` — the peer-scoped deriver representation is the per-user isolation boundary |
| agent peer | `jarvis` (shared assistant peer) |
| session | `web-{user_id}-{room}` (user-namespaced, sanitized — generic LiveKit room names can't collide across users) |

## 2. Verify end-to-end

```bash
# a) honcho healthy (host loopback)
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/health   # → 200

# b) web container reaches honcho over jarvis-honcho (in-network DNS alias)
cd /opt/jarvis/src/web
docker compose --env-file .env.production exec web \
  node -e "fetch('http://honcho:8000/health').then(r=>console.log(r.status))"   # → 200

# c) /api/recall round-trip with the voice worker's own service JWT.
#    Needs a REAL web user id (the route 404s unknown ids):
docker compose --env-file .env.production exec postgres \
  psql -U jarvis -d jarvis -tAc 'select id from web.users limit 1'
# Mint a service token exactly the way the worker does (same secret the web
# stack verifies — JARVIS_PROXY_JWT_SECRET from .env.production):
cd /opt/jarvis/src/voice-agent-lk
TOKEN=$(JARVIS_PROXY_JWT_SECRET='<from web .env.production>' \
  python3 -c 'import proxy_token; print(proxy_token.mint_from_env())')
# Sync a turn, then query it back (from inside the web network so Cloudflare
# Access isn't in the way; the route is method-scoped SELF_AUTH in proxy.ts):
cd /opt/jarvis/src/web
docker compose --env-file .env.production exec web node -e "
const t=process.argv[1], u=process.argv[2];
const post=b=>fetch('http://127.0.0.1:3000/api/recall',{method:'POST',
  headers:{'content-type':'application/json',authorization:'Bearer '+t},
  body:JSON.stringify(b)}).then(async r=>console.log(r.status,await r.text()));
post({mode:'sync',user_id:u,role:'user',text:'My favorite color is teal.',session_id:'runbook-verify'})
  .then(()=>post({mode:'query',user_id:u,query:'What is my favorite color?'}));
" "$TOKEN" "<user-uuid>"
# sync → 200 {"ok":true}. query → 200 {"text":"..."} — may be empty for a
# minute or two on first use: the DERIVER ingests asynchronously. Re-run the
# query after it churns:
cd /opt/honcho && docker compose logs -f deriver   # watch it process the message

# d) the voice path: join a realtime voice room and ask
#    "Jarvis, what do you remember about my favorite color?" — the agent's
#    recall tool → /api/recall {mode:"query"} → spoken answer. Worker logs
#    (`docker logs -f voice-agent-lk`) show one warning per boot if
#    /api/recall is unreachable ("semantic recall will lag").
```

Auth negatives worth one curl each: no bearer → `401`; a proxy JWT minted
with any `sub` other than `voice-agent` → `403` (the sub-pin); an unknown
`user_id` → `404`.

## 3. Backup (Storage Box)

Honcho's only state is its pgvector Postgres (volume `honcho_pgdata`;
`POSTGRES_HOST_AUTH_METHOD=trust`, loopback-only). It is **derived data** —
losing it loses the accumulated user model (re-derivable only from future
turns), while the ground-truth turns stay in the web Postgres, which your
existing DB backups already cover. Still cheap to keep:

```bash
# nightly dump + push to the Hetzner Storage Box (same box as the web backups)
cat >/etc/cron.d/honcho-backup <<'EOF'
15 3 * * * root cd /opt/honcho && docker compose exec -T database \
  pg_dump -U postgres postgres | gzip > /var/backups/honcho-$(date +\%F).sql.gz \
  && scp -q /var/backups/honcho-$(date +\%F).sql.gz <storagebox>:backups/honcho/ \
  && find /var/backups -name 'honcho-*.sql.gz' -mtime +14 -delete
EOF
```

Restore: `gunzip -c honcho-<date>.sql.gz | docker compose exec -T database
psql -U postgres postgres` into a fresh stack (bring `api`/`deriver` down
first).

## 4. Ops

- **Logs:** `cd /opt/honcho && docker compose logs -f api deriver`
- **Pause / resume:** `docker compose down` / re-run the setup script.
  While down, recall fails soft (empty), syncs no-op with `ok:false` —
  nothing user-facing breaks; the deriver catches up only on messages
  synced *after* it returns (missed turns aren't replayed).
- **Manual `down` of the WEB stack:** while honcho's api is attached to
  `jarvis-honcho`, `docker compose down` in `src/web` fails with "network
  jarvis-honcho has active endpoints" — `cd /opt/honcho && docker compose
  down` first (or ignore the network-removal error). `deploy-poll.sh`
  never runs `down`, so continuous deploy is unaffected.
- **Disable for good:** remove `HONCHO_BASE_URL` from
  `src/web/.env.production`, recreate `web`, then `docker compose down -v`
  in `/opt/honcho`.
- **Continuous deploy does NOT touch honcho.** `deploy-poll.sh` only
  rebuilds the web/cli stacks; honcho is version-pinned and upgraded by
  hand. To upgrade: `git -C /opt/honcho fetch --tags && git checkout
  <newtag>`, then **re-verify the two wire shapes most likely to drift**
  before rebuilding — the `{"messages":[...]}` batch wrapper on
  `POST .../sessions/{id}/messages` and the `GET .../context?summary=true
  &tokens=...` query params — against `src/routers/` in the new tag.
  `/api/recall` was verified against `v3.0.9`.
- **Deriver spend guardrail:** the deriver bills per synced message. If
  OpenAI spend spikes, `docker compose stop deriver` stops derivation
  (sync + already-derived recall keep working) while you look.
- **Local/desktop honcho is a different install.** The local voice agent's
  stack (`setup/honcho/setup-honcho.sh`, `~/honcho`, keyed `ulrich`/
  `jarvis`) is independent of this one; nothing syncs between them.
