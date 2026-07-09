# Jarvis-in-Chrome as MCP — Implementation Plan (Phase 1: local)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the jarvis CLI agent fine-grained MCP browser tools that drive the jarvis-screen extension through the local Jarvis bridge (`POST /api/ext_browse`), reachable via `--chrome`/`/chrome`.

**Architecture:** A new in-process MCP server `jarvis-in-chrome` exposes the 22 proven browser tools (shared from `bridge/browserAgent.ts`). Each tool call → `POST {action,args,confirmed}` to the bridge → jarvis-screen. Registered via a name-keyed in-process branch in `services/mcp/client.ts`, mirroring the existing computer-use branch. Enabled when `isSelfHostedBridge()`. The upstream `jarvisInChrome/` path is left untouched.

**Tech Stack:** TypeScript, Bun, `@modelcontextprotocol/sdk`, `bun:test`.

**Spec:** `docs/superpowers/specs/2026-07-09-jarvis-in-chrome-mcp-design.md`

**Branch:** `feat/jarvis-in-chrome-mcp` (already created; gate edits to `main.tsx`/`setup.ts`/`chrome.tsx` already in the working tree — they land with this change, never before it).

---

## File Structure

- **Create** `src/cli/src/bridge/browserTools.ts` — exported `TOOLS` + `SYSTEM` (moved from `browserAgent.ts`). Single source of truth for the browser tool surface.
- **Modify** `src/cli/src/bridge/browserAgent.ts` — import `TOOLS`/`SYSTEM` from `browserTools.ts` instead of defining them.
- **Create** `src/cli/src/utils/jarvisChromeMcp/resolveChromeBridge.ts` — resolve `{baseUrl, token}` (token from `~/.jarvis/local-api-token.env`).
- **Create** `src/cli/src/utils/jarvisChromeMcp/extBrowseClient.ts` — `callExtBrowse(action, args, confirmed, fetchImpl?)` with the full error-class mapping.
- **Create** `src/cli/src/utils/jarvisChromeMcp/server.ts` — `createJarvisInChromeMcpServer()` (in-process MCP `Server`) + `isJarvisInChromeMCPServer(name)`.
- **Create** `src/cli/src/utils/jarvisChromeMcp/setup.ts` — `setupJarvisInChrome()` → `{mcpConfig, allowedTools, systemPrompt}`.
- **Modify** `src/cli/src/services/mcp/client.ts` — new name-keyed in-process branch for `jarvis-in-chrome`.
- **Modify** `src/cli/src/main.tsx` — branch the `--chrome` enablement to `setupJarvisInChrome()` when `isSelfHostedBridge()`.
- **Modify** `src/cli/src/commands/chrome/chrome.tsx` — repoint install URL → 0wlan.com/extension; detection via bridge `/api/ext_status`.
- **Tests:** colocated `*.test.ts` next to each new file. Run with `bun test <file>`.

Constant: MCP server name `'jarvis-in-chrome'`. The MCP tool the agent sees is `mcp__jarvis-in-chrome__<tool>`.

---

## Task 0: Reconcile the in-tree gate edits

Three uncommitted gate edits are already in the working tree from the earlier chrome investigation. Reconcile them with the design before starting:

- **Keep** `src/cli/src/main.tsx` (the `isSelfHostedBridge()` disjunct at ~2424) — needed for Task 7; commits in Task 7.
- **Keep** `src/cli/src/commands/chrome/chrome.tsx` (the `isSubscriber = isSelfHostedBridge() || isClaudeAISubscriber()` at ~282) — needed for Task 8; commits in Task 8.
- **Revert** `src/cli/src/utils/jarvisInChrome/setup.ts` — this touches the *reserved* `jarvisInChrome/` dir (spec non-goal) and its auto-enable disjunct is inert (also requires the Anthropic-ID extension scan). Phase 1 uses the explicit `--chrome` flag path, which works via the `main.tsx` edit alone.

