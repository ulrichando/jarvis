# Cloud Computer-Use Desktop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run JARVIS computer use on an isolated Linux desktop container on the VPS so account-connected web users get computer use without a tunnel to the local box.

**Architecture:** A new `computer-use` Docker service (Xvfb virtual display + openbox/tint2 + Firefox + x11vnc + websockify + the existing `computer_use_service.py` sidecar) slotted into `src/web/docker-compose.yml`. The web app reaches the sidecar server-side (`:8771`) and the browser reaches the live desktop through a Caddy-proxied, auth-gated noVNC path (`:6080`). All sidecar/adapter/safety code is reused; a small delta makes the web route + sidecar `/health` split-topology-aware.

**Tech Stack:** Docker/Compose, Caddy, Xvfb/openbox/tint2/x11vnc/websockify, Python 3.13 (slim sidecar deps), Next.js route handler (modified Next — see `src/web/AGENTS.md`).

**Spec:** `docs/superpowers/specs/2026-07-02-cloud-computer-use-desktop-design.md`

**Verification reality:** Tasks 1 and 5 are locally verifiable (pytest / typecheck). Tasks 2–4, 6–7 are config/packaging — verified by `bash -n`, `docker build`, `docker compose config`, `caddy validate`. Task 8 (image build + compose up + live smoke) runs **on the VPS** (needs Docker + a real desktop image + provider keys); it cannot be completed in a local dev shell without Docker.

---

### Task 1: Sidecar `/health` reports `streamUp`

Lets the web route derive stream liveness from the co-located sidecar instead of a cross-container TCP probe.

**Files:**
- Modify: `src/voice-agent/computer_use_service.py`
- Test: `src/voice-agent/tests/test_computer_use_service_health.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# src/voice-agent/tests/test_computer_use_service_health.py
"""_stream_up + /health streamUp field (plan: cloud computer-use desktop)."""
import asyncio
import contextlib
import json

import computer_use_service as svc


def test_stream_up_true(monkeypatch):
    monkeypatch.setattr(svc.socket, "create_connection", lambda *a, **k: contextlib.nullcontext())
    assert svc._stream_up(port=6080) is True


def test_stream_up_false(monkeypatch):
    def boom(*a, **k):
        raise OSError("connection refused")
    monkeypatch.setattr(svc.socket, "create_connection", boom)
    assert svc._stream_up(port=6080) is False


def test_health_includes_streamUp(monkeypatch):
    monkeypatch.setattr(svc.socket, "create_connection", lambda *a, **k: contextlib.nullcontext())
    resp = asyncio.run(svc._health(None))
    data = json.loads(resp.text)
    assert data["streamUp"] is True
    assert "providers" in data and "x11" in data
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_computer_use_service_health.py -q`
Expected: FAIL — `AttributeError: module 'computer_use_service' has no attribute 'socket'` / `_stream_up`.

- [ ] **Step 3: Add `socket` import + `_stream_up` + wire into `_health`**

In `src/voice-agent/computer_use_service.py`, add `import socket` to the imports block, and near the other module constants (after `MAX_STEPS = ...`):

```python
import socket  # add to the top import block
```

Add the helper (place it just above `async def _health`):

```python
def _stream_up(host: str = "127.0.0.1", port: int | None = None, timeout: float = 0.5) -> bool:
    """True if the noVNC websockify stream is accepting connections. Co-located
    with the sidecar (same container/host), so 127.0.0.1 is correct. The web
    route reads this from /health instead of probing across containers."""
    p = int(os.environ.get("JARVIS_CU_WS_PORT", "6080")) if port is None else port
    try:
        with socket.create_connection((host, p), timeout=timeout):
            return True
    except OSError:
        return False
```

Replace the `_health` body to include `streamUp`:

```python
async def _health(_req: web.Request) -> web.Response:
    return web.json_response(
        {"ok": True, "x11": x11_backend_available(), "model": MODEL,
         "max_steps": MAX_STEPS, "providers": available_providers(),
         "streamUp": _stream_up()}
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_computer_use_service_health.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Full suite gate + commit**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/ -q` → no new failures.

