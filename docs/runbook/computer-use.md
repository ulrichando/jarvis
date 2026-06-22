# Computer use — how it actually works (reference)

**Status:** current as of 2026-06-22. Grounded in the live code, not the historical
specs. Where the older specs / CLAUDE.md disagree with this doc, **this doc is
right about the running system** — see [§7 Documentation truth](#7-documentation-truth).

This is the study deliverable for backlog #49 ("study all computer-use
documentation — design / workflow / functionality"). It consolidates four specs
+ four plans and reconciles them against what is actually deployed.

---

## 1. There are TWO computer-use surfaces

They share the executor and the screen-grounding layer but are reached
completely differently. Don't conflate them.

| | **Voice tool** | **Web `/computer-use`** |
|---|---|---|
| Entry | Supervisor calls the `computer_use` registry tool mid-conversation | Browser page → SSE → standalone sidecar service |
| Process | In the voice-agent worker | `computer_use_service.py`, **separate** aiohttp service on `:8771` |
| Driver | The supervisor LLM (or its fallback cascade) | Per-request model picked in the page (multi-provider) |
| Screen to user | None (voice only) | Live noVNC stream (`x11vnc` + `websockify`, `:6080`) |
| Approval | Voice confirm + tier gate + blocklist | In-chat permission cards + blocklist |
| Code | `tools/computer_use.py` | `computer_use_service.py` + `pipeline/cu_adapters/` |

Both drive the **same X11 display** (`:0`) through the **same executor**
(`handle_computer_use` in `tools/computer_use.py`) and the **same screen
grounding** (`pipeline/computer_use_vision.py`). X11 only — no Wayland.

---

## 2. The tool surface: a custom SOM schema (NOT native `computer_20251124`)

The single source of truth is `COMPUTER_USE_SCHEMA` in
`tools/computer_use.py` — a **custom function-calling tool** named
`computer_use`. It is deliberately **not** Anthropic's native
`computer_20251124` tool type. Rationale (2026-06-20 multi-provider design):
native CU returns pixel coordinates at a scaled resolution, forcing a
coordinate-scaling code path; the custom **Set-of-Marks (SOM) element mode**
is coordinate-free and identical across providers, so one executor serves
Claude, GPT-5.5, and Gemini unchanged.

**Action enum:** `capture`, `click`, `type`, `key`, `scroll`, `focus_app`,
`list_apps`, `drag`.

**Targeting (two ways):**
- **Element mode (preferred, most reliable):** `capture` with `mode="som"`
  paints numbered overlays on windows; act with `element=N` (or
  `from_element`/`to_element` for drag). 1-based index from the last SOM
  capture. Coordinate-free.
- **Pixel fallback:** `capture` with `mode="vision"` (raw screen), then act
  with `coordinate=[x, y]`. `mode="ax"` exposes the accessibility tree.

**Key conveniences baked in:**
- After every *mutating* action the post-action screen is **auto-captured**
  and attached to the next turn — the model shouldn't spend a `capture` call
  just to see the result.
- `region=[x1,y1,x2,y2]` re-captures a sub-rectangle at full 1:1 resolution so
  small text (file names, line numbers, menu labels) is legible.
- `focus_app` only *activates an existing* window (`wmctrl -a`). To launch a
  new app, use the `terminal` tool (`setsid <app> &`) first, then come back.

---

## 3. Workflow — the vision→plan→act loop

Identical shape on both surfaces:

```
SEE     capture (som | vision | ax)  → screenshot (+ numbered overlays / AX tree)
PLAN    model emits a computer_use tool call
GATE    blocklist  →  permission (tier / voice-confirm / in-chat card)
ACT     handle_computer_use → xdotool / wmctrl on :0
OBSERVE post-action screen auto-captured, fed back
repeat  until the model stops emitting tool calls (or a cap fires)
```

**Web sidecar specifics** (`computer_use_service.py::run_loop`):
- `MAX_STEPS=30` (env `JARVIS_COMPUTER_USE_WEB_MAX_STEPS`).
- A `CUAdapter` per provider owns the provider-native message format; the loop
  is provider-agnostic. `provider_for(model)` selects it; `make_adapter` builds
  it; `available_providers()` reports which have keys.
- History is trimmed (image-free) and can be re-imported across requests in a
  session.
- **SSE frames emitted:** `start`, `text`, `action`, `permission_request`,
  `blocked`, `denied`, `ping` (keep-alive), `done`, `error`. The page renders
  each as a chat part (the `permission_request` becomes an Approve / For
  session / Deny card).

---

## 4. Safety model (three independent layers)

1. **Sensitive-app blocklist** — a hard floor, enforced regardless of any
   supervised/auto mode. Default substrings: `bank, paypal, venmo, cash app,
   coinbase, binance, kraken, metamask, crypto, wallet, 1password, bitwarden,
   keepass, lastpass, dashlane`. Extend via
   `JARVIS_COMPUTER_USE_APP_BLOCKLIST` (comma-separated). Voice tool +
   sidecar both honor it.
2. **Permission tier** (`JARVIS_COMPUTER_USE_TIER`, default `full`):
   `view` (read-only captures), `interact` (no destructive/launch), `full`
   (everything). Blocks actions above the tier with a clear error.
3. **Per-action approval:**
   - *Web:* the page's "Supervised" toggle requests an approval card per
     action *kind* (type / click / key / …); "Auto" skips it but the blocklist
     still applies.
   - *Voice:* destructive verbs + password/2FA screens route to a voice
     confirm; default-deny on timeout/ambiguity.

Every action is audited (redacted params) to the telemetry DB; the web sidecar
additionally streams a human summary per action.

---

## 5. Model surface (verified 2026-06-20, 2 days before this doc)

Don't trust training data for model names — these were verified online per the
"never assume" rule and live in the 2026-06-20 multi-provider spec.

| Model id | Provider | Notes |
|---|---|---|
| `claude-sonnet-4-6` | Anthropic | sidecar default; strongest on OSWorld (~72.7%) |
| `claude-opus-4-8` | Anthropic | most capable |
| `claude-opus-4-7` | Anthropic | allowed (sidecar set) |
| `claude-haiku-4-5` | Anthropic | fastest / weakest tier |
| `gpt-5.5`, `gpt-5.5-pro` | OpenAI | agentic; via Responses API; multimodal |
| `gemini-3-flash-preview` | Google | computer use built in |

Each provider is gated on its key (`ANTHROPIC_API_KEY` / `OPENAI_API_KEY` /
`GEMINI_API_KEY`|`GOOGLE_API_KEY`, loaded from `~/.jarvis/keys.env`).
`/health` reports availability so the page dims models whose key is absent.
All are driven through the **uniform SOM custom-tool path** — native
per-provider CU is a future per-adapter upgrade, not what runs today.

---

## 6. Operating it

| | |
|---|---|
| Sidecar service | `systemctl --user {status,restart} jarvis-computer-use.service` (`setup/systemd/jarvis-computer-use.service`, auto-start) |
| Sidecar port | `:8771` (env `JARVIS_COMPUTER_USE_WEB_PORT`) |
| Stream | `x11vnc` + `websockify` on `:6080`, viewed via noVNC in the page |
| Web status probe | `GET /api/computer-use` → `{ ready, streamUp, sidecarUp, providers, wsUrl, … }` |
| Default model | `JARVIS_COMPUTER_USE_WEB_MODEL` (default `claude-sonnet-4-6`) |
| Step cap | `JARVIS_COMPUTER_USE_WEB_MAX_STEPS` (default 30) |
| Tier | `JARVIS_COMPUTER_USE_TIER` = `view` \| `interact` \| `full` |
| Display | X11 `:0` only; headless/CI → the registry `check_fn` registers the tool inert |

The voice tool needs no separate service — it's a registry tool inside the
voice-agent worker, gated by `check_computer_use_requirements()` (reachable
`$DISPLAY` + `xdotool`).

---

## 7. Documentation truth

Reconciling the historical docs against the running system. **Fix the doc, not
the code** — the code is correct; these are stale descriptions.

- **`docs/superpowers/specs/2026-05-18-jarvis-computer-use-parity-design.md`** —
  **architecture superseded.** It describes a `HandoffSubagent` /
  `transfer_to_computer_use` design with separate files (`subagents/computer_use.py`,
  `tools/computer_loop.py`, `computer_atspi.py`, `computer_safety.py`,
  `computer_backend.py`) and native `computer_20251124`. The 2026-05-20 rebuild
  tore down the entire subagent layer (see `.claude/rules/voice-agent.md`); CU is
  now a **single direct registry tool** with a custom SOM schema. The spec is a
  useful record of the *loop shape and safety thinking*, but its file map and
  handoff mechanics no longer match reality. A status banner has been added to
  its top pointing here.

- **`CLAUDE.md`** says the voice tool uses "Anthropic's `computer_20251124` tool
  surface." **Inaccurate for the current implementation** — it's a custom
  `computer_use` SOM-element schema (coordinate-free, provider-agnostic), per
  the 2026-06-20 decision. Not corrected here because CLAUDE.md is on the
  auto-mod hard blocklist and is load-bearing; flagged for the user to amend.

- **`computer_use_service.py`** — the `_ALLOWED_MODELS` comment says
  cross-provider CU "needs separate loop backends (tracked elsewhere)." Stale:
  the `pipeline/cu_adapters/` backends (`anthropic`/`openai`/`gemini`) **are**
  wired (`make_adapter`/`provider_for`). Comment-only drift; behavior is correct.

- **Accurate, no action:** `docs/superpowers/specs/2026-06-20-multi-provider-computer-use-design.md`
  (matches the deployed adapter design) and
  `docs/superpowers/specs/2026-05-30-computer-use-vision-feedback-design.md`
  (SOM / vision-feedback).

---

## 8. Open gaps (feed the blocked backlog)

- **#45 UI polish** (blocked on screenshots): the page already has model picker,
  supervised/auto, take-control, connect/disconnect, in-chat approval cards, and
  example prompts. Polish candidates to confirm against a live screenshot:
  permission-card affordance, the takeover banner, and the "stream not ready"
  empty state.
- **#46 E2E** (blocked on live-drive): auth half done — the page calls
  `/api/computer-use` same-origin (covered by the proxy carve-out, now
  session-tied); the sidecar `:8771` is reached host-side by the route, not the
  browser. Remaining: a live drive of a real task end-to-end.
- **#50 CLI computer use** (`src/cli`, blocked on sign-off): the natural path is
  to reuse `handle_computer_use` + the SOM schema behind a CLI command, exactly
  as the sidecar does — no new executor needed.

---

## 9. Source map

| Concern | File |
|---|---|
| Tool schema + executor + safety/tier/blocklist + audit | `src/voice-agent/tools/computer_use.py` |
| X11 backend (screenshot / xdotool / wmctrl) | `src/voice-agent/tools/computer_use_backend.py` |
| SOM / vision / AX screen grounding | `src/voice-agent/pipeline/computer_use_vision.py` |
| Web sidecar (loop, SSE, approval, health) | `src/voice-agent/computer_use_service.py` |
| Per-provider adapters | `src/voice-agent/pipeline/cu_adapters/{base,anthropic_adapter,openai_adapter,gemini_adapter}.py` |
| Web page | `src/web/src/app/(app)/computer-use/page.tsx` |
| Web route (SSE proxy) + approve | `src/web/src/app/api/computer-use/{route,approve/route}.ts` |
| noVNC view | `src/web/src/components/computer-use/novnc-view.tsx` |
| Sidecar service unit | `setup/systemd/jarvis-computer-use.service` |
| Historical design (superseded arch) | `docs/superpowers/specs/2026-05-18-jarvis-computer-use-parity-design.md` |
| Current multi-provider design | `docs/superpowers/specs/2026-06-20-multi-provider-computer-use-design.md` |
