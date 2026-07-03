# Cloud Computer-Use Desktop — Design Spec

**Date:** 2026-07-02
**Status:** Approved (brainstorming complete — Approach 1, persistent cloud desktop)
**Written against commit:** `9861fd11` (branch `cli-feature-unlock`)
**Goal:** Make JARVIS computer use work when the user is connected via their account on the
deployed web app (0wlan.com), by giving the model an **isolated Linux desktop that runs on
the server** — not the user's local Moon box, and with no tunnel back to it.

---

## 1. Problem

Computer use today is **X11-local**: both the voice tool and the web `/computer-use` sidecar
drive `DISPLAY=:0` on whatever box runs them. The web route reaches the sidecar at
`http://127.0.0.1:8771` and the noVNC stream at `ws://127.0.0.1:6080` — a **co-located**
assumption. When the web app is served from the Hetzner VPS (0wlan.com) and the desktop is on
the local Moon box, the VPS web app cannot reach Moon's sidecar, so "connected to my account"
does **not** give computer use. The user has also declined a tunnel back to Moon (deploy
runbook), and driving the real machine over the internet is a large security surface.

## 2. Decision

Run a **persistent, isolated cloud desktop container on the VPS** and point the existing web
app at it. This mirrors how Anthropic's reference `computer-use-demo` and Claude's cloud
computer use work (a sandboxed virtual desktop the agent controls, never the user's real
machine). Chosen over per-session ephemeral containers (YAGNI for a single user) and over a
host-level Xvfb (worst isolation on an internet-facing box).

**Core insight:** the sidecar (`computer_use_service.py`), the X11 backend
(`tools/computer_use*.py`), the adapters (`pipeline/cu_adapters/`), the safety layers, the
audit, and the noVNC view are all `$DISPLAY`-agnostic and reused unchanged. The build is
mostly **packaging + deploy**, with a small, bounded app-code delta (§6).

## 3. Architecture

```
Browser (0wlan.com, logged in)
 ├─ page → POST /api/computer-use/run (SSE)
 │     → Next route → http://computer-use:8771/run   (SERVER-SIDE, compose net — never public)
 │     → sidecar loop drives DISPLAY=:99 via xdotool/wmctrl
 │     → SSE text/action/permission/done back to the page
 └─ noVNC client → wss://0wlan.com/cu-vnc            (BROWSER-SIDE → must be public + auth-gated)
       → Caddy reverse-proxy → computer-use:6080 (websockify) → x11vnc :5900 → Xvfb :99
```

One new `docker-compose.yml` service, `computer-use`, whose entrypoint (`start.sh`) starts and
supervises a small process tree; the web service is pointed at it via two env vars.

## 4. Components (the `computer-use` container)

| Process | Role | Notes |
|---|---|---|
| **Xvfb** | Virtual display `:99` | `1280x800x24` (good for web; sidecar downscales to 1280; SOM is coordinate-free) |
| **openbox + tint2** | Minimal WM + taskbar | EWMH-compliant so `wmctrl` enumeration + `focus_app` work (SOM overlays depend on it) |
| **firefox** | Browser for web tasks | Demo default; profile persists (see §7 caveat) |
| **x11vnc** | Serve `:99` on VNC `:5900` | `-localhost` (in-container) + `-rfbauth` from a fixed password |
| **websockify** | VNC `:5900` → WS `:6080` | **Binds `0.0.0.0:6080`** (not `127.0.0.1`) so Caddy reaches it over the compose net |
| **sidecar** | `computer_use_service.py` on `:8771` | `DISPLAY=:99`; provider keys via env |

**Slim image (verified):** importing the sidecar loads **zero** heavy deps (no
torch/livekit/whisper). `requirements-cu.txt` = `anthropic`, `openai`, `google-genai`,
`aiohttp`, `mss`, `Pillow` — all light HTTP SDKs, lazily imported by `make_adapter`.
`available_providers()` only reads env keys, so `/health` works without the SDKs loaded. OS
packages: `xvfb`, `openbox`, `tint2`, `firefox-esr`, `x11vnc`, `websockify`, `xdotool`,
`wmctrl`, `imagemagick`, `scrot`, `python3`, `fonts-dejavu`.

## 5. Networking — two ports, two exposure rules

- **`:8771` (loop / SSE / approve)** — reached **server-side** by the Next route; set
  `JARVIS_COMPUTER_USE_WEB_URL=http://computer-use:8771` on the `web` service. **Never public.**
- **`:6080` (live desktop pixels)** — consumed **browser-side** by the noVNC client, so it needs
  a public path: a **Caddy** reverse-proxy block for `/cu-vnc` → `computer-use:6080`, behind the
  same web-login + Cloudflare Access gate; then `JARVIS_CU_VNC_WS_URL=wss://0wlan.com/cu-vnc` on
  `web`. The route already supports this seam (its comment documents `wss://…/vnc`).

## 6. App-code delta (bounded — NOT zero, per fact-check)

The web route and sidecar carry co-located assumptions that must change for the split topology:

1. **`src/web/src/app/api/computer-use/route.ts`**
   - Stream-liveness probe is hardcoded `tcpUp('127.0.0.1', 6080)` — in a split it checks the
     *web* container's own localhost and always reports `streamUp:false`. **Fix:** derive liveness
     from the sidecar `/health` (co-located with websockify) instead of a cross-container TCP probe.
   - `readVncPassword()` reads `~/.jarvis/computer-use-vnc.pass` from the *web* container's disk
     (the password is minted in the *CU* container). **Fix:** read the VNC password from an env var
     (`JARVIS_CU_VNC_PASSWORD`), falling back to the local file so local dev is unchanged.
   - (The `${SIDECAR}/health` + `/run` fetches already use `JARVIS_COMPUTER_USE_WEB_URL` — fine.)
   - **Constraint:** `src/web` runs a modified Next.js — read `node_modules/next/dist/docs/` before
     editing route code (per `src/web/AGENTS.md`).