```bash
git add src/voice-agent/computer_use_service.py src/voice-agent/tests/test_computer_use_service_health.py
git commit -m "feat(computer-use): sidecar /health reports streamUp (cloud desktop split-topology)" -- src/voice-agent/computer_use_service.py src/voice-agent/tests/test_computer_use_service_health.py
```

---

### Task 2: Slim Python requirements for the container

**Files:**
- Create: `src/voice-agent/requirements-cu.txt`

- [ ] **Step 1: Create the file**

```
# Slim deps for the cloud computer-use sidecar (no torch/livekit/whisper).
# Verified: importing computer_use_service loads none of the heavy voice stack.
# Keep versions in sync with src/voice-agent/requirements.txt where they overlap.
anthropic>=0.105
openai>=1.50
google-genai>=2.6
aiohttp>=3.9
mss>=10.0
Pillow>=10.0
```

- [ ] **Step 2: Sanity-check the pins resolve (optional, needs network)**

Run: `python -m pip download --no-deps -d /tmp/cu-wheels -r src/voice-agent/requirements-cu.txt >/dev/null 2>&1 && echo OK || echo "check pins"`
Expected: `OK` (or adjust a pin if a version is yanked).

- [ ] **Step 3: Commit**

```bash
git add src/voice-agent/requirements-cu.txt
git commit -m "feat(computer-use): slim requirements-cu.txt for the desktop container" -- src/voice-agent/requirements-cu.txt
```

---

### Task 3: Container entrypoint `start.sh`

Starts Xvfb → openbox/tint2 → x11vnc → websockify(0.0.0.0) → Firefox → sidecar.

**Files:**
- Create: `src/voice-agent/scripts/computer-use-container-start.sh`

- [ ] **Step 1: Create the script**

```bash
#!/usr/bin/env bash
# Entrypoint for the cloud computer-use desktop container.
# Xvfb :99 -> openbox + tint2 -> x11vnc -> websockify(0.0.0.0) -> firefox -> sidecar.
set -euo pipefail

export DISPLAY="${DISPLAY:-:99}"
GEOM="${JARVIS_CU_GEOMETRY:-1280x800x24}"
VNC_PORT="${JARVIS_CU_VNC_PORT:-5900}"
WS_PORT="${JARVIS_CU_WS_PORT:-6080}"
PASS="${JARVIS_CU_VNC_PASSWORD:?JARVIS_CU_VNC_PASSWORD is required}"
RFBAUTH="/tmp/computer-use-vnc.rfbauth"

# 1. Virtual display
Xvfb "$DISPLAY" -screen 0 "$GEOM" -ac +extension RANDR >/tmp/xvfb.log 2>&1 &
for _ in $(seq 1 50); do xdpyinfo -display "$DISPLAY" >/dev/null 2>&1 && break; sleep 0.1; done

# 2. Window manager + panel (EWMH so wmctrl enumeration / focus_app works)
openbox >/tmp/openbox.log 2>&1 &
tint2 >/tmp/tint2.log 2>&1 &

# 3. VNC server (localhost in-container) with the fixed password
x11vnc -storepasswd "$PASS" "$RFBAUTH" >/dev/null 2>&1
x11vnc -display "$DISPLAY" -rfbport "$VNC_PORT" -rfbauth "$RFBAUTH" \
  -localhost -forever -shared -noxdamage -ncache 0 -quiet -bg >/tmp/x11vnc.log 2>&1

# 4. websockify — bind 0.0.0.0 so Caddy (another container) can reach it
websockify --daemon "0.0.0.0:${WS_PORT}" "127.0.0.1:${VNC_PORT}" >/tmp/websockify.log 2>&1

# 5. Pre-launch a browser so the desktop isn't empty (best-effort)
( sleep 1; firefox --no-remote about:blank >/tmp/firefox.log 2>&1 & ) || true

# 6. The sidecar as the container's main process
cd /app
exec python computer_use_service.py
```

- [ ] **Step 2: Syntax-check**

Run: `bash -n src/voice-agent/scripts/computer-use-container-start.sh && echo OK`
Expected: `OK`.

- [ ] **Step 3: Commit**

