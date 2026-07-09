# Jarvis-in-Chrome as MCP — design

**Date:** 2026-07-09
**Status:** Approved design (Fable-reviewed), pending implementation plan
**Branch:** `feat/jarvis-in-chrome-mcp`

## Problem

JARVIS's real browser plugin is the **`jarvis-screen` extension** (`src/extensions/jarvis-screen/`),
which connects over WebSocket to the local **Jarvis bridge** (`ws://127.0.0.1:8765`, started by the
desktop app), authed via 0wlan.com → proxy JWT. The bridge relays low-level commands to the extension
via `sendExtCommand(action, args, confirmed)` and exposes `POST /api/ext_browse`
(`src/cli/src/bridge/ext_browse.ts`). Today only the bridge's own `browserAgent` NL loop drives this;
**the jarvis CLI agent has no browser tools.**

Separately, the CLI carries the *ported upstream* "Claude in Chrome" feature (`--chrome`,
`src/cli/src/utils/jarvisInChrome/`, MCP server `claude-in-chrome`). It is gated behind
`isClaudeAISubscriber()` (always false for self-hosted JARVIS), points at Anthropic
(`claude.ai/chrome`, `wss://bridge.claudeusercontent.com`), and its in-process branch imports a
**non-existent** package `@ant/claude-for-chrome-mcp`. It is effectively dead for JARVIS.

**Goal:** give the jarvis CLI agent fine-grained MCP browser tools that drive the *jarvis-screen*
extension through the Jarvis bridge, reachable via the documented `--chrome`/`/chrome` surface, and
pointed at JARVIS infrastructure (0wlan.com), not Anthropic.

## Non-goals