2. **`src/voice-agent/computer_use_service.py`** — `/health` additionally reports `streamUp`
   (check its own container's `127.0.0.1:6080`), so the route trusts health instead of probing
   across containers. Additive; local behavior unchanged.
3. **Container `start.sh`** (new) vs. `bin/jarvis-computer-use-stream`: websockify binds
   `0.0.0.0:6080`; x11vnc uses the fixed `JARVIS_CU_VNC_PASSWORD` (via `-storepasswd` into an
   rfbauth file at boot) rather than a random per-boot mint, so web and stream agree on the password.

## 7. Security model (internet-facing LLM desktop — load-bearing)

1. **Container isolation** — non-root user, `cap_drop: [ALL]` (+ only what X needs),
   `security_opt: no-new-privileges`, `mem_limit` ~2 GB, **no docker socket** (unlike `hub`). The
   CU desktop is a *static* service, so it needs none of the `docker-proxy` machinery.
2. **Network segmentation** — `docker-compose.yml` currently has **no `networks:` section** (all
   services on one default bridge). Add a `cu-net` so `computer-use` connects only to `web` (for
   `:8771`) and `caddy` (for `:6080`), and **cannot reach `postgres` / `docker-proxy`**. Internet
   egress stays open (needed for browsing).
3. **App-layer safety unchanged** — the sidecar's sensitive-app blocklist (banking/crypto/
   passwords), permission tiers (`JARVIS_COMPUTER_USE_TIER`), per-action "Supervised" approval, and
   redacted audit all run inside the container exactly as today.
4. **Auth** — `/api/computer-use` already sits behind web login + Cloudflare Access; the `/cu-vnc`
   Caddy path gets the same gate + the VNC password (x11vnc `-localhost` in-container + password +
   auth-gated public path = layered).
5. **Secrets** — inject only `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GEMINI_API_KEY` and
   `JARVIS_CU_VNC_PASSWORD` (compose secret/env). No PG creds or other secrets in the sandbox.
6. **Persistent-state caveat** — a persistent container keeps browser cookies/logins across tasks
   (feature: stay signed into sites; risk: a poisoned sandbox retains them). Acceptable single-user;
   a "reset desktop" (container restart) button is deferred (§10).

## 8. New vs. reused

- **New:** `Dockerfile.computer-use`, `start.sh`, `requirements-cu.txt`, one `docker-compose.yml`
  service + `cu-net`, one Caddy `/cu-vnc` block, env wiring on `web`.
- **Small edits:** `route.ts` (liveness via health + password via env), `computer_use_service.py`
  (`/health` reports `streamUp`).
- **Reused unchanged:** `computer_use_service.py` loop/adapters/safety/audit,
  `tools/computer_use*.py`, `pipeline/cu_adapters/*`, `pipeline/computer_use_vision.py`, the
  `/computer-use` page + noVNC component.

## 9. Resource footprint

Estimate (unmeasured): Xvfb+openbox+tint2 ~0.2 GB, Firefox ~0.5–1.5 GB, sidecar ~0.2–0.4 GB →
~1–2 GB steady. Cap at **2 GB**. The CPX31 has 8 GB shared with web + PG + `/code` containers, so
concurrency is tight — if it's a problem, **on-demand start** (spin `computer-use` only when a
session opens) is a clean follow-up (§10). Measure real usage before raising the cap.

## 10. Deferred (YAGNI now)

- Per-session ephemeral containers (multi-user / clean-room isolation).
- On-demand container start/stop tied to session lifecycle (resource savings).
- "Reset desktop" button (restart container to clear browser state).
- Egress domain allowlist (start with open egress + app blocklist + network segmentation).

## 11. Testing

- `docker build` succeeds; `docker compose up computer-use` → `/health` = `{ok, x11:true,
  streamUp:true, providers}`.
- noVNC reachable through Caddy (auth-gated) shows the Xvfb desktop.
- Manual live smoke (real key): a task like "open Firefox and go to example.com" completes via
  `/run`, and the `/computer-use` page renders the SSE action log + live view.
- Existing `src/voice-agent` pytest stays green (the `/health` `streamUp` addition gets a unit
  test with websockify mocked up/down); `src/web` typecheck/build stays green after the route edit.

## 12. Rollout

1. Land the Dockerfile/start.sh/requirements + compose service + Caddy block + route/health edits.
2. `docker compose build computer-use && docker compose up -d computer-use`.
3. Verify `/health` + auth-gated noVNC; run the manual smoke.
4. Point `web` env at the container; confirm `/computer-use` works from 0wlan.com.
5. Rollback: remove the `computer-use` service + revert the two env vars on `web`; the route/health
   edits are backward-compatible (env/file fallback) so they can stay.

## 13. References

- Anthropic computer-use tool doc + `anthropic-quickstarts/computer-use-demo` (container recipe:
  Xvfb + WM + panel + Firefox + noVNC `:6080`, 1024×768, isolation guidance).
- `docs/runbook/computer-use.md` — the two current CU surfaces.
- `src/web/src/app/api/computer-use/route.ts` — the existing web seam (`JARVIS_COMPUTER_USE_WEB_URL`,
  `JARVIS_CU_VNC_WS_URL`).
- `bin/jarvis-computer-use-stream` — the x11vnc/websockify + password-mint mechanism this container
  adapts.