```bash
git add src/voice-agent/scripts/computer-use-container-start.sh
git commit -m "feat(computer-use): container entrypoint (Xvfb+WM+x11vnc+websockify+sidecar)" -- src/voice-agent/scripts/computer-use-container-start.sh
```

---

### Task 4: `Dockerfile.computer-use`

**Files:**
- Create: `src/voice-agent/Dockerfile.computer-use`

- [ ] **Step 1: Create the Dockerfile**

```dockerfile
# Cloud computer-use desktop: virtual X11 + WM + browser + noVNC + the JARVIS sidecar.
FROM python:3.13-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
      xvfb x11-utils openbox tint2 firefox-esr \
      x11vnc websockify \
      xdotool wmctrl imagemagick scrot \
      fonts-dejavu-core ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Slim Python deps (sidecar only — no torch/livekit/whisper).
COPY requirements-cu.txt /app/requirements-cu.txt
RUN pip install --no-cache-dir -r requirements-cu.txt

# Sidecar + exactly the modules it imports.
COPY computer_use_service.py /app/
COPY tools/ /app/tools/
COPY pipeline/ /app/pipeline/
COPY scripts/computer-use-container-start.sh /app/start.sh
RUN chmod +x /app/start.sh

# Build-time smoke: the sidecar must import with ONLY the slim deps present.
# If this fails, a transitive import pulled a heavy dep — trim it, don't add torch.
RUN python -c "import computer_use_service; print('sidecar import OK')"

# Non-root
RUN useradd -m -u 10001 cu && chown -R cu:cu /app
USER cu

ENV DISPLAY=:99 \
    JARVIS_COMPUTER_USE_WEB_PORT=8771 \
    JARVIS_CU_WS_PORT=6080
EXPOSE 8771 6080
ENTRYPOINT ["/app/start.sh"]
```

- [ ] **Step 2: Build (needs Docker; run on the box or a Docker host)**

Run: `cd src/voice-agent && docker build -f Dockerfile.computer-use -t jarvis-cu-desktop .`
Expected: build succeeds; the `sidecar import OK` line prints (proves the slim-deps claim). If the import step fails, read the error, add ONLY the missing light dep to `requirements-cu.txt`, rebuild.

- [ ] **Step 3: Commit**

```bash
git add src/voice-agent/Dockerfile.computer-use
git commit -m "feat(computer-use): Dockerfile for the cloud desktop image" -- src/voice-agent/Dockerfile.computer-use
```

---

### Task 5: Web route — `streamUp` from health, VNC password from env

**Files:**
- Modify: `src/web/src/app/api/computer-use/route.ts`

**Pre-step:** Per `src/web/AGENTS.md`, this is a modified Next.js — skim `node_modules/next/dist/docs/` for any route-handler changes before editing. The edit uses no new Next APIs (only handler-internal logic), so this is a sanity check, not a blocker.

- [ ] **Step 1: Edit the constants + `Health` type + `readVncPassword`**

Change the `Health` type to include the new field:

```typescript
type Health = { ok: boolean; providers?: Record<string, boolean>; streamUp?: boolean }
```