- [ ] **Step 1: Revert the reserved-dir edit**

Run: `cd /home/ulrich/Documents/Projects/jarvis && git checkout -- src/cli/src/utils/jarvisInChrome/setup.ts`
Expected: `git status --short src/cli` now lists only `main.tsx` and `commands/chrome/chrome.tsx` as modified.

---

## Task 1: Extract the shared browser tool surface

**Files:**
- Create: `src/cli/src/bridge/browserTools.ts`
- Modify: `src/cli/src/bridge/browserAgent.ts:30-70` (the `const TOOLS = [...]` and `const SYSTEM = \`...\`` blocks)
- Test: `src/cli/src/bridge/browserTools.test.ts`

- [ ] **Step 1: Write the failing test**

```ts
// src/cli/src/bridge/browserTools.test.ts
import { test, expect } from 'bun:test'
import { TOOLS, SYSTEM } from './browserTools.js'

test('TOOLS has the 22 proven browser actions with unique names + valid schemas', () => {
  expect(TOOLS.length).toBe(22)
  const names = TOOLS.map(t => t.name)
  expect(new Set(names).size).toBe(22) // unique
  expect(names).toContain('navigate')
  expect(names).toContain('list_tabs')
  expect(names).not.toContain('title') // folded into get_url
  for (const t of TOOLS) {
    expect(typeof t.name).toBe('string')
    expect(typeof t.description).toBe('string')
    expect(t.input_schema.type).toBe('object')
  }
})

test('SYSTEM prompt mentions the tab-workflow guidance', () => {
  expect(SYSTEM).toContain('list_tabs')
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/cli && bun test src/bridge/browserTools.test.ts`
Expected: FAIL — cannot resolve `./browserTools.js`.

- [ ] **Step 3: Create `browserTools.ts` by moving the constants out of `browserAgent.ts`**

Cut the `const TOOLS = [ ... ]` array (currently `browserAgent.ts:30-52`) and the `const SYSTEM = \`...\`` template (currently starting `browserAgent.ts:54`) VERBATIM into the new file and export them:

```ts
// src/cli/src/bridge/browserTools.ts
// The browser surface exposed to a model, shared by the bridge's browserAgent
// (NL loop) and the CLI's jarvis-in-chrome MCP server. Mirrors the jarvis-screen
// extension's handlers 1:1; args match ext_browse.
export const TOOLS = [
  // ... paste the exact 22-entry array from browserAgent.ts (get_url … download) ...
] as const

export const SYSTEM = `...paste the exact SYSTEM template from browserAgent.ts...`
```

Do not edit the array contents — this is a move, not a rewrite.

- [ ] **Step 4: Point `browserAgent.ts` at the shared module**

In `src/cli/src/bridge/browserAgent.ts`, delete the moved `const TOOLS`/`const SYSTEM` and add at the top with the other imports:

```ts
import { TOOLS, SYSTEM } from './browserTools.js'
```

- [ ] **Step 5: Run tests + transpile-check both files**

Run: `cd src/cli && bun test src/bridge/browserTools.test.ts && bun build src/bridge/browserAgent.ts --no-bundle --outdir /tmp/ck`
Expected: test PASS; "Transpiled file" with no errors.

- [ ] **Step 6: Commit**

```bash
git add src/cli/src/bridge/browserTools.ts src/cli/src/bridge/browserAgent.ts src/cli/src/bridge/browserTools.test.ts
git commit -m "refactor(cli): extract shared browser TOOLS/SYSTEM into browserTools.ts"
```

---

## Task 2: Bridge transport resolver

**Files:**
- Create: `src/cli/src/utils/jarvisChromeMcp/resolveChromeBridge.ts`
- Test: `src/cli/src/utils/jarvisChromeMcp/resolveChromeBridge.test.ts`

- [ ] **Step 1: Write the failing test**

