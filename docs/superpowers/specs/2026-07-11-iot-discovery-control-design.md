# JARVIS Local Network IoT Discovery & Control — Design

**Date:** 2026-07-11
**Status:** Draft (awaiting review)
**Author:** JARVIS (with Ulrich)

## 1. Goal

Give JARVIS a local-network smart-home capability, callable by the voice agent, brain-server agents, and the web UI:

1. Discover devices on the LAN (IPs + hostnames).
2. Identify device type/brand (lights, TVs, Alexa, switches, thermostats…).
3. Expose a REST API the brain + voice agent + web call.
4. Handle common IoT protocols (mDNS, SSDP/UPnP, and more).
5. Voice commands: "what devices do I have", later "turn on the living-room light".
6. New **Devices** tab in the web left-nav (list + manual control).
7. Remote access via the JARVIS domain with existing auth.
8. Speakable confirmations for voice.
9. Modular — new device families/protocols added as plugins.

## 2. Reality check (fact-checked 2026-07-11, sourced)

This grounds the scope honestly — the household in question is **Alexa + cloud smart bulbs (vendor apps) + a smart TV, no hub**:

- **Discovery works for everything, but is NOT a single mDNS pass.** Echo (`_amzn-wplay._tcp` + SSDP), Roku (SSDP `roku:ecp` / `_roku._tcp`), Hue bridge (`_hue._tcp`), Cast/Cast-TVs (`_googlecast._tcp`) are mDNS/SSDP-findable. **Tuya/Smart-Life-class bulbs are NOT** — they use proprietary encrypted UDP broadcasts (:6666/:6667, plus :7000 for protocol v3.5 devices). **Wyze is locally invisible.** → per-protocol scanners + ARP/MAC-OUI vendor lookup, not one mDNS sweep.
- **Control splits hard:**
  - *Locally controllable:* Roku (ECP :8060, official), Vizio (:7345), LG webOS (wss :3001), Samsung Tizen (unofficial ws), Philips Hue (local bridge), LIFX (LAN UDP), legacy TP-Link Kasa. **Matter devices (local, vendor-neutral).**
  - *Discover-only (cloud-locked):* **Alexa — a hard wall, no local API exists, ever.** Cloud bulbs (Tuya/Wyze) unless they're Matter models. Nest/Ring.
  - *Eroding:* Kasa (newer KLAP firmware needs TP-Link cloud creds), Fire TV (ADB blocked; new Vega-OS sticks have none). Treat vendor local APIs as erosion-prone.
- **Matter/Thread is now first-class** (~34% of 2025 shipments; lighting/plugs best-covered). A Matter controller gives genuine local, vendor-neutral control. Don't embed the CHIP SDK — run the **Matter Server as a sidecar and consume its WebSocket API** (HA's architecture; HA moved to a matter.js-based server in 2026 — build against the WS API, not server internals). Caveat: Thread needs a border router; devices already bound to Alexa must be **shared to our fabric via Matter multi-admin** from the Alexa app.