Make `readVncPassword` prefer the env var (split topology: the password is set in the CU container, not on the web container's disk):

```typescript
async function readVncPassword(): Promise<string | null> {
  const fromEnv = process.env.JARVIS_CU_VNC_PASSWORD?.trim()
  if (fromEnv) return fromEnv
  try {
    const raw = await fs.readFile(PASS_FILE, 'utf8')
    const pass = raw.trim()
    return pass || null
  } catch {
    return null // not minted yet → stream hasn't been started
  }
}
```

- [ ] **Step 2: Replace the `GET` handler to trust `health.streamUp`**

```typescript
export async function GET(): Promise<Response> {
  const [health, password] = await Promise.all([sidecarHealth(), readVncPassword()])
  const scUp = !!health?.ok
  const streamUp = !!health?.streamUp
  const ready = streamUp && scUp && !!password
  return Response.json({
    ready,
    streamUp,
    sidecarUp: scUp,
    providers: health?.providers ?? {},
    wsUrl: VNC_WS_URL,
    password: streamUp ? password : null,
    hint: ready ? null : 'Run `bin/jarvis-computer-use start` (local) or bring up the computer-use container (deployed).',
  })
}
```

- [ ] **Step 3: Remove the now-dead `tcpUp` helper and its `net` import**

Delete the `import net from 'node:net'` line and the entire `tcpUp` function (no longer referenced — liveness now comes from `/health`).

- [ ] **Step 4: Typecheck + build**

Run: `cd src/web && npm run build 2>&1 | tail -20` (or the repo's typecheck script if faster)
Expected: builds clean; no unused-`net`/`tcpUp` errors, no type error on `health.streamUp`.

- [ ] **Step 5: Commit**

```bash
git add src/web/src/app/api/computer-use/route.ts
git commit -m "feat(computer-use): web route reads streamUp from /health + VNC password from env (split topology)" -- src/web/src/app/api/computer-use/route.ts
```

---

### Task 6: Compose service + isolated network + web env wiring

**Files:**
- Modify: `src/web/docker-compose.yml`

- [ ] **Step 1: Add the `computer-use` service** (under `services:`)

```yaml
  computer-use:
    build:
      context: ../voice-agent
      dockerfile: Dockerfile.computer-use
    restart: unless-stopped
    environment:
      DISPLAY: ":99"
      JARVIS_CU_WS_PORT: "6080"
      JARVIS_COMPUTER_USE_WEB_PORT: "8771"
      JARVIS_COMPUTER_USE_TIER: "${JARVIS_COMPUTER_USE_TIER:-full}"
      JARVIS_CU_VNC_PASSWORD: "${JARVIS_CU_VNC_PASSWORD:?set JARVIS_CU_VNC_PASSWORD in .env}"
      ANTHROPIC_API_KEY: "${ANTHROPIC_API_KEY:-}"
      OPENAI_API_KEY: "${OPENAI_API_KEY:-}"
      GEMINI_API_KEY: "${GEMINI_API_KEY:-}"
    networks: [cu-net]
    mem_limit: 2g
    shm_size: "512m"
    cap_drop: [ALL]
    security_opt: ["no-new-privileges:true"]
    expose: ["8771", "6080"]
```

- [ ] **Step 2: Add the `cu-net` network + keep existing services on `default`**

At the top-level `networks:` block (add the block if absent; the file currently has none):

```yaml
networks:
  default: {}
  cu-net:
    driver: bridge
```

On the `web` service, add BOTH networks + the two pointer envs (append to its existing `environment:` and add a `networks:` key):

```yaml
    # web service — add to environment:
      JARVIS_COMPUTER_USE_WEB_URL: "http://computer-use:8771"
      JARVIS_CU_VNC_WS_URL: "wss://0wlan.com/cu-vnc"
    # web service — add networks (keeps default connectivity to postgres etc.):
    networks: [default, cu-net]
```

On the `caddy` service, add the networks key so it can reach the stream:

```yaml
    # caddy service — add:
    networks: [default, cu-net]
```

> `computer-use` is on `cu-net` ONLY → it can reach the internet and be reached by `web`/`caddy`, but cannot reach `postgres`/`docker-proxy` (which stay on `default`).

- [ ] **Step 3: Add `JARVIS_CU_VNC_PASSWORD` to the deploy env file**

Add a strong password to `src/web/.env.production` (or wherever the compose `env_file` points): `JARVIS_CU_VNC_PASSWORD=<openssl rand -base64 12 | tr -dc A-Za-z0-9 | cut -c1-16>`.

- [ ] **Step 4: Validate compose (needs Docker)**

Run: `cd src/web && docker compose config >/dev/null && echo OK`
Expected: `OK` (no schema errors; `computer-use` on `cu-net`, `web`/`caddy` on `default`+`cu-net`). If `docker compose config` reports `web`/`caddy` dropped off `default`, ensure `default: {}` is declared and both are listed as shown.

- [ ] **Step 5: Commit**

```bash
git add src/web/docker-compose.yml
git commit -m "feat(computer-use): compose service + isolated cu-net + web env wiring" -- src/web/docker-compose.yml
```

---

### Task 7: Caddy `/cu-vnc` reverse-proxy (auth-gated live stream)

**Files:**
- Modify: the Caddyfile mounted by the `caddy` service (locate via the `caddy` volume mount in `docker-compose.yml`, e.g. `src/web/Caddyfile` or `src/web/caddy/Caddyfile`).

- [ ] **Step 1: Find the Caddyfile**

Run: `grep -nA4 'caddy:' src/web/docker-compose.yml | grep -iE 'Caddyfile|volumes'`
Then open the host path that's mounted to `/etc/caddy/Caddyfile`.

- [ ] **Step 2: Add the stream block inside the site (the `0wlan.com { ... }` block)**

```
# Live desktop stream for /computer-use → websockify in the computer-use container.
# handle_path strips the /cu-vnc prefix so websockify sees the WS at its root.
# Auth: Cloudflare Access (edge) + the VNC password. Do NOT exclude /cu-vnc from
# Cloudflare Access — that gate is the primary guard here.
handle_path /cu-vnc* {
    reverse_proxy computer-use:6080
}
```

Caddy's `reverse_proxy` upgrades WebSocket automatically. Ensure this block is INSIDE the existing site block (so it inherits TLS + is subject to the CF Access gate), and that no `/api/auth/*`-style exclusion covers `/cu-vnc`.

- [ ] **Step 3: Validate**

Run: `docker compose exec caddy caddy validate --config /etc/caddy/Caddyfile` (deployed) or `caddy validate --config <path>` (local caddy).
Expected: `Valid configuration`.

- [ ] **Step 4: Commit**

```bash
git add <path-to-Caddyfile>
git commit -m "feat(computer-use): Caddy /cu-vnc reverse-proxy for the live desktop stream" -- <path-to-Caddyfile>
```

---

### Task 8: Integration + live smoke (ON THE VPS — needs Docker + keys)

Not completable in a local dev shell. Run on the deploy host.

- [ ] **Step 1: Build + bring up**

Run: `cd src/web && docker compose build computer-use && docker compose up -d computer-use caddy web`

- [ ] **Step 2: Sidecar health (server-side, via the web network)**

Run: `docker compose exec web sh -lc 'wget -qO- http://computer-use:8771/health'`
Expected JSON: `{"ok":true,"x11":true,...,"streamUp":true,"providers":{...}}`.

- [ ] **Step 3: Web status probe**

Load `https://0wlan.com/computer-use` (logged in). The status should show `ready` once `streamUp` + `sidecarUp` + a password are all present; the live noVNC view should render the Xvfb desktop with Firefox.

- [ ] **Step 4: Live task smoke**

In the page, run a task: "open a new tab and go to example.com, then tell me the page heading." Confirm the SSE action log advances and the live view shows it happening. Confirm the sensitive-app blocklist still trips (try "open my bank" → blocked).

- [ ] **Step 5: Rollback note**

To disable: `docker compose stop computer-use` and unset the two `web` envs. The route/health edits are backward-compatible (env/file fallback), so they can stay.

---

## Self-Review

- **Spec coverage:** container components (Task 3/4), slim image (Task 2/4 build-check), networking split (Task 5/6), Caddy public path (Task 7), security isolation — non-root/cap_drop/mem_limit/no-socket (Task 4/6) + network segmentation (Task 6) + auth (Task 7), app-code delta — route+health (Task 1/5), testing (Task 1 pytest + Task 8 smoke), rollout (Task 8). All spec sections map to a task. Deferred items (per-session, on-demand, reset button, egress allowlist) are intentionally out.
- **Placeholder scan:** the only "locate the file" step is Task 7 Step 1 (the Caddyfile path is deploy-specific — a discovery step with an exact command, not a TODO). No other placeholders.
- **Type/name consistency:** `_stream_up` (Task 1) ↔ `streamUp` health field (Task 1) ↔ `health.streamUp` in route (Task 5) ↔ `JARVIS_CU_VNC_PASSWORD` (Tasks 3/5/6) ↔ `JARVIS_CU_WS_PORT`=6080 ↔ websockify `0.0.0.0:6080` (Task 3) ↔ `computer-use:6080` (Tasks 6/7). Consistent.
