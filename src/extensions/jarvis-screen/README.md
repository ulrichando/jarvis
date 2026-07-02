# Jarvis in Chrome (`jarvis-screen` extension, v3.0)

A Manifest V3 Chrome extension that lets Jarvis act on web pages. It's the
browser-side counterpart to the local **bridge** (`src/cli/src/bridge/`): the
service worker holds a WebSocket to `127.0.0.1:8765`, receives deterministic
commands from Jarvis, runs them in the active tab, and returns the result.

```
voice agent / web  ──▶  bridge /api/ext_browse  ──WS──▶  background.js
                                                              │ safety gate
                                   chrome.tabs/cookies ◀──────┤
                                   content.js (DOM) ◀─────────┘
```

## Architecture

| File | Role |
|---|---|
| `manifest.json` | MV3 manifest — permissions, content scripts, side panel |
| `background.js` | Service worker: WS to the bridge, reconnect, **safety gate**, dispatch (tab/history/screenshot/cookies here; DOM actions forwarded to the tab) |
| `content.js` | Runs in the page; routes a forwarded `{action,args}` to a handler |
| `actions.js` | 16 content-side DOM handlers (get_url, extract_text, find_by_text, dom_summary, click, right_click, hover, drag, select, type, fill_form, submit, press_key, scroll, wait_for, close_tab) |
| `safety.js` | `isDestructive()` + `gate()` — destructive clicks, credential fields, payment submits, and power tools require `confirmed:true` |
| `side_panel.{html,js}` | Connection status, token pairing, recent-action log |

Bridge protocol (already implemented in `ext_browse.ts` + `server.ts`):
- Connect: `ws://127.0.0.1:8765/ws?token=<TOKEN>`
- Register: send `{type:"extension_hello", version, token:<TOKEN>}` → `extension_hello_ack`
- Command in: `{cmd_id, action, args, confirmed}` → Reply out: `{cmd_id, ok, ...}`

## Install (unpacked — not on the Web Store)

1. Start the Jarvis desktop app so the bridge is running (`pgrep -fa bridge/server.ts`).
2. Chrome → `chrome://extensions` → enable **Developer mode** → **Load unpacked** →
   select this folder (`src/extensions/jarvis-screen/`).
3. Open the extension's **side panel** and paste the bridge token — the value of
   `JARVIS_LOCAL_API_TOKEN` from `~/.jarvis/local-api-token.env`. Click **Connect**.
   The status pill goes green ("Connected"). The token is stored in
   `chrome.storage.local` and used only to authenticate to the local bridge.

## Smoke test

```bash
curl -s http://127.0.0.1:8765/api/ext_status
# {"connected":true}

curl -s -X POST http://127.0.0.1:8765/api/ext_browse \
  -H 'content-type: application/json' \
  -d '{"action":"get_url"}'
# {"cmd_id":"…","ok":true,"url":"…","title":"…"}
```

## Tests

```bash
cd tests && npm install && npx jest    # 26 tests (jsdom): actions + safety
```

## Safety

- Destructive commands (delete/purchase/unsubscribe clicks, password/otp/cvv
  typing, payment-form submits) and power tools return `needs_confirmation`
  until Jarvis re-sends with `confirmed:true`.
- `exec_js` (arbitrary in-page eval) is **not** implemented in v1 — parked
  behind a future explicit opt-in.
- The bridge requires the local token both on the WS upgrade and in
  `extension_hello`, so a rogue local process can't impersonate the extension.

## Not yet (v2)

- Publishing to the Chrome Web Store (currently unpacked/dev only).
- The rich side-panel chat UX + onboarding + workflow-teaching (mirrors
  "Claude in Chrome"); v1 is the functional command channel + status panel.
- `exec_js` main-world execution via `chrome.scripting`.
- `accept_dialog` (needs `chrome.debugger`).