- The upstream `jarvisInChrome/` path — left untouched (it is "reserved" per `.claude/rules/cli.md`).
- Phase-2 cloud relay implementation (spec'd at a high level here; its own sub-spec later).
- The other docs-parity audit defects (`/schedule`, keybindings, memory-extractor, etc.).

## Key facts established during review (Fable, with source)

1. **The bridge is a *separate process*** from the CLI agent — launched by
   `bin/_internal/start-desktop.sh:253` (`bun server.ts &`), dies with the desktop. Only that process
   gets `JARVIS_LOCAL_API_TOKEN` (start-desktop.sh:72-88). The CLI's own launcher does **not** load it.
2. **Bridge auth** (`server.ts:147-160`): `Authorization: Bearer <cred>` where `<cred>` is the local
   token (`JARVIS_LOCAL_API_TOKEN`) OR a proxy JWT. `/api/ext_browse` is not public (server.ts:82);
   fail-closed unless `JARVIS_BRIDGE_INSECURE=1`.
3. **`/api/ext_browse` contract** (`ext_browse.ts:70-96`): `POST {action, args, confirmed, timeout_ms?}`
   → `sendExtCommand` → extension → `{ok, ...}` JSON. **503** = extension not connected, **504** =
   timeout, **500** = other, **400** = bad request. Default timeout 10s.
4. **Confirmation/blocked come back as HTTP 200** with `{ok:false, needs_confirmation:true}` /
   `{ok:false, site_blocked:true}` — must be handled at the body level, not as HTTP errors.
5. **MCP registration** (`services/mcp/types.ts:124-135`, `client.ts`): no generic "in-process" config
   type. In-process servers are *name-keyed* special cases in `connectToServer` (`client.ts:905-924`
   for `claude-in-chrome`, `925-943` for computer-use), using `InProcessTransport.createLinkedTransportPair()`.
   A server named `jarvis-in-chrome` needs its **own new branch**.
6. **`isSelfHostedBridge()`** (`bridgeEnabled.ts:37`) = `!!process.env.JARVIS_BRIDGE_BASE_URL` (the
   0wlan.com CCR base, persisted by `jarvis auth login` into `~/.jarvis/keys.env`, loaded by
   start-env.sh:56-58). It is the *entitlement* signal — **not** proof the local 8765 bridge is running.
7. **The proven tool surface already exists** at `src/cli/src/bridge/browserAgent.ts:30-52` — 22 tools
   (get_url … download) with descriptions + JSON schemas + a SYSTEM prompt, matching the extension's
   handlers 1:1.

## Architecture

A new **in-CLI, in-process MCP server** `jarvis-in-chrome`. Each tool call is forwarded as
`POST {action, args, confirmed}` to the Jarvis bridge `/api/ext_browse`, which relays it to the
jarvis-screen extension and returns the result. No new browser protocol.

```
CLI agent → mcp__jarvis-in-chrome__<tool>(args)
          → jarvisInChromeMcpServer (in-process MCP)
          → resolveChromeBridge() → POST {action,args,confirmed} to baseUrl/api/ext_browse  (Bearer token)
          → bridge sendExtCommand → WS → jarvis-screen (active tab) → {ok,...}
          → MCP tool result
```

### Components (each: purpose / interface / deps)

1. **`src/cli/src/bridge/browserTools.ts`** *(new, shared)* — extract the `TOOLS` array + `SYSTEM`
   string from `browserAgent.ts` into an exported module; `browserAgent.ts` re-imports them. Single
   source of truth for the browser tool surface. *(Additive refactor — both callers want the same set;
   no behavior change.)*
2. **`resolveChromeBridge()`** *(new, tiny)* — returns `{ baseUrl, token }`.
   - `baseUrl` = `JARVIS_CHROME_BRIDGE_URL` (override) → `http://127.0.0.1:8765` (phase 1 local).
   - `token` = parsed from `~/.jarvis/local-api-token.env` (`JARVIS_LOCAL_API_TOKEN=<value>`, chmod 600).
     Reuse the regex-parse precedent at `src/web/src/app/api/chrome/status/route.ts:34-45`.
   - File absent → structured "desktop-not-launched" signal.
3. **`jarvisInChromeMcpServer.ts`** *(new)* — builds an `@modelcontextprotocol/sdk` `Server` exposing
   `browserTools`. Each tool handler: `resolveChromeBridge()` → `fetch(POST /api/ext_browse)` →
   map result. Handles the error classes + confirmation semantics + screenshot data-URL (below).
4. **`setupJarvisInChrome()`** *(new)* — returns
   `{ mcpConfig: { 'jarvis-in-chrome': <in-process marker config> }, allowedTools: ['mcp__jarvis-in-chrome__<tool>'…], systemPrompt }`.
   Modeled on `setupClaudeInChrome()`'s return shape (consumed unchanged at main.tsx:2434-2448) but with
   the jarvis-screen surface and **no** native-messaging-host installation.
5. **`services/mcp/client.ts`** — new name-keyed in-process branch for `jarvis-in-chrome`
   (mirror the computer-use branch at `client.ts:925-943`), wiring `InProcessTransport` to the server
   from (3).
6. **`main.tsx` enable block (~2418-2462)** — branch the enablement:
   `if (enableChrome && isSelfHostedBridge()) → setupJarvisInChrome()` (new); the upstream
   `setupClaudeInChrome()` path remains only for a genuine claude.ai subscriber. The gate edits
   (already in tree) stay but now route to the new setup.
7. **`commands/chrome/chrome.tsx`** — `/chrome` menu: install → `https://0wlan.com/extension`,
   status + detection via bridge `/api/ext_status` (live truth, not the Anthropic-ID disk scan), drop
   the subscriber gate (edit already in tree). Reconnect/manage-permissions point at the extension.

### Tool surface

Port the **22 proven tools** verbatim from `browserAgent.ts` (via the shared `browserTools.ts`):
`get_url, dom_summary, extract_text, find_by_text, read_console, read_network, navigate, click,
click_text, type, fill_form, submit, select, scroll, press_key, wait_for, back, forward, list_tabs,
activate_tab, group_tabs, download`.

- **Optional add:** `screenshot` (extension supports it; returns a **data-URL** in `image_b64` —
  strip the `data:image/png;base64,` prefix and emit an MCP image block). Marked as beyond the proven
  set; include if cheap.
- **Excluded:** `get_cookies`/`set_cookies` (always-confirm, security), `record_start`/`record_stop`
  (panel-only), `title` (folded into `get_url`). `right_click`/`hover`/`drag` exist in the extension
  but are outside the proven browserAgent set — defer unless needed.
- Destructive/mutating tools pass `confirmed` through to the bridge.

### Transport + auth

- Phase 1: `http://127.0.0.1:8765/api/ext_browse`, `Authorization: Bearer <local token from file>`.
- Phase 2 (deferred): `https://0wlan.com/api/ext_browse` via a cloud rendezvous relay (see below).
- `JARVIS_CHROME_BRIDGE_URL` overrides the base for testing/advanced setups.

### Error handling

| Condition | Detection | Tool result |
|---|---|---|
| Bridge not running | `fetch` `ECONNREFUSED` / connect error | "Jarvis-in-Chrome bridge isn't running — start the Jarvis desktop app." |
| Extension not connected | HTTP 503 | "Browser extension not connected — open your browser with the jarvis-screen extension." |
| Timeout | HTTP 504 | "Browser action timed out." |
| Needs confirmation | HTTP 200 `{ok:false, needs_confirmation:true}` | structured "requires confirmation" — agent re-issues with `confirmed:true` (mutating tools expose a `confirmed` param). |
| Site blocked | HTTP 200 `{ok:false, site_blocked:true}` | "This site is blocked by your extension policy." |
| Token file absent | `resolveChromeBridge` | same as bridge-not-running (desktop never launched). |
| Feature off / not self-hosted | gate | server not registered; tools absent. |

## Sequencing (load-bearing)

**The gate edits must NOT be committed alone.** On their own they light up the *broken* upstream
`claude-in-chrome` path (missing `@ant` package + Anthropic native-messaging manifests written to disk).
The gate edits + `setupJarvisInChrome` + the `client.ts` branch land in the **same change**.

## Phase 2 — cloud relay (deferred, net-new)

For the web/cloud CLI (jarvis-web container / teleport) to reach the user's *local* browser:
1. A rendezvous relay service on 0wlan.com (WS sidecar in the style of `src/web/scripts/pty-server.mjs`;
   Next route handlers cannot hold the persistent socket), keyed by user.
2. An outbound transport from the extension (second WS to `wss://0wlan.com/...`) or the local bridge
   dialing out.
3. Auth: proxy-JWT audience for the relay; `proxy.ts` SELF_AUTH allowlist entry (new CLI→server routes
   401 only-online without it — cf. remote-control-proxy-selfauth-gap / PR #115); CF Access exclusion.
4. Security review: this is remote control of the user's browser via the cloud — real takeover surface.

## Testing

- **Unit** (mock `fetch`): each tool maps to the correct `{action,args}` POST; error mapping
  (ECONNREFUSED / 503 / 504 / 500 / `needs_confirmation` / `site_blocked`); token-file parse;
  screenshot data-URL prefix strip. One runnable `test_*.ts` / assert-based self-check.
- **Live** (the "test it, don't just compile" bar): desktop bridge + jarvis-screen loaded, then
  `bin/jarvis --chrome -p "navigate to example.com and read the page title"` → confirm the tool fires,
  hits the extension, and returns. Also `/chrome` shows the actionable menu (no subscription error) and
  correct connected/not-connected status from `/api/ext_status`.

## Open questions — resolved

- **Wire name:** `jarvis-in-chrome` (fresh), per the user's "jarvis in chrome, not claude in chrome."
  The new server talks the bridge command protocol, not the `claude-in-chrome` MCP wire protocol, so
  there is no protocol-stability reason to keep the old name. New name-keyed branch in `client.ts`.
- **Reuse vs new:** new `setupJarvisInChrome` (do NOT reuse the ported `setupClaudeInChrome` internals —
  its tool list, prompt, and native-host install are all wrong for jarvis-screen).