```ts
// resolveChromeBridge.test.ts
import { test, expect } from 'bun:test'
import { parseLocalApiToken, resolveBaseUrl } from './resolveChromeBridge.js'

test('parseLocalApiToken extracts the token from the env-file body', () => {
  expect(parseLocalApiToken('JARVIS_LOCAL_API_TOKEN=abc123\n')).toBe('abc123')
  expect(parseLocalApiToken('# comment\nJARVIS_LOCAL_API_TOKEN="q w"\n')).toBe('q w')
  expect(parseLocalApiToken('nothing here')).toBeNull()
})

test('resolveBaseUrl honors the override then defaults to the local bridge', () => {
  expect(resolveBaseUrl({ JARVIS_CHROME_BRIDGE_URL: 'http://x:9' })).toBe('http://x:9')
  expect(resolveBaseUrl({})).toBe('http://127.0.0.1:8765')
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/cli && bun test src/utils/jarvisChromeMcp/resolveChromeBridge.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement**

```ts
// src/cli/src/utils/jarvisChromeMcp/resolveChromeBridge.ts
import { readFileSync } from 'fs'
import { homedir } from 'os'
import { join } from 'path'

const TOKEN_FILE = join(homedir(), '.jarvis', 'local-api-token.env')

/** Extract JARVIS_LOCAL_API_TOKEN=<value> from an env-file body (quotes optional). */
export function parseLocalApiToken(body: string): string | null {
  const m = body.match(/^\s*JARVIS_LOCAL_API_TOKEN\s*=\s*(.+?)\s*$/m)
  if (!m) return null
  return m[1].replace(/^["']|["']$/g, '')
}

export function resolveBaseUrl(env: Record<string, string | undefined> = process.env): string {
  return env.JARVIS_CHROME_BRIDGE_URL || 'http://127.0.0.1:8765'
}

export type ChromeBridge = { baseUrl: string; token: string }

/** Throws a bridge-not-running error if the desktop never wrote the token file. */
export function resolveChromeBridge(): ChromeBridge {
  const baseUrl = resolveBaseUrl()
  let token: string | null = null
  try {
    token = parseLocalApiToken(readFileSync(TOKEN_FILE, 'utf8'))
  } catch {
    token = null
  }
  if (!token) {
    const e = new Error('BRIDGE_DOWN')
    ;(e as any).code = 'BRIDGE_DOWN'
    throw e
  }
  return { baseUrl, token }
}
```

- [ ] **Step 4: Run tests**

Run: `cd src/cli && bun test src/utils/jarvisChromeMcp/resolveChromeBridge.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cli/src/utils/jarvisChromeMcp/resolveChromeBridge.ts src/cli/src/utils/jarvisChromeMcp/resolveChromeBridge.test.ts
git commit -m "feat(cli): resolveChromeBridge — local bridge base + token-file auth for jarvis-in-chrome"
```

---

## Task 3: The ext_browse client + error mapping

**Files:**
- Create: `src/cli/src/utils/jarvisChromeMcp/extBrowseClient.ts`
- Test: `src/cli/src/utils/jarvisChromeMcp/extBrowseClient.test.ts`

Error contract (from `ext_browse.ts`): `ECONNREFUSED`→bridge down, 503→ext not connected, 504→timeout, 500→error, and HTTP 200 `{ok:false, needs_confirmation}` / `{ok:false, site_blocked}`.

- [ ] **Step 1: Write the failing test**

```ts
// extBrowseClient.test.ts
import { test, expect } from 'bun:test'
import { callExtBrowse } from './extBrowseClient.js'

const bridge = { baseUrl: 'http://127.0.0.1:8765', token: 't' }

function fetchStub(status: number, body: any) {
  return async () => new Response(JSON.stringify(body), { status })
}

test('success returns the extension result', async () => {
  const r = await callExtBrowse('get_url', {}, false, bridge, fetchStub(200, { ok: true, url: 'https://x' }))
  expect(r.ok).toBe(true)
  expect((r.result as any).url).toBe('https://x')
})

test('503 maps to extension-not-connected', async () => {
  const r = await callExtBrowse('click', { selector: 'a' }, false, bridge, fetchStub(503, { ok: false, error: 'extension not connected' }))
  expect(r.ok).toBe(false)
  expect(r.kind).toBe('EXT_NOT_CONNECTED')
})

test('200 needs_confirmation maps to NEEDS_CONFIRMATION', async () => {
  const r = await callExtBrowse('navigate', { url: 'https://x' }, false, bridge, fetchStub(200, { ok: false, needs_confirmation: true }))
  expect(r.kind).toBe('NEEDS_CONFIRMATION')
})

test('connect error maps to BRIDGE_DOWN', async () => {
  const boom = async () => { throw new Error('ECONNREFUSED') }
  const r = await callExtBrowse('get_url', {}, false, bridge, boom)
  expect(r.kind).toBe('BRIDGE_DOWN')
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/cli && bun test src/utils/jarvisChromeMcp/extBrowseClient.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement**

```ts
// src/cli/src/utils/jarvisChromeMcp/extBrowseClient.ts
import type { ChromeBridge } from './resolveChromeBridge.js'

export type ExtResult =
  | { ok: true; result: unknown }
  | { ok: false; kind: 'BRIDGE_DOWN' | 'EXT_NOT_CONNECTED' | 'TIMEOUT' | 'NEEDS_CONFIRMATION' | 'SITE_BLOCKED' | 'ERROR'; message: string }

export async function callExtBrowse(
  action: string,
  args: Record<string, unknown>,
  confirmed: boolean,
  bridge: ChromeBridge,
  fetchImpl: typeof fetch = fetch,
): Promise<ExtResult> {
  let res: Response
  try {
    res = await fetchImpl(`${bridge.baseUrl}/api/ext_browse`, {
      method: 'POST',
      headers: { 'content-type': 'application/json', authorization: `Bearer ${bridge.token}` },
      body: JSON.stringify({ action, args, confirmed }),
    })
  } catch (e: any) {
    return { ok: false, kind: 'BRIDGE_DOWN', message: 'Jarvis-in-Chrome bridge is not running — start the Jarvis desktop app.' }
  }

  if (res.status === 503) return { ok: false, kind: 'EXT_NOT_CONNECTED', message: 'Browser extension not connected — open your browser with the jarvis-screen extension.' }
  if (res.status === 504) return { ok: false, kind: 'TIMEOUT', message: 'Browser action timed out.' }

  let body: any = null
  try { body = await res.json() } catch { /* fallthrough */ }

  if (res.status >= 500 || !body) return { ok: false, kind: 'ERROR', message: body?.error || `Bridge error (HTTP ${res.status}).` }

  // HTTP 200 body-level refusals:
  if (body.ok === false && body.needs_confirmation) return { ok: false, kind: 'NEEDS_CONFIRMATION', message: 'This action needs confirmation (it modifies the page or navigates). Re-run the tool with confirmed: true to proceed.' }
  if (body.ok === false && body.site_blocked) return { ok: false, kind: 'SITE_BLOCKED', message: 'This site is blocked by your extension policy.' }
  if (body.ok === false) return { ok: false, kind: 'ERROR', message: body.error || 'Browser action failed.' }

  return { ok: true, result: body }
}
```

- [ ] **Step 4: Run tests**

Run: `cd src/cli && bun test src/utils/jarvisChromeMcp/extBrowseClient.test.ts`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/cli/src/utils/jarvisChromeMcp/extBrowseClient.ts src/cli/src/utils/jarvisChromeMcp/extBrowseClient.test.ts
git commit -m "feat(cli): ext_browse client with bridge/ext/timeout/confirmation error mapping"
```

---

## Task 4: The in-process MCP server

**Files:**
- Create: `src/cli/src/utils/jarvisChromeMcp/server.ts`
- Test: `src/cli/src/utils/jarvisChromeMcp/server.test.ts`

- [ ] **Step 1: Write the failing test**

```ts
// server.test.ts
import { test, expect } from 'bun:test'
import { JARVIS_IN_CHROME_SERVER_NAME, isJarvisInChromeMCPServer, buildToolList } from './server.js'

test('name matcher recognizes the jarvis-in-chrome server only', () => {
  expect(isJarvisInChromeMCPServer(JARVIS_IN_CHROME_SERVER_NAME)).toBe(true)
  expect(isJarvisInChromeMCPServer('claude-in-chrome')).toBe(false)
})

test('buildToolList exposes 22 tools, each augmented with optional confirmed', () => {
  const tools = buildToolList()
  expect(tools.length).toBe(22)
  const nav = tools.find(t => t.name === 'navigate')!
  expect(nav.inputSchema.properties.confirmed).toEqual({ type: 'boolean', description: expect.any(String) })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/cli && bun test src/utils/jarvisChromeMcp/server.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement**

```ts
// src/cli/src/utils/jarvisChromeMcp/server.ts
import { Server } from '@modelcontextprotocol/sdk/server/index.js'
import { CallToolRequestSchema, ListToolsRequestSchema } from '@modelcontextprotocol/sdk/types.js'
import { TOOLS } from '../../bridge/browserTools.js'
import { resolveChromeBridge } from './resolveChromeBridge.js'
import { callExtBrowse } from './extBrowseClient.js'

export const JARVIS_IN_CHROME_SERVER_NAME = 'jarvis-in-chrome'

/** Exact normalized-name match, mirroring isComputerUseMCPServer. */
export function isJarvisInChromeMCPServer(name: string): boolean {
  return name === JARVIS_IN_CHROME_SERVER_NAME
}

const CONFIRMED_PROP = { type: 'boolean' as const, description: 'Set true to proceed past the extension safety prompt for a mutating action.' }

/** ListTools payload: the shared TOOLS, each augmented with an optional `confirmed`. */
export function buildToolList() {
  return TOOLS.map(t => ({
    name: t.name,
    description: t.description,
    inputSchema: {
      ...t.input_schema,
      properties: { ...t.input_schema.properties, confirmed: CONFIRMED_PROP },
    },
  }))
}

/** data:image/png;base64,XXXX -> XXXX (for MCP image blocks). */
function stripDataUrl(s: string): string {
  const i = s.indexOf('base64,')
  return i >= 0 ? s.slice(i + 'base64,'.length) : s
}

export async function createJarvisInChromeMcpServer(): Promise<Server> {
  const server = new Server(
    { name: JARVIS_IN_CHROME_SERVER_NAME, version: '1.0.0' },
    { capabilities: { tools: {} } },
  )

  server.setRequestHandler(ListToolsRequestSchema, async () => ({ tools: buildToolList() }))

  server.setRequestHandler(CallToolRequestSchema, async req => {
    const action = req.params.name
    const raw = (req.params.arguments ?? {}) as Record<string, unknown>
    const confirmed = raw.confirmed === true
    const { confirmed: _drop, ...args } = raw

    let bridge
    try {
      bridge = resolveChromeBridge()
    } catch {
      return { isError: true, content: [{ type: 'text', text: 'Jarvis-in-Chrome bridge is not running — start the Jarvis desktop app.' }] }
    }

    const r = await callExtBrowse(action, args, confirmed, bridge)
    if (!r.ok) return { isError: true, content: [{ type: 'text', text: r.message }] }

    // screenshot returns a data-URL in image_b64 -> emit an image block.
    const result: any = r.result
    if (action === 'screenshot' && typeof result?.image_b64 === 'string') {
      return { content: [{ type: 'image', data: stripDataUrl(result.image_b64), mimeType: 'image/png' }] }
    }
    return { content: [{ type: 'text', text: JSON.stringify(result) }] }
  })

  return server
}
```

Note: `screenshot` is included only if it was added to `TOOLS`; if not, this branch is inert. Keeping the handler is harmless.

- [ ] **Step 4: Run tests + transpile-check**

Run: `cd src/cli && bun test src/utils/jarvisChromeMcp/server.test.ts && bun build src/utils/jarvisChromeMcp/server.ts --no-bundle --outdir /tmp/ck`
Expected: test PASS (2 tests); transpile OK.

- [ ] **Step 5: Commit**

```bash
git add src/cli/src/utils/jarvisChromeMcp/server.ts src/cli/src/utils/jarvisChromeMcp/server.test.ts
git commit -m "feat(cli): in-process jarvis-in-chrome MCP server (ListTools + CallTool -> ext_browse)"
```

---

## Task 5: setupJarvisInChrome

**Files:**
- Create: `src/cli/src/utils/jarvisChromeMcp/setup.ts`
- Test: `src/cli/src/utils/jarvisChromeMcp/setup.test.ts`

The `mcpConfig` uses the same `type:'stdio'` marker shape as `setupClaudeInChrome` (`setup.ts:35-42`); the `client.ts` in-process branch (Task 6) intercepts it before any subprocess spawns. The `command`/`args` are an unreachable fallback.

- [ ] **Step 1: Write the failing test**

```ts
// setup.test.ts
import { test, expect } from 'bun:test'
import { setupJarvisInChrome } from './setup.js'
import { JARVIS_IN_CHROME_SERVER_NAME } from './server.js'

test('setupJarvisInChrome returns a dynamic in-process config + mcp__ allowedTools + prompt', () => {
  const s = setupJarvisInChrome()
  const cfg = s.mcpConfig[JARVIS_IN_CHROME_SERVER_NAME]
  expect(cfg.type).toBe('stdio')
  expect(cfg.scope).toBe('dynamic')
  expect(s.allowedTools).toContain(`mcp__${JARVIS_IN_CHROME_SERVER_NAME}__navigate`)
  expect(s.allowedTools.length).toBe(22)
  expect(s.systemPrompt.length).toBeGreaterThan(0)
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/cli && bun test src/utils/jarvisChromeMcp/setup.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement**

```ts
// src/cli/src/utils/jarvisChromeMcp/setup.ts
import type { ScopedMcpServerConfig } from '../../services/mcp/types.js'
import { TOOLS, SYSTEM } from '../../bridge/browserTools.js'
import { JARVIS_IN_CHROME_SERVER_NAME } from './server.js'

export function setupJarvisInChrome(): {
  mcpConfig: Record<string, ScopedMcpServerConfig>
  allowedTools: string[]
  systemPrompt: string
} {
  const allowedTools = TOOLS.map(t => `mcp__${JARVIS_IN_CHROME_SERVER_NAME}__${t.name}`)
  return {
    mcpConfig: {
      [JARVIS_IN_CHROME_SERVER_NAME]: {
        type: 'stdio' as const,
        command: process.execPath,
        args: ['--jarvis-in-chrome-mcp'], // unreachable: client.ts runs it in-process
        scope: 'dynamic' as const,
      },
    },
    allowedTools,
    systemPrompt: SYSTEM,
  }
}
```

If `ScopedMcpServerConfig` requires more fields than shown, copy the exact object shape from `setup.ts:35-42` (`jarvisInChrome/setup.ts`).

- [ ] **Step 4: Run tests**

Run: `cd src/cli && bun test src/utils/jarvisChromeMcp/setup.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cli/src/utils/jarvisChromeMcp/setup.ts src/cli/src/utils/jarvisChromeMcp/setup.test.ts
git commit -m "feat(cli): setupJarvisInChrome -> in-process MCP config + allowedTools + system prompt"
```

---

## Task 6: Register the in-process branch in client.ts

**Files:**
- Modify: `src/cli/src/services/mcp/client.ts` (in `connectToServer`, right after the computer-use branch at ~`:925-943`)

- [ ] **Step 1: Add the branch (mirrors the computer-use branch exactly)**

Insert, immediately after the `isComputerUseMCPServer` `else if` block closes:

```ts
      } else if (
        (serverRef.type === 'stdio' || !serverRef.type) &&
        isJarvisInChromeMCPServer(name)
      ) {
        // Run the Jarvis-in-Chrome MCP server in-process; its tools POST to the
        // local Jarvis bridge (/api/ext_browse) which drives the jarvis-screen extension.
        const { createJarvisInChromeMcpServer } = await import(
          '../../utils/jarvisChromeMcp/server.js'
        )
        const { createLinkedTransportPair } = await import(
          './InProcessTransport.js'
        )
        inProcessServer = await createJarvisInChromeMcpServer()
        const [clientTransport, serverTransport] = createLinkedTransportPair()
        await inProcessServer.connect(serverTransport)
        transport = clientTransport
        logMCPDebug(name, `In-process Jarvis-in-Chrome MCP server started`)
```

- [ ] **Step 2: Add the import at the top of client.ts**

```ts
import { isJarvisInChromeMCPServer } from '../../utils/jarvisChromeMcp/server.js'
```

- [ ] **Step 3: Transpile-check**

Run: `cd src/cli && bun build src/services/mcp/client.ts --no-bundle --outdir /tmp/ck`
Expected: "Transpiled file", no errors.

- [ ] **Step 4: Commit**

```bash
git add src/cli/src/services/mcp/client.ts
git commit -m "feat(cli): register jarvis-in-chrome as an in-process MCP server in client.ts"
```

---

## Task 7: Wire the --chrome enablement to setupJarvisInChrome

**Files:**
- Modify: `src/cli/src/main.tsx:2418-2462` (the `enableClaudeInChrome` block; gate edits already present in the tree)

- [ ] **Step 1: Branch the setup call**

The gate edit already makes `enableClaudeInChrome` true for self-hosted users. Change the `if (enableClaudeInChrome) { ... setupClaudeInChrome() ... }` body so that, when `isSelfHostedBridge()`, it calls `setupJarvisInChrome()` instead. Concretely, replace the two `setupClaudeInChrome()` call sites (main.tsx:2434 and the auto-enable path ~:2458) with:

```tsx
const chromeSetup = isSelfHostedBridge() ? setupJarvisInChrome() : setupClaudeInChrome();
const {
  mcpConfig: chromeMcpConfig,
  allowedTools: chromeMcpTools,
  systemPrompt: chromeSystemPrompt,
} = chromeSetup;
```

(Keep the existing `dynamicMcpConfig`/`allowedTools`/`appendSystemPrompt` merge that follows — it is unchanged. Apply the same swap to the auto-enable branch.)

- [ ] **Step 2: Add the import**

```tsx
import { setupJarvisInChrome } from "./utils/jarvisChromeMcp/setup.js";
```

`isSelfHostedBridge` is already imported (from the gate fix).

- [ ] **Step 3: Transpile-check**

Run: `cd src/cli && bun build src/main.tsx --no-bundle --outdir /tmp/ck`
Expected: "Transpiled file", no errors.

- [ ] **Step 4: Commit**

```bash
git add src/cli/src/main.tsx
git commit -m "feat(cli): --chrome enables the jarvis-in-chrome MCP server for self-hosted users"
```

---

## Task 8: Repoint the /chrome menu

**Files:**
- Modify: `src/cli/src/commands/chrome/chrome.tsx` (`CHROME_EXTENSION_URL:15`; extension detection; subscriber-gate edit already present)

- [ ] **Step 1: Repoint the extension URL**

```tsx
const CHROME_EXTENSION_URL = 'https://0wlan.com/extension';
```

- [ ] **Step 2: Detect the extension via the bridge, not the Anthropic-ID disk scan**

Replace the `isChromeExtensionInstalled()` call feeding the menu with a check against the bridge `/api/ext_status` (live truth). Add a small helper next to `resolveChromeBridge` usage:

```tsx
// live extension-connected status from the bridge; false if bridge down
async function extensionConnectedViaBridge(): Promise<boolean> {
  try {
    const { baseUrl, token } = resolveChromeBridge();
    const res = await fetch(`${baseUrl}/api/ext_status`, { headers: { authorization: `Bearer ${token}` } });
    if (!res.ok) return false;
    const j: any = await res.json();
    return !!j.isExtensionConnected;
  } catch { return false; }
}
```

Use its result for the `isExtensionInstalled` state that drives the menu labels. Import `resolveChromeBridge` from `../../utils/jarvisChromeMcp/resolveChromeBridge.js`.

- [ ] **Step 3: Transpile-check**

Run: `cd src/cli && bun build src/commands/chrome/chrome.tsx --no-bundle --outdir /tmp/ck`
Expected: "Transpiled file", no errors.

- [ ] **Step 4: Commit**

```bash
git add src/cli/src/commands/chrome/chrome.tsx
git commit -m "feat(cli): /chrome menu points at 0wlan.com extension + bridge-based status"
```

---

## Task 9: Full-suite + live verification

- [ ] **Step 1: Run the new unit tests together**

Run: `cd src/cli && bun test src/utils/jarvisChromeMcp/ src/bridge/browserTools.test.ts`
Expected: all PASS.

- [ ] **Step 2: Transpile-check every changed/created file**

Run: `cd src/cli && for f in src/bridge/browserTools.ts src/bridge/browserAgent.ts src/utils/jarvisChromeMcp/*.ts src/services/mcp/client.ts src/main.tsx src/commands/chrome/chrome.tsx; do bun build "$f" --no-bundle --outdir /tmp/ck || echo "FAIL $f"; done`
Expected: every file "Transpiled file", no FAIL lines.

- [ ] **Step 3: Boot smoke (no regression from enabling --chrome)**

Run: `bin/jarvis --chrome -p "say READY"` (from repo root)
Expected: returns `READY`; no crash/hang; no Anthropic native-messaging manifests written (`ls ~/.jarvis/chrome 2>/dev/null` should not appear from this path).

- [ ] **Step 4: Live tool exercise (requires desktop bridge + jarvis-screen loaded)**

With the Jarvis desktop running (bridge up) and the jarvis-screen extension loaded + connected:
Run: `bin/jarvis --chrome -p "Use your browser tools: navigate to https://example.com then read the page title and tell me what it is."`
Expected: the agent calls `mcp__jarvis-in-chrome__navigate` then `get_url`/`dom_summary`, and reports "Example Domain". Confirm via `/chrome` that the menu shows the actionable options (no "requires a claude.ai subscription") and Extension: Connected.

- [ ] **Step 5: Push the branch**

All per-task commits are already made (Tasks 1-8). Push the branch (no `git add -A` — every file was committed by explicit pathspec in its task):

```bash
git push -u origin feat/jarvis-in-chrome-mcp
```

Then open a PR when ready.

---

## Deferred: Phase 2 (0wlan.com relay)

Net-new; its own spec + plan. Scope: a WS rendezvous relay on 0wlan.com (sidecar like `src/web/scripts/pty-server.mjs`), an outbound transport from the extension (or the bridge dialing out), proxy-JWT audience + `proxy.ts` SELF_AUTH entry + CF Access exclusion, and a security review (remote browser control over the cloud). `resolveChromeBridge` already centralizes the base-URL choice, so phase 2 changes only that resolver + the server/relay endpoints.