**Implication:** ship discovery/identification first (works + truthful for the whole house, and reveals exactly what's controllable); make control a modular layer led by a Matter controller, with per-vendor adapters and an HA escape hatch. Never promise Alexa control.

## 3. Architecture overview

A standalone **Python IoT service** in the voice-agent tree, following the existing sidecar pattern (`computer_use_service.py`, :8771). Bound to `127.0.0.1:<port>` (default 8779).

```
voice agent ─┐
brain agents ─┼─► iot_service (REST, 127.0.0.1:8779) ─► scanners/  (mDNS, SSDP, Tuya-UDP, ARP)
web (via proxy)┘                                       └─► registry (in-mem + ~/.jarvis/iot-devices.json)
                                                        └─► controllers/  (Phase 2: matter, roku, hue, ha, …)
```

- **scanners/** — one module per discovery protocol; each yields raw `Observation`s.
- **identify.py** — merges observations per host → a `Device` with type/brand/controllability.
- **registry.py** — dedupe/merge by MAC (fallback IP), TTL, persist to `~/.jarvis/iot-devices.json`.
- **controllers/** (Phase 2) — one plugin per device family; each declares which devices it can drive + a `command()` surface.
- **service.py** — the REST API (**aiohttp**, matching `computer_use_service.py` — the CU sidecar is aiohttp, not FastAPI; aiohttp ships in the voice-agent venv via livekit-agents, FastAPI/uvicorn do not).
- Voice: a self-registering tool in `src/voice-agent/tools/` calls the local REST API.
- Web: a new **Devices** tab that calls the service **through the existing proxy** (auth/domain preserved).

## 4. Phase 1 — Discovery & Identification (this build)

### 4.1 Scanners (modular)
- `mdns.py` — `python-zeroconf`, browse the known service types (`_hue._tcp`, `_roku._tcp`, `_googlecast._tcp`, `_amzn-wplay._tcp`, `_airplay._tcp`, `_spotify-connect._tcp`, `_printer._tcp`, …).
- `ssdp.py` — SSDP M-SEARCH (`ssdp:all` + `roku:ecp`), parse UPnP device descriptors.
- `tuya.py` — listen for Tuya UDP broadcasts on :6666/:6667/:7000 (via `tinytuya` scanner — it listens on all three; :7000 carries protocol-v3.5 devices) to catch the cloud-bulb class that mDNS misses. Discover-only in Phase 1 (no local_key).
- `arp.py` — ARP table / ping sweep + **MAC OUI → vendor** lookup (`mac-vendor-lookup`) so even silent devices get a vendor guess.
- Each scanner is registered in a list; adding a protocol = adding a module. Scanners run concurrently with a bounded timeout.

### 4.2 Identification
`identify.py` merges observations by MAC (fallback IP) and fingerprints: mDNS service type, SSDP `ST`/model, open ports (8060 Roku, 7345 Vizio, 3001 LG, 8001/8002 Samsung, 80/443), HTTP banners, MAC vendor. Output verdict per device:
- `type` (light / tv / speaker / thermostat / plug / hub / unknown)
- `brand`
- `controllable`: `local` | `matter` | `cloud_only` | `unknown`
- `control_hint` (e.g. "Roku ECP", "needs Home Assistant", "Alexa — not locally controllable")

### 4.3 Data model
```
Device: id, name, ip, mac, hostname, type, brand, protocol[],
        controllable, control_hint, first_seen, last_seen, raw{}
```

### 4.4 REST API (127.0.0.1:8779)
- `GET  /health`
- `GET  /devices` — current registry (filter by type/controllable).
- `GET  /devices/{id}`
- `POST /scan` — trigger a fresh scan (async; returns job/updated list).
- `POST /devices/{id}/command` — **Phase 2 stub** (returns 501 with the honest reason until controllers land).

### 4.5 Voice tool
New self-registering tool `tools/smart_home.py` (registry-only, like the others): actions `list_devices` / `find_device(query)` / (Phase 2) `control`. Phase-1 answers "what devices do I have" with a **speakable summary** ("You've got 9 devices — a Roku TV, an Echo, three smart bulbs…"), and truthfully flags what's controllable. Gated via `check_fn` (service reachable).

### 4.6 Web Devices tab
New left-nav **Devices** tab (the existing tab pattern: an entry in `src/web/src/lib/ai/features.ts::PROVIDER_FEATURES["anthropic"]` — `{slug, label, icon, href: "/devices"}` — rendered by `src/web/src/components/layout/sidebar.tsx`, plus a route page at `src/web/src/app/(app)/devices/page.tsx`). Lists discovered devices grouped by room/type with a "Rescan" button and per-device detail. Control buttons render **disabled with the reason** in Phase 1; wired in Phase 2. Calls the service through the existing proxy route (so remote + auth work unchanged).

### 4.7 Remote access & auth
- Voice/brain call `127.0.0.1:8779` directly (same box).
- Web calls go through the existing Next.js proxy (`src/web/src/proxy.ts`) → a new `/api/iot/*` route that forwards to the local service, reusing the app's auth gate + CF-Access/domain. No new auth surface.

## 5. Phase 2 — Control (roadmap; separate plan)

`controllers/` plugins, in priority order:
1. **Matter controller** (first-class) — `matter-server` sidecar + a Python WS client; commission + control Matter devices locally.
2. **Per-vendor local adapters** — Roku (ECP), Hue, LIFX, Vizio, LG webOS, Samsung; each maps `command()` → device protocol.
3. **Home Assistant adapter** (escape hatch) — if the user runs HA, proxy control through HA's REST/WS API to reach the cloud-locked remainder.
4. **Alexa** — discover-only, permanently. The tool says so.

Each controller declares `can_control(device)` and `command(device, action, **params)`; the service routes a command to the first matching controller. Voice returns speakable confirmations ("Living-room light is on.").

## 6. Security & blast radius

- Service binds `127.0.0.1` only; never `0.0.0.0`.
- Remote access strictly through the authenticated web proxy — no direct WAN exposure.
- Phase 2 control commands are state-changing on physical devices; they require the same auth as the rest of the app, and destructive/ambiguous commands go through JARVIS's normal `clarify` path.
- Discovery is read-only/passive; note it enumerates the LAN (privacy-relevant) — data stays local in `~/.jarvis/iot-devices.json`.

## 7. Testing

- Per-scanner unit tests with **recorded/mocked** protocol responses (no live network in CI): a captured mDNS PTR, an SSDP descriptor, a Tuya broadcast frame, an ARP table sample.
- `identify.py` fingerprint tests: fixtures → expected type/brand/controllable verdict (incl. "Alexa → cloud_only").
- REST API tests (aiohttp test client — `aiohttp.test_utils`/`pytest-aiohttp`, matching the framework above).
- Voice-tool test: mocked service → speakable summary shape.
- Web: component test for the Devices tab render + proxy route test.

## 8. Dependencies (Phase 1)

`zeroconf` (use `zeroconf.asyncio.AsyncZeroconf` — the sync API would block the event loop), `async-upnp-client` (or `ssdp`), `tinytuya` (Tuya scan only), `mac-vendor-lookup`, **aiohttp** (already used by the CU sidecar — it is aiohttp, not FastAPI/uvicorn, and is already in the voice-agent venv). Phase 2 adds a Matter WS client + per-vendor libs (`rokuecp`, `aiohue`, `aiolifx`, `pyvizio`, `aiowebostv`, `samsungtvws`) and/or `homeassistant` REST. **Matter note:** the `python-matter-server` PyPI package is EOL (final release 8.1.2, Dec 2025) — HA 2026.2 replaced it with the matter.js-based Matter Server 9.0 (`matter-js/matterjs-server`), which keeps a python-matter-server-**compatible WebSocket API** (default `localhost:5580/ws`); run THAT as the sidecar and speak its WS protocol (the EOL package's `matter_server.client` still works against it today, but treat it as frozen).

## 9. Non-goals / honest limits

- **No Alexa control** — impossible locally; discover-only.
- **Wyze** — locally invisible; won't appear unless cloud API is added later.
- **Cloud bulbs** — discover-only unless they're Matter models or the user adds cloud OAuth / HA.
- Phase 1 does **not** control anything — it discovers, identifies, and tells the truth about what's controllable.

## 10. Open decisions

- Service home: `src/voice-agent/iot/` (new package) vs a sibling top-level service. Recommend `src/voice-agent/iot/` for reuse of the sidecar/venv pattern.
- Whether to stand up Home Assistant now (unlocks the cloud remainder in Phase 2) or defer until Phase 1 reveals the device mix. Recommend **defer** — decide from real data.
