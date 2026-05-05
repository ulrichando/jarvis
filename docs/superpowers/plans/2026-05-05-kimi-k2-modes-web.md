# Kimi K2.6 Web Modes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `kimi-k2-instant`, `-thinking`, `-agent`, `-swarm` behaviorally distinct in the Next.js web chat by adding per-mode handlers that call Moonshot's K2.6 API with the right `thinking` params, tool-loop, and decompose-fan-out-aggregate orchestration.

**Architecture:** A single early-return at the top of `src/web/src/app/api/chat/route.ts` dispatches `kimi-k2-*` model IDs into a new `src/web/src/lib/ai/kimi/` package with one handler file per mode. Handlers use the Vercel AI SDK 6 (`streamText`, `generateText`, `generateObject`) with `providerOptions.kimi.thinking` for the K2.6 thinking-mode parameter. New custom UI parts (`kimi-reasoning`, `kimi-tool-trace`, `kimi-swarm-status`) flow through `experimental_dataPart` → rendered by new components in `src/web/src/components/chat/`.

**Tech Stack:** Next.js 16, Vercel AI SDK 6.0.168, `@ai-sdk/openai-compatible` (already used for Kimi), `ioredis` (already used for hub) for the Swarm cost guard, Vitest + MSW (NEW dev deps) for tests.

**Spec:** `docs/superpowers/specs/2026-05-05-kimi-k2-modes-web-design.md` (committed as `bd0dcd4`)

**Branch:** continue on `feat/ext-browser-control-v3` (or a new worktree if executor prefers — tasks are self-contained per-file).

---

## Task ordering rationale

Tasks are ordered to enable an end-to-end "Instant works" milestone after Task 4, then layer modes one at a time. The build-time gate (Task 2) lands first so the dispatcher (Task 3) can no-op until the first mode handler is ready. Tests are colocated with the file they test and are mandatory per task — `superpowers:test-driven-development` semantics throughout.

---

## File structure

**New files (with one-line responsibility each):**

```
src/web/vitest.config.ts                              # vitest config: jsdom env, paths alias, setup file
src/web/vitest.setup.ts                               # global mocks (ai SDK, kimi client, fetch)
src/web/src/lib/ai/kimi/index.ts                      # routeKimiMode dispatcher
src/web/src/lib/ai/kimi/shared.ts                     # buildKimiClient, formatKimiError, extractMessages, persona
src/web/src/lib/ai/kimi/instant.ts                    # Instant handler (thinking:disabled)
src/web/src/lib/ai/kimi/thinking.ts                   # Thinking handler (thinking:enabled, reasoning split)
src/web/src/lib/ai/kimi/agent.ts                      # Agent handler (tools, stepCountIs(5))
src/web/src/lib/ai/kimi/swarm.ts                      # Swarm handler (decompose, fan-out, aggregate, budget guard)
src/web/src/lib/ai/kimi/budget.ts                     # Per-day Swarm budget tracker (Redis INCRBY + EXPIRE)
src/web/src/components/chat/kimi-reasoning.tsx        # Collapsible "Thinking..." display for K2.6 reasoning
src/web/src/components/chat/kimi-tool-trace.tsx       # Tool call/result breadcrumbs for Agent
src/web/src/components/chat/kimi-swarm-progress.tsx   # Sub-agent progress counter for Swarm
src/web/tests/kimi/shared.test.ts
src/web/tests/kimi/instant.test.ts
src/web/tests/kimi/thinking.test.ts
src/web/tests/kimi/agent.test.ts
src/web/tests/kimi/swarm.test.ts
src/web/tests/kimi/budget.test.ts
src/web/tests/kimi/e2e.test.ts                        # 6 integration scenarios with MSW
```

**Modified files:**

```
src/web/package.json                                  # add vitest, @vitest/ui, jsdom, msw, @testing-library/react devDeps + scripts
src/web/src/app/api/chat/route.ts                     # add 5-line K2.6 routing block
src/web/src/components/chat/message.tsx               # render KimiReasoning, KimiToolTrace, KimiSwarmProgress on data parts
.env.local.example (or settings docs)                 # document KIMI_K2_MODES_ENABLED + KIMI_SWARM_DAILY_BUDGET_USD
```

---

## Shared types contract (used across all tasks)

These three discriminated UI part types are emitted by handlers and consumed by `message.tsx`. Defined ONCE in `src/web/src/lib/ai/kimi/shared.ts` and re-exported:

```typescript
export type KimiReasoningPart = {
  type: "kimi-reasoning";
  delta: string;
};

export type KimiToolTracePart = {
  type: "kimi-tool-trace";
  toolName: string;
  phase: "call" | "result";
  // For phase==="call": the tool input JSON. For phase==="result": the tool output (any shape).
  data: unknown;
};

export type KimiSwarmStatusPart = {
  type: "kimi-swarm-status";
  total: number;
  completed: number;
  // Optional human-readable description of the latest sub-agent that just finished.
  current?: string;
};

export type KimiUIPart =
  | KimiReasoningPart
  | KimiToolTracePart
  | KimiSwarmStatusPart;
```

---

## Task 1: Wire up Vitest + MSW + jsdom test infrastructure

**Files:**
- Modify: `src/web/package.json`
- Create: `src/web/vitest.config.ts`
- Create: `src/web/vitest.setup.ts`
- Create: `src/web/tests/sanity.test.ts`

- [ ] **Step 1: Add devDeps to package.json**

Append these entries to the `devDependencies` block (preserve existing entries — alphabetical order):

```json
    "@testing-library/jest-dom": "^6.5.0",
    "@testing-library/react": "^16.0.1",
    "@vitest/ui": "^2.1.0",
    "jsdom": "^25.0.0",
    "msw": "^2.6.0",
    "vitest": "^2.1.0",
```

Add this entry to the `scripts` block:

```json
    "test": "vitest run",
    "test:watch": "vitest",
    "test:ui": "vitest --ui"
```

- [ ] **Step 2: Install**

Run: `cd src/web && npm install`
Expected: clean install, no peer warnings beyond the existing ones.

- [ ] **Step 3: Create vitest config**

```typescript
// src/web/vitest.config.ts
import { defineConfig } from "vitest/config";
import path from "node:path";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./vitest.setup.ts"],
    include: ["tests/**/*.test.{ts,tsx}"],
    exclude: ["node_modules/**", ".next/**"],
    coverage: {
      provider: "v8",
      reporter: ["text", "html"],
      include: ["src/lib/ai/kimi/**", "src/components/chat/kimi-*.tsx"],
    },
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
});
```

Note: we deliberately omit `@vitejs/plugin-react` to keep deps minimal — Vitest's default JSX transform handles our test components. If a React component test needs the plugin, add it in a follow-up.

Strip the unused import:

```typescript
// src/web/vitest.config.ts
import { defineConfig } from "vitest/config";
import path from "node:path";

export default defineConfig({
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./vitest.setup.ts"],
    include: ["tests/**/*.test.{ts,tsx}"],
    exclude: ["node_modules/**", ".next/**"],
    coverage: {
      provider: "v8",
      reporter: ["text", "html"],
      include: ["src/lib/ai/kimi/**", "src/components/chat/kimi-*.tsx"],
    },
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
});
```

- [ ] **Step 4: Create vitest setup**

```typescript
// src/web/vitest.setup.ts
import "@testing-library/jest-dom/vitest";
import { afterEach, beforeAll, afterAll } from "vitest";
import { cleanup } from "@testing-library/react";

afterEach(() => cleanup());

// Tests that need MSW import { server } from "./tests/_msw/server" and
// call server.listen() in their own beforeAll. We don't start it
// globally because most unit tests mock the SDK directly and don't
// need a network interceptor.
```

- [ ] **Step 5: Write sanity test**

```typescript
// src/web/tests/sanity.test.ts
import { describe, it, expect } from "vitest";

describe("vitest infrastructure", () => {
  it("runs", () => {
    expect(1 + 1).toBe(2);
  });

  it("resolves @/ alias", async () => {
    // If this import errors, the alias is misconfigured.
    const mod = await import("@/lib/ai/models-meta");
    expect(mod).toBeDefined();
    expect(mod.MODELS_META).toBeTypeOf("object");
  });
});
```

- [ ] **Step 6: Run tests**

Run: `cd src/web && npm test`
Expected: 2 tests passing, exit code 0.

- [ ] **Step 7: Commit**

```bash
git add src/web/package.json src/web/package-lock.json src/web/vitest.config.ts src/web/vitest.setup.ts src/web/tests/sanity.test.ts
git commit -m "kimi-modes: vitest + jsdom + msw scaffolding (Task 1)"
```

---

## Task 2: Add KIMI_K2_MODES_ENABLED flag + minimal shared.ts

**Files:**
- Create: `src/web/src/lib/ai/kimi/shared.ts`
- Create: `src/web/tests/kimi/shared.test.ts`

This task lays down the shared module so subsequent mode handlers can import client/error/persona helpers without duplicating logic. The flag-check helper is defined here too so Task 3's dispatcher can simply call `kimiModesEnabled()`.

- [ ] **Step 1: Write the failing tests**

```typescript
// src/web/tests/kimi/shared.test.ts
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import {
  kimiModesEnabled,
  formatKimiError,
  extractMessagesForKimi,
  loadKimiPersona,
} from "@/lib/ai/kimi/shared";
import type { UIMessage } from "ai";

describe("kimiModesEnabled", () => {
  const originalEnv = process.env.KIMI_K2_MODES_ENABLED;
  afterEach(() => {
    if (originalEnv === undefined) delete process.env.KIMI_K2_MODES_ENABLED;
    else process.env.KIMI_K2_MODES_ENABLED = originalEnv;
  });

  it("returns false when env var unset", () => {
    delete process.env.KIMI_K2_MODES_ENABLED;
    expect(kimiModesEnabled()).toBe(false);
  });

  it("returns false when env var is anything other than '1'", () => {
    process.env.KIMI_K2_MODES_ENABLED = "true";
    expect(kimiModesEnabled()).toBe(false);
    process.env.KIMI_K2_MODES_ENABLED = "0";
    expect(kimiModesEnabled()).toBe(false);
    process.env.KIMI_K2_MODES_ENABLED = "";
    expect(kimiModesEnabled()).toBe(false);
  });

  it("returns true when env var is exactly '1'", () => {
    process.env.KIMI_K2_MODES_ENABLED = "1";
    expect(kimiModesEnabled()).toBe(true);
  });
});

describe("formatKimiError", () => {
  it("returns a 502 SSE Response for generic errors with kimi-error data part", async () => {
    const err = new Error("upstream exploded");
    const resp = formatKimiError(err);
    expect(resp.status).toBe(502);
    expect(resp.headers.get("Content-Type")).toBe("text/event-stream");
    const text = await resp.text();
    expect(text).toContain("kimi-error");
    expect(text).toContain("upstream exploded");
    expect(text).toContain("[DONE]");
  });

  it("returns 401 for AuthenticationError", async () => {
    const err: Error & { status?: number } = new Error("invalid key");
    err.status = 401;
    const resp = formatKimiError(err);
    expect(resp.status).toBe(401);
  });

  it("returns 429 for RateLimit with retry-after hint in body", async () => {
    const err: Error & { status?: number } = new Error("Rate limit");
    err.status = 429;
    const resp = formatKimiError(err, { retryAfterSeconds: 10 });
    expect(resp.status).toBe(429);
    const text = await resp.text();
    expect(text).toContain("10");
  });
});

describe("extractMessagesForKimi", () => {
  it("filters out file parts (Kimi text-only) and preserves text parts", () => {
    const msgs: UIMessage[] = [
      {
        id: "u1",
        role: "user",
        parts: [
          { type: "text", text: "hello" },
          // @ts-expect-error - file part shape varies; use cast
          { type: "file", url: "data:image/png;base64,xxx", mediaType: "image/png" },
        ],
      },
    ];
    const out = extractMessagesForKimi(msgs);
    expect(out).toHaveLength(1);
    expect(out[0].parts).toEqual([{ type: "text", text: "hello" }]);
  });

  it("drops messages that have no text after filtering", () => {
    const msgs: UIMessage[] = [
      {
        id: "u1",
        role: "user",
        parts: [
          // @ts-expect-error - file-only message
          { type: "file", url: "data:image/png;base64,xxx", mediaType: "image/png" },
        ],
      },
      { id: "u2", role: "user", parts: [{ type: "text", text: "real text" }] },
    ];
    const out = extractMessagesForKimi(msgs);
    expect(out).toHaveLength(1);
    expect(out[0].id).toBe("u2");
  });
});

describe("loadKimiPersona", () => {
  it("returns the JARVIS persona string by default", () => {
    const p = loadKimiPersona();
    expect(p).toContain("JARVIS");
    expect(p.length).toBeGreaterThan(50);
  });

  it("appends custom suffix when passed", () => {
    const p = loadKimiPersona({ suffix: "Be terse." });
    expect(p).toContain("Be terse.");
  });
});
```

- [ ] **Step 2: Run tests to verify failure**

Run: `cd src/web && npm test -- tests/kimi/shared.test.ts`
Expected: FAIL — module `@/lib/ai/kimi/shared` not found.

- [ ] **Step 3: Implement shared.ts**

```typescript
// src/web/src/lib/ai/kimi/shared.ts
import "server-only";
import type { UIMessage } from "ai";
import type { LanguageModel } from "ai";
import { resolveApiKey } from "@/lib/ai/models";
import { createOpenAICompatible } from "@ai-sdk/openai-compatible";

// Shared discriminated UI parts emitted by K2.6 mode handlers.
// message.tsx switches on `type` to render the right component.
export type KimiReasoningPart = {
  type: "kimi-reasoning";
  delta: string;
};

export type KimiToolTracePart = {
  type: "kimi-tool-trace";
  toolName: string;
  phase: "call" | "result";
  data: unknown;
};

export type KimiSwarmStatusPart = {
  type: "kimi-swarm-status";
  total: number;
  completed: number;
  current?: string;
};

export type KimiUIPart =
  | KimiReasoningPart
  | KimiToolTracePart
  | KimiSwarmStatusPart;

const KIMI_BASE_URL = "https://api.moonshot.ai/v1";
const KIMI_API_MODEL = "kimi-k2.6";

export function kimiModesEnabled(): boolean {
  return process.env.KIMI_K2_MODES_ENABLED === "1";
}

export class KimiKeyMissingError extends Error {
  constructor() {
    super("KIMI_API_KEY not configured");
    this.name = "KimiKeyMissingError";
  }
}

export async function buildKimiClient(): Promise<{
  model: LanguageModel;
  apiKey: string;
  baseURL: string;
}> {
  const { apiKey, baseURL } = await resolveApiKey("kimi");
  if (!apiKey) throw new KimiKeyMissingError();
  const url = baseURL ?? KIMI_BASE_URL;
  const factory = createOpenAICompatible({
    name: "kimi",
    apiKey,
    baseURL: url,
  });
  return {
    model: factory(KIMI_API_MODEL) as LanguageModel,
    apiKey,
    baseURL: url,
  };
}

// Drop image/file parts (K2.6 is text-only — vision goes through the
// kimi-vision-* models on a different path) and any messages that end
// up empty after stripping.
export function extractMessagesForKimi(msgs: UIMessage[]): UIMessage[] {
  const out: UIMessage[] = [];
  for (const m of msgs) {
    const textParts = m.parts.filter((p) => p.type === "text");
    if (textParts.length === 0) continue;
    out.push({ ...m, parts: textParts });
  }
  return out;
}

export function formatKimiError(
  err: unknown,
  opts: { retryAfterSeconds?: number } = {},
): Response {
  const e = err as Error & { status?: number };
  const status = e.status ?? 502;
  const message = e.message ?? "Kimi request failed";
  const headers: Record<string, string> = {
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-store",
  };
  if (opts.retryAfterSeconds !== undefined) {
    headers["Retry-After"] = String(opts.retryAfterSeconds);
  }
  const body = [
    `data: ${JSON.stringify({
      type: "kimi-error",
      status,
      message,
      retryAfter: opts.retryAfterSeconds,
    })}\n\n`,
    `data: [DONE]\n\n`,
  ].join("");
  return new Response(body, { status, headers });
}

export function loadKimiPersona(opts: { suffix?: string } = {}): string {
  const base = `You are JARVIS, an advanced AI assistant. Answer concisely. \
For complex questions, use markdown with headings, lists, and tables. \
Skip greetings and filler.`;
  return opts.suffix ? `${base}\n\n${opts.suffix}` : base;
}
```

- [ ] **Step 4: Run tests to verify pass**

Run: `cd src/web && npm test -- tests/kimi/shared.test.ts`
Expected: 11 tests passing.

- [ ] **Step 5: Commit**

```bash
git add src/web/src/lib/ai/kimi/shared.ts src/web/tests/kimi/shared.test.ts
git commit -m "kimi-modes: shared helpers (client, error, persona, flag) (Task 2)"
```

---

## Task 3: Routing dispatcher + chat-route hook

**Files:**
- Create: `src/web/src/lib/ai/kimi/index.ts`
- Modify: `src/web/src/app/api/chat/route.ts:188-208` (insert dispatcher early-return after settings load + modelId resolve)

The dispatcher is a switch on the `kimi-k2-{instant,thinking,agent,swarm}` suffix. Until subsequent tasks land the actual handlers, every branch returns a stub Response that says "mode not yet implemented." Task 4 replaces the Instant stub, Task 5 replaces Thinking, etc. This lets us land the chat-route edit ONCE and never touch it again.

- [ ] **Step 1: Write the failing tests**

```typescript
// src/web/tests/kimi/dispatch.test.ts
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { routeKimiMode } from "@/lib/ai/kimi";

function makeReq(body: object): Request {
  return new Request("http://localhost/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

describe("routeKimiMode dispatcher", () => {
  beforeEach(() => {
    process.env.KIMI_K2_MODES_ENABLED = "1";
    process.env.KIMI_API_KEY = "test-key";
  });
  afterEach(() => {
    delete process.env.KIMI_K2_MODES_ENABLED;
    delete process.env.KIMI_API_KEY;
  });

  it("rejects unknown kimi-k2-* model with 400", async () => {
    const req = makeReq({ messages: [], model: "kimi-k2-bogus" });
    const resp = await routeKimiMode(req, "kimi-k2-bogus");
    expect(resp.status).toBe(400);
  });

  it("dispatches kimi-k2-instant", async () => {
    const req = makeReq({ messages: [{ id: "u", role: "user", parts: [{ type: "text", text: "hi" }] }], model: "kimi-k2-instant" });
    const resp = await routeKimiMode(req, "kimi-k2-instant");
    // Either 200 (handler responded) or 502 (no API reachable in test);
    // both prove the switch matched. 400 would mean the switch missed.
    expect(resp.status).not.toBe(400);
  });

  it("dispatches kimi-k2-thinking", async () => {
    const req = makeReq({ messages: [{ id: "u", role: "user", parts: [{ type: "text", text: "hi" }] }], model: "kimi-k2-thinking" });
    const resp = await routeKimiMode(req, "kimi-k2-thinking");
    expect(resp.status).not.toBe(400);
  });

  it("dispatches kimi-k2-agent", async () => {
    const req = makeReq({ messages: [{ id: "u", role: "user", parts: [{ type: "text", text: "hi" }] }], model: "kimi-k2-agent" });
    const resp = await routeKimiMode(req, "kimi-k2-agent");
    expect(resp.status).not.toBe(400);
  });

  it("dispatches kimi-k2-swarm", async () => {
    const req = makeReq({ messages: [{ id: "u", role: "user", parts: [{ type: "text", text: "hi" }] }], model: "kimi-k2-swarm" });
    const resp = await routeKimiMode(req, "kimi-k2-swarm");
    expect(resp.status).not.toBe(400);
  });
});
```

- [ ] **Step 2: Run tests to verify failure**

Run: `cd src/web && npm test -- tests/kimi/dispatch.test.ts`
Expected: FAIL — `@/lib/ai/kimi` not found.

- [ ] **Step 3: Implement dispatcher with mode stubs**

```typescript
// src/web/src/lib/ai/kimi/index.ts
import "server-only";
import { formatKimiError } from "./shared";

export async function routeKimiMode(
  req: Request,
  modelId: string,
): Promise<Response> {
  const mode = modelId.replace(/^kimi-k2-/, "");
  switch (mode) {
    case "instant": {
      const { handleInstant } = await import("./instant");
      return handleInstant(req);
    }
    case "thinking": {
      const { handleThinking } = await import("./thinking");
      return handleThinking(req);
    }
    case "agent": {
      const { handleAgent } = await import("./agent");
      return handleAgent(req);
    }
    case "swarm": {
      const { handleSwarm } = await import("./swarm");
      return handleSwarm(req);
    }
    default:
      return Response.json(
        { error: "unknown_kimi_mode", modelId },
        { status: 400 },
      );
  }
}
```

The dispatcher uses dynamic imports so the four mode files can land in subsequent tasks without breaking this one. Until each is implemented, the dynamic import returns a stub (next step).

- [ ] **Step 4: Add stub mode handlers (instant, thinking, agent, swarm)**

```typescript
// src/web/src/lib/ai/kimi/instant.ts
import "server-only";

export async function handleInstant(_req: Request): Promise<Response> {
  return new Response(
    `data: ${JSON.stringify({
      type: "kimi-error",
      status: 501,
      message: "Instant mode not yet implemented (Task 4)",
    })}\n\ndata: [DONE]\n\n`,
    { status: 501, headers: { "Content-Type": "text/event-stream" } },
  );
}
```

```typescript
// src/web/src/lib/ai/kimi/thinking.ts
import "server-only";

export async function handleThinking(_req: Request): Promise<Response> {
  return new Response(
    `data: ${JSON.stringify({
      type: "kimi-error",
      status: 501,
      message: "Thinking mode not yet implemented (Task 5)",
    })}\n\ndata: [DONE]\n\n`,
    { status: 501, headers: { "Content-Type": "text/event-stream" } },
  );
}
```

```typescript
// src/web/src/lib/ai/kimi/agent.ts
import "server-only";

export async function handleAgent(_req: Request): Promise<Response> {
  return new Response(
    `data: ${JSON.stringify({
      type: "kimi-error",
      status: 501,
      message: "Agent mode not yet implemented (Task 6)",
    })}\n\ndata: [DONE]\n\n`,
    { status: 501, headers: { "Content-Type": "text/event-stream" } },
  );
}
```

```typescript
// src/web/src/lib/ai/kimi/swarm.ts
import "server-only";

export async function handleSwarm(_req: Request): Promise<Response> {
  return new Response(
    `data: ${JSON.stringify({
      type: "kimi-error",
      status: 501,
      message: "Swarm mode not yet implemented (Task 7)",
    })}\n\ndata: [DONE]\n\n`,
    { status: 501, headers: { "Content-Type": "text/event-stream" } },
  );
}
```

- [ ] **Step 5: Update dispatch test expectations**

The stubs return 501 which is `!== 400`, so the existing assertions hold. Run:

Run: `cd src/web && npm test -- tests/kimi/dispatch.test.ts`
Expected: 5 tests passing.

- [ ] **Step 6: Hook the dispatcher into the chat route**

In `src/web/src/app/api/chat/route.ts`, find this block (around lines 240-247):

```typescript
  // eslint-disable-next-line no-console
  console.log(
    `[chat] POST mode=${mode ?? "regular"} model=${modelId} msgs=${messages.length} ws=${workspaceId ?? "—"}`,
  );

  let selected;
  try {
    selected = await getModel(modelId);
```

Insert the K2.6 routing block IMMEDIATELY BEFORE `let selected;`:

```typescript
  // K2.6 mode-aware routing. Each kimi-k2-{instant,thinking,agent,swarm}
  // model id maps to the same upstream API but needs different params /
  // orchestration to deliver the user-facing semantics. The full
  // dispatcher lives in src/lib/ai/kimi/. Gated by KIMI_K2_MODES_ENABLED
  // so we can roll it out behind a flag and revert in one env-var flip.
  if (modelId.startsWith("kimi-k2-") && process.env.KIMI_K2_MODES_ENABLED === "1") {
    const { routeKimiMode } = await import("@/lib/ai/kimi");
    return routeKimiMode(req, modelId);
  }

  let selected;
```

Note: we re-clone the request body in the dispatcher because Next.js Request body is single-consumption. The dispatcher must call `await req.json()` itself (it does in subsequent tasks). To make this work, we pass `req` directly — but the chat route ALREADY consumed `req.json()` at line 189. We need to pass the parsed body forward.

Revise: change the dispatcher signature to accept the parsed body, and pass it from the chat route.

Update `src/web/src/lib/ai/kimi/index.ts`:

```typescript
// src/web/src/lib/ai/kimi/index.ts
import "server-only";
import type { UIMessage } from "ai";

export type KimiModeRequest = {
  messages: UIMessage[];
  model?: string;
  system?: string;
  conversationId?: string;
};

export async function routeKimiMode(
  body: KimiModeRequest,
  modelId: string,
): Promise<Response> {
  const mode = modelId.replace(/^kimi-k2-/, "");
  switch (mode) {
    case "instant": {
      const { handleInstant } = await import("./instant");
      return handleInstant(body);
    }
    case "thinking": {
      const { handleThinking } = await import("./thinking");
      return handleThinking(body);
    }
    case "agent": {
      const { handleAgent } = await import("./agent");
      return handleAgent(body);
    }
    case "swarm": {
      const { handleSwarm } = await import("./swarm");
      return handleSwarm(body);
    }
    default:
      return Response.json(
        { error: "unknown_kimi_mode", modelId },
        { status: 400 },
      );
  }
}
```

Update each stub handler to accept `body: KimiModeRequest`:

```typescript
// src/web/src/lib/ai/kimi/instant.ts
import "server-only";
import type { KimiModeRequest } from "./index";

export async function handleInstant(_body: KimiModeRequest): Promise<Response> {
  return new Response(
    `data: ${JSON.stringify({
      type: "kimi-error",
      status: 501,
      message: "Instant mode not yet implemented (Task 4)",
    })}\n\ndata: [DONE]\n\n`,
    { status: 501, headers: { "Content-Type": "text/event-stream" } },
  );
}
```

(Apply the same `KimiModeRequest` parameter swap to thinking.ts, agent.ts, swarm.ts.)

Update `src/web/src/app/api/chat/route.ts` insertion to use the parsed body:

```typescript
  if (modelId.startsWith("kimi-k2-") && process.env.KIMI_K2_MODES_ENABLED === "1") {
    const { routeKimiMode } = await import("@/lib/ai/kimi");
    return routeKimiMode({ messages, model, system }, modelId);
  }

  let selected;
```

Update the dispatch tests to pass body objects instead of Requests:

```typescript
// src/web/tests/kimi/dispatch.test.ts (relevant section)
import { routeKimiMode } from "@/lib/ai/kimi";

describe("routeKimiMode dispatcher", () => {
  beforeEach(() => {
    process.env.KIMI_K2_MODES_ENABLED = "1";
    process.env.KIMI_API_KEY = "test-key";
  });
  afterEach(() => {
    delete process.env.KIMI_K2_MODES_ENABLED;
    delete process.env.KIMI_API_KEY;
  });

  it("rejects unknown kimi-k2-* model with 400", async () => {
    const resp = await routeKimiMode({ messages: [] }, "kimi-k2-bogus");
    expect(resp.status).toBe(400);
  });

  it("dispatches kimi-k2-instant", async () => {
    const resp = await routeKimiMode(
      { messages: [{ id: "u", role: "user", parts: [{ type: "text", text: "hi" }] }] },
      "kimi-k2-instant",
    );
    expect(resp.status).not.toBe(400);
  });

  it("dispatches kimi-k2-thinking", async () => {
    const resp = await routeKimiMode(
      { messages: [{ id: "u", role: "user", parts: [{ type: "text", text: "hi" }] }] },
      "kimi-k2-thinking",
    );
    expect(resp.status).not.toBe(400);
  });

  it("dispatches kimi-k2-agent", async () => {
    const resp = await routeKimiMode(
      { messages: [{ id: "u", role: "user", parts: [{ type: "text", text: "hi" }] }] },
      "kimi-k2-agent",
    );
    expect(resp.status).not.toBe(400);
  });

  it("dispatches kimi-k2-swarm", async () => {
    const resp = await routeKimiMode(
      { messages: [{ id: "u", role: "user", parts: [{ type: "text", text: "hi" }] }] },
      "kimi-k2-swarm",
    );
    expect(resp.status).not.toBe(400);
  });
});
```

- [ ] **Step 7: Run tests + typecheck**

Run: `cd src/web && npm test -- tests/kimi/`
Expected: dispatch (5) + shared (11) tests pass.

Run: `cd src/web && npx tsc --noEmit`
Expected: zero new TS errors.

- [ ] **Step 8: Commit**

```bash
git add src/web/src/lib/ai/kimi/index.ts src/web/src/lib/ai/kimi/instant.ts src/web/src/lib/ai/kimi/thinking.ts src/web/src/lib/ai/kimi/agent.ts src/web/src/lib/ai/kimi/swarm.ts src/web/src/app/api/chat/route.ts src/web/tests/kimi/dispatch.test.ts
git commit -m "kimi-modes: dispatcher + chat-route hook + handler stubs (Task 3)"
```

---

## Task 4: Implement Instant mode

**Files:**
- Modify: `src/web/src/lib/ai/kimi/instant.ts`
- Create: `src/web/tests/kimi/instant.test.ts`

Instant is the simplest mode: one streamText call with `thinking: { type: 'disabled' }` and `max_tokens: 1024`. No reasoning, no tools, no fan-out. This is the floor — every other mode is a superset.

- [ ] **Step 1: Write the failing tests**

```typescript
// src/web/tests/kimi/instant.test.ts
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";

// Mock streamText so we can inspect the params and avoid a real API call.
vi.mock("ai", async () => {
  const actual = await vi.importActual<typeof import("ai")>("ai");
  return {
    ...actual,
    streamText: vi.fn(),
  };
});

vi.mock("@/lib/ai/kimi/shared", async () => {
  const actual = await vi.importActual<typeof import("@/lib/ai/kimi/shared")>(
    "@/lib/ai/kimi/shared",
  );
  return {
    ...actual,
    buildKimiClient: vi.fn(async () => ({
      model: { _mock: "kimi-k2.6" },
      apiKey: "test-key",
      baseURL: "https://api.moonshot.ai/v1",
    })),
  };
});

import { streamText } from "ai";
import { handleInstant } from "@/lib/ai/kimi/instant";

const mockedStreamText = streamText as unknown as ReturnType<typeof vi.fn>;

function fakeStreamResult() {
  return {
    toUIMessageStreamResponse: () =>
      new Response("ok", {
        status: 200,
        headers: { "Content-Type": "text/event-stream" },
      }),
    consumeStream: () => undefined,
  };
}

describe("handleInstant", () => {
  beforeEach(() => {
    mockedStreamText.mockReset();
    mockedStreamText.mockReturnValue(fakeStreamResult());
    process.env.KIMI_API_KEY = "test-key";
  });
  afterEach(() => {
    delete process.env.KIMI_API_KEY;
  });

  it("calls streamText with thinking:disabled in providerOptions.kimi", async () => {
    await handleInstant({
      messages: [{ id: "u", role: "user", parts: [{ type: "text", text: "hi" }] }],
    });
    expect(mockedStreamText).toHaveBeenCalledOnce();
    const args = mockedStreamText.mock.calls[0][0] as Record<string, unknown>;
    const providerOptions = args.providerOptions as { kimi?: { thinking?: { type?: string } } };
    expect(providerOptions?.kimi?.thinking?.type).toBe("disabled");
  });

  it("uses maxOutputTokens 1024", async () => {
    await handleInstant({
      messages: [{ id: "u", role: "user", parts: [{ type: "text", text: "hi" }] }],
    });
    const args = mockedStreamText.mock.calls[0][0] as Record<string, unknown>;
    expect(args.maxOutputTokens).toBe(1024);
  });

  it("uses temperature 0.6", async () => {
    await handleInstant({
      messages: [{ id: "u", role: "user", parts: [{ type: "text", text: "hi" }] }],
    });
    const args = mockedStreamText.mock.calls[0][0] as Record<string, unknown>;
    expect(args.temperature).toBe(0.6);
  });

  it("returns a 200 SSE Response from toUIMessageStreamResponse", async () => {
    const resp = await handleInstant({
      messages: [{ id: "u", role: "user", parts: [{ type: "text", text: "hi" }] }],
    });
    expect(resp.status).toBe(200);
    expect(resp.headers.get("Content-Type")).toBe("text/event-stream");
  });

  it("returns 401 SSE when KIMI_API_KEY missing", async () => {
    delete process.env.KIMI_API_KEY;
    const { buildKimiClient } = await import("@/lib/ai/kimi/shared");
    (buildKimiClient as unknown as ReturnType<typeof vi.fn>).mockRejectedValueOnce(
      Object.assign(new Error("KIMI_API_KEY not configured"), { name: "KimiKeyMissingError" }),
    );
    const resp = await handleInstant({
      messages: [{ id: "u", role: "user", parts: [{ type: "text", text: "hi" }] }],
    });
    expect(resp.status).toBe(401);
  });
});
```

- [ ] **Step 2: Run test to verify failure**

Run: `cd src/web && npm test -- tests/kimi/instant.test.ts`
Expected: FAIL — assertions on streamText args fail (handler is the stub).

- [ ] **Step 3: Implement Instant handler**

Replace the stub in `src/web/src/lib/ai/kimi/instant.ts`:

```typescript
// src/web/src/lib/ai/kimi/instant.ts
import "server-only";
import { convertToModelMessages, streamText } from "ai";
import {
  buildKimiClient,
  extractMessagesForKimi,
  formatKimiError,
  KimiKeyMissingError,
  loadKimiPersona,
} from "./shared";
import type { KimiModeRequest } from "./index";

export async function handleInstant(body: KimiModeRequest): Promise<Response> {
  let client;
  try {
    client = await buildKimiClient();
  } catch (err) {
    if (err instanceof KimiKeyMissingError) {
      return new Response(
        `data: ${JSON.stringify({
          type: "kimi-error",
          status: 401,
          message: "Kimi API key missing or invalid",
        })}\n\ndata: [DONE]\n\n`,
        { status: 401, headers: { "Content-Type": "text/event-stream" } },
      );
    }
    return formatKimiError(err);
  }

  try {
    const messages = await convertToModelMessages(
      extractMessagesForKimi(body.messages),
    );
    const system = body.system ?? loadKimiPersona();

    const result = streamText({
      model: client.model,
      system,
      messages,
      temperature: 0.6,
      maxOutputTokens: 1024,
      providerOptions: {
        kimi: {
          thinking: { type: "disabled" },
        },
      },
      onError: (err) => {
        console.error("[kimi-instant] streamText error:", err);
      },
    });

    result.consumeStream();
    return result.toUIMessageStreamResponse({
      headers: { "X-Kimi-Mode": "instant" },
    });
  } catch (err) {
    return formatKimiError(err);
  }
}
```

- [ ] **Step 4: Run test to verify pass**

Run: `cd src/web && npm test -- tests/kimi/instant.test.ts`
Expected: 5 tests passing.

- [ ] **Step 5: Live smoke check (manual)**

Run: `cd src/web && KIMI_K2_MODES_ENABLED=1 npm run dev:next`

In the chat UI, pick `kimi-k2-instant` and send "what's 2+2?".
Expected: short answer ("4"), no reasoning section. Server log shows `[chat]` then no errors.

- [ ] **Step 6: Commit**

```bash
git add src/web/src/lib/ai/kimi/instant.ts src/web/tests/kimi/instant.test.ts
git commit -m "kimi-modes: Instant handler — thinking:disabled, max 1024 (Task 4)"
```

---

## Task 5: Implement Thinking mode + KimiReasoning UI component

**Files:**
- Modify: `src/web/src/lib/ai/kimi/thinking.ts`
- Create: `src/web/src/components/chat/kimi-reasoning.tsx`
- Modify: `src/web/src/components/chat/message.tsx` (render KimiReasoning when data parts present)
- Create: `src/web/tests/kimi/thinking.test.ts`

Thinking mode passes `thinking: { type: 'enabled', keep: 'all' }` and `temp: 1.0`. The K2.6 API streams `reasoning_content` deltas BEFORE the regular `content` deltas. We split them: reasoning → custom data part → KimiReasoning UI; text → normal text deltas → main message body.

The Vercel AI SDK 6 `openai-compatible` provider may not surface `reasoning_content` as a typed field. We tap into the raw stream via `experimental_transform` (or `result.fullStream`) and emit data parts.

- [ ] **Step 1: Write the failing tests**

```typescript
// src/web/tests/kimi/thinking.test.ts
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";

vi.mock("ai", async () => {
  const actual = await vi.importActual<typeof import("ai")>("ai");
  return {
    ...actual,
    streamText: vi.fn(),
  };
});

vi.mock("@/lib/ai/kimi/shared", async () => {
  const actual = await vi.importActual<typeof import("@/lib/ai/kimi/shared")>(
    "@/lib/ai/kimi/shared",
  );
  return {
    ...actual,
    buildKimiClient: vi.fn(async () => ({
      model: { _mock: "kimi-k2.6" },
      apiKey: "test-key",
      baseURL: "https://api.moonshot.ai/v1",
    })),
  };
});

import { streamText } from "ai";
import { handleThinking } from "@/lib/ai/kimi/thinking";

const mockedStreamText = streamText as unknown as ReturnType<typeof vi.fn>;

function fakeStreamResult() {
  return {
    toUIMessageStreamResponse: () =>
      new Response("ok", { status: 200, headers: { "Content-Type": "text/event-stream" } }),
    consumeStream: () => undefined,
  };
}

describe("handleThinking", () => {
  beforeEach(() => {
    mockedStreamText.mockReset();
    mockedStreamText.mockReturnValue(fakeStreamResult());
    process.env.KIMI_API_KEY = "test-key";
  });

  it("sends thinking:enabled,keep:all", async () => {
    await handleThinking({
      messages: [{ id: "u", role: "user", parts: [{ type: "text", text: "explain" }] }],
    });
    const args = mockedStreamText.mock.calls[0][0] as Record<string, unknown>;
    const po = args.providerOptions as { kimi?: { thinking?: { type?: string; keep?: string } } };
    expect(po?.kimi?.thinking?.type).toBe("enabled");
    expect(po?.kimi?.thinking?.keep).toBe("all");
  });

  it("uses maxOutputTokens 16000", async () => {
    await handleThinking({
      messages: [{ id: "u", role: "user", parts: [{ type: "text", text: "explain" }] }],
    });
    const args = mockedStreamText.mock.calls[0][0] as Record<string, unknown>;
    expect(args.maxOutputTokens).toBe(16000);
  });

  it("uses temperature 1.0", async () => {
    await handleThinking({
      messages: [{ id: "u", role: "user", parts: [{ type: "text", text: "explain" }] }],
    });
    const args = mockedStreamText.mock.calls[0][0] as Record<string, unknown>;
    expect(args.temperature).toBe(1.0);
  });

  it("retries with maxOutputTokens 8000 when first call rejects '16000 too high'", async () => {
    mockedStreamText.mockReset();
    // First call: synchronously throws (the SDK rejects at call time when max_tokens too high)
    let calls = 0;
    mockedStreamText.mockImplementation(() => {
      calls++;
      if (calls === 1) {
        throw Object.assign(new Error("max_completion_tokens is above the model's limit"), {
          status: 400,
        });
      }
      return fakeStreamResult();
    });
    await handleThinking({
      messages: [{ id: "u", role: "user", parts: [{ type: "text", text: "explain" }] }],
    });
    expect(calls).toBe(2);
    const secondCallArgs = mockedStreamText.mock.calls[1][0] as Record<string, unknown>;
    expect(secondCallArgs.maxOutputTokens).toBe(8000);
  });

  it("returns 200 SSE", async () => {
    const resp = await handleThinking({
      messages: [{ id: "u", role: "user", parts: [{ type: "text", text: "explain" }] }],
    });
    expect(resp.status).toBe(200);
  });

  it("emits X-Kimi-Mode: thinking response header", async () => {
    const resp = await handleThinking({
      messages: [{ id: "u", role: "user", parts: [{ type: "text", text: "explain" }] }],
    });
    expect(resp.headers.get("X-Kimi-Mode")).toBe("thinking");
  });

  it("returns 401 when KIMI_API_KEY missing", async () => {
    const { buildKimiClient } = await import("@/lib/ai/kimi/shared");
    (buildKimiClient as unknown as ReturnType<typeof vi.fn>).mockRejectedValueOnce(
      Object.assign(new Error("KIMI_API_KEY not configured"), { name: "KimiKeyMissingError" }),
    );
    const resp = await handleThinking({
      messages: [{ id: "u", role: "user", parts: [{ type: "text", text: "explain" }] }],
    });
    expect(resp.status).toBe(401);
  });
});
```

- [ ] **Step 2: Run tests to verify failure**

Run: `cd src/web && npm test -- tests/kimi/thinking.test.ts`
Expected: FAIL.

- [ ] **Step 3: Implement Thinking handler**

Replace `src/web/src/lib/ai/kimi/thinking.ts`:

```typescript
// src/web/src/lib/ai/kimi/thinking.ts
import "server-only";
import { convertToModelMessages, streamText } from "ai";
import {
  buildKimiClient,
  extractMessagesForKimi,
  formatKimiError,
  KimiKeyMissingError,
  loadKimiPersona,
} from "./shared";
import type { KimiModeRequest } from "./index";

const PRIMARY_MAX = 16000;
const FALLBACK_MAX = 8000;

function isMaxTokensError(err: unknown): boolean {
  const msg = (err as Error)?.message ?? "";
  return /max_(completion_)?tokens.*(limit|above|exceed)/i.test(msg);
}

function callStream(
  client: { model: import("ai").LanguageModel },
  system: string,
  messages: Awaited<ReturnType<typeof convertToModelMessages>>,
  maxOutputTokens: number,
) {
  return streamText({
    model: client.model,
    system,
    messages,
    temperature: 1.0,
    maxOutputTokens,
    providerOptions: {
      kimi: {
        thinking: { type: "enabled", keep: "all" },
      },
    },
    onError: (err) => {
      console.error("[kimi-thinking] streamText error:", err);
    },
  });
}

export async function handleThinking(body: KimiModeRequest): Promise<Response> {
  let client;
  try {
    client = await buildKimiClient();
  } catch (err) {
    if (err instanceof KimiKeyMissingError) {
      return new Response(
        `data: ${JSON.stringify({
          type: "kimi-error",
          status: 401,
          message: "Kimi API key missing or invalid",
        })}\n\ndata: [DONE]\n\n`,
        { status: 401, headers: { "Content-Type": "text/event-stream" } },
      );
    }
    return formatKimiError(err);
  }

  try {
    const messages = await convertToModelMessages(
      extractMessagesForKimi(body.messages),
    );
    const system = body.system ?? loadKimiPersona();

    let result;
    try {
      result = callStream(client, system, messages, PRIMARY_MAX);
    } catch (err) {
      if (isMaxTokensError(err)) {
        console.warn(
          `[kimi-thinking] ${PRIMARY_MAX} rejected; retrying with ${FALLBACK_MAX}`,
        );
        result = callStream(client, system, messages, FALLBACK_MAX);
      } else {
        throw err;
      }
    }

    result.consumeStream();
    return result.toUIMessageStreamResponse({
      headers: { "X-Kimi-Mode": "thinking" },
    });
  } catch (err) {
    return formatKimiError(err);
  }
}
```

- [ ] **Step 4: Run tests to verify pass**

Run: `cd src/web && npm test -- tests/kimi/thinking.test.ts`
Expected: 7 tests passing.

- [ ] **Step 5: Add stream transform that splits reasoning_content from text**

The Moonshot K2.6 API surfaces `reasoning_content` in the streaming chunk shape. The Vercel AI SDK 6 openai-compatible provider may pass this through as a `reasoning` part, OR may need a transform.

First inspection: read what the SDK forwards. In Step 3 we use the SDK's default behavior, which for openai-compatible may already split reasoning into `reasoning-delta` UI parts. Verify with one live call before adding a transform.

After Task 5 Step 4 passes the unit tests, run a live thinking-mode call against the actual Moonshot endpoint:

```bash
cd src/web && KIMI_K2_MODES_ENABLED=1 KIMI_API_KEY=$KIMI_API_KEY npm run dev:next
```

In the UI, select `kimi-k2-thinking` and ask "what's 17 * 23, show your work?". In the network tab inspect the `/api/chat` SSE stream. If you see `reasoning-delta` events with the chain-of-thought, the SDK is doing the split — proceed to Step 6 (UI). If you see only `text-delta` with `<think>...</think>` tags or with reasoning baked into the visible body, you need the transform below.

If transform needed, add to `thinking.ts` after the `streamText` call (before `consumeStream`):

```typescript
    // Tap fullStream to split reasoning_content vs text. K2.6 emits
    // reasoning_content first, then content. The openai-compatible
    // provider's behavior depends on whether it normalizes the field.
    // This transform is a safety net: if the provider already split
    // them into reasoning vs text deltas, this is a no-op.
    // Note: applied via transform option, not post-hoc, so the UI
    // sees the corrected stream from the first delta.
```

If you find the SDK is forwarding raw `<think>...</think>` tagged text (DeepSeek-R1 style), add:

```typescript
    // ... inside streamText({ ... }):
    experimental_transform: (text) => {
      // If the model emits <think>...</think> blocks inline, route them
      // to a kimi-reasoning data part. This is a fallback for providers
      // that don't natively split reasoning_content.
      // Implementation: a stream pipe that buffers, detects opening
      // <think>, accumulates inside, emits delta as reasoning data part,
      // then resumes text streaming after </think>.
    },
```

The decision and the actual transform code go in this step at impl time — `experimental_transform` API in AI SDK 6 takes a TransformStream factory. Pseudocode:

```typescript
function thinkingTagSplitter(): (
  controller: TransformStreamDefaultController<unknown>,
) => TransformStream<unknown, unknown> {
  return () => {
    let inThink = false;
    let buf = "";
    return new TransformStream({
      transform(chunk, controller) {
        // chunk is the SDK's text-delta event shape:
        // { type: 'text-delta', textDelta: string }
        if (chunk.type !== "text-delta") {
          controller.enqueue(chunk);
          return;
        }
        buf += chunk.textDelta;
        // Process tags greedily.
        while (buf.length > 0) {
          if (!inThink) {
            const open = buf.indexOf("<think>");
            if (open === -1) {
              controller.enqueue({ type: "text-delta", textDelta: buf });
              buf = "";
              return;
            }
            if (open > 0) {
              controller.enqueue({ type: "text-delta", textDelta: buf.slice(0, open) });
            }
            buf = buf.slice(open + "<think>".length);
            inThink = true;
          } else {
            const close = buf.indexOf("</think>");
            if (close === -1) {
              if (buf.length > 0) {
                controller.enqueue({
                  type: "data-kimi-reasoning",
                  delta: buf,
                });
                buf = "";
              }
              return;
            }
            if (close > 0) {
              controller.enqueue({
                type: "data-kimi-reasoning",
                delta: buf.slice(0, close),
              });
            }
            buf = buf.slice(close + "</think>".length);
            inThink = false;
          }
        }
      },
    });
  };
}
```

If the live test in this step shows the SDK already splits cleanly, skip the transform code entirely. The decision is binary based on what the live response looks like.

Document the chosen path in a one-line comment at the top of `thinking.ts` so the next maintainer knows whether the transform is in play or not.

- [ ] **Step 6: Create KimiReasoning UI component**

```typescript
// src/web/src/components/chat/kimi-reasoning.tsx
"use client";
import { useEffect, useRef, useState } from "react";
import { motion, AnimatePresence } from "motion/react";
import { Brain, ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";

export function KimiReasoning({
  text,
  streaming,
}: {
  text: string;
  streaming: boolean;
}) {
  const [open, setOpen] = useState(streaming);
  const [duration, setDuration] = useState<number | null>(null);
  const startedAtRef = useRef<number | null>(null);
  const scrollerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (streaming && startedAtRef.current === null) {
      startedAtRef.current = Date.now();
    }
    if (!streaming && startedAtRef.current !== null && duration === null) {
      setDuration(Math.max(1, Math.round((Date.now() - startedAtRef.current) / 1000)));
    }
  }, [streaming, duration]);

  useEffect(() => {
    if (!streaming) setOpen(false);
  }, [streaming]);

  useEffect(() => {
    if (!open || !streaming) return;
    const el = scrollerRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [text, open, streaming]);

  const label = streaming
    ? "Thinking…"
    : duration !== null
      ? `Thought for ${duration}s`
      : "Thoughts";

  if (!text && !streaming) return null;

  return (
    <div className="mb-3 rounded-lg border border-border/40 bg-muted/20 overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-[12px] text-muted-foreground hover:bg-muted/40 transition-colors"
      >
        <Brain
          className={cn(
            "size-3.5 shrink-0",
            streaming ? "text-primary animate-pulse" : "text-muted-foreground/70",
          )}
        />
        <span className="flex-1 font-medium">{label}</span>
        <ChevronDown
          className={cn(
            "size-3.5 shrink-0 transition-transform duration-200",
            open ? "rotate-180" : "rotate-0",
          )}
        />
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            key="kimi-reasoning-body"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.18, ease: "easeOut" }}
            className="overflow-hidden border-t border-border/30"
          >
            <div
              ref={scrollerRef}
              className="max-h-64 overflow-y-auto px-3 py-2.5 text-[12px] leading-5 text-muted-foreground/85 whitespace-pre-wrap font-mono"
            >
              {text}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
```

- [ ] **Step 7: Wire KimiReasoning into message.tsx**

In `src/web/src/components/chat/message.tsx`, locate the import block at the top and add:

```typescript
import { KimiReasoning } from "./kimi-reasoning";
```

Add a helper that scans `message.parts` for the custom Kimi reasoning data parts and concatenates them:

After the existing helper functions (around line 102, after `imagePartsFromMessage`), add:

```typescript
function kimiReasoningFromMessage(parts: UIMessage["parts"]): string {
  let text = "";
  for (const p of parts) {
    if (
      typeof p === "object" &&
      p !== null &&
      (p as { type?: string }).type === "data-kimi-reasoning"
    ) {
      const delta = (p as { delta?: unknown }).delta;
      if (typeof delta === "string") text += delta;
    }
  }
  return text;
}
```

In the `Message` function body, just after `const text = textFromParts(message.parts);`, add:

```typescript
  const kimiReasoning = kimiReasoningFromMessage(message.parts);
```

In the assistant render branch (the `<div className="w-full">` block, currently at line 230), insert KimiReasoning ABOVE the existing `ReasoningBlock` so K2.6 thinking shows in its own widget if the existing one is unused for this turn:

```typescript
        <div className="w-full">
          {kimiReasoning ? (
            <KimiReasoning
              text={kimiReasoning}
              streaming={Boolean(isStreaming && !text)}
            />
          ) : null}
          {reasoning ? (
            <ReasoningBlock
              reasoning={reasoning}
              streaming={reasoningStreaming}
            />
          ) : null}
          {/* …rest unchanged… */}
```

- [ ] **Step 8: Run typecheck**

Run: `cd src/web && npx tsc --noEmit`
Expected: zero errors.

- [ ] **Step 9: Live smoke test (manual)**

Run: `cd src/web && KIMI_K2_MODES_ENABLED=1 npm run dev:next`

In the chat UI:
- Select `kimi-k2-thinking`
- Send: "what's 17 * 23, show your work?"
- Expected: a "Thinking…" pill expands while reasoning streams; the final answer "391" arrives in the main body. After streaming, the pill collapses to "Thought for Ns".

If reasoning leaks INTO the main body (i.e., you see the chain-of-thought as the answer), the transform from Step 5 needs to be implemented and re-tested.

- [ ] **Step 10: Commit**

```bash
git add src/web/src/lib/ai/kimi/thinking.ts src/web/src/components/chat/kimi-reasoning.tsx src/web/src/components/chat/message.tsx src/web/tests/kimi/thinking.test.ts
git commit -m "kimi-modes: Thinking handler + KimiReasoning UI (Task 5)"
```

---

## Task 6: Implement Agent mode + KimiToolTrace UI component

**Files:**
- Modify: `src/web/src/lib/ai/kimi/agent.ts`
- Create: `src/web/src/components/chat/kimi-tool-trace.tsx`
- Modify: `src/web/src/components/chat/message.tsx` (render KimiToolTrace on data parts)
- Create: `src/web/tests/kimi/agent.test.ts`

Agent uses `streamText` with `tools: { webSearch: webSearchTool }` and `stopWhen: stepCountIs(5)`. We tap `result.fullStream` to surface tool-call/tool-result events as `kimi-tool-trace` data parts so the UI shows breadcrumbs.

Note: Moonshot's builtin `$web_search` tool is exposed via the chat completions API but the openai-compatible provider may not pass it through cleanly — for v1 we use the existing `webSearchTool` (DuckDuckGo) only. If during impl `$web_search` works through `providerOptions.kimi.tools`, add it as a second tool.

- [ ] **Step 1: Write the failing tests**

```typescript
// src/web/tests/kimi/agent.test.ts
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";

vi.mock("ai", async () => {
  const actual = await vi.importActual<typeof import("ai")>("ai");
  return {
    ...actual,
    streamText: vi.fn(),
    stepCountIs: vi.fn((n: number) => ({ _stepLimit: n })),
  };
});

vi.mock("@/lib/ai/kimi/shared", async () => {
  const actual = await vi.importActual<typeof import("@/lib/ai/kimi/shared")>(
    "@/lib/ai/kimi/shared",
  );
  return {
    ...actual,
    buildKimiClient: vi.fn(async () => ({
      model: { _mock: "kimi-k2.6" },
      apiKey: "test-key",
      baseURL: "https://api.moonshot.ai/v1",
    })),
  };
});

import { streamText } from "ai";
import { handleAgent } from "@/lib/ai/kimi/agent";

const mockedStreamText = streamText as unknown as ReturnType<typeof vi.fn>;

function fakeStreamResult() {
  return {
    toUIMessageStreamResponse: () =>
      new Response("ok", { status: 200, headers: { "Content-Type": "text/event-stream" } }),
    consumeStream: () => undefined,
  };
}

describe("handleAgent", () => {
  beforeEach(() => {
    mockedStreamText.mockReset();
    mockedStreamText.mockReturnValue(fakeStreamResult());
    process.env.KIMI_API_KEY = "test-key";
  });

  it("binds webSearch tool", async () => {
    await handleAgent({
      messages: [{ id: "u", role: "user", parts: [{ type: "text", text: "what's the weather in Paris" }] }],
    });
    const args = mockedStreamText.mock.calls[0][0] as Record<string, unknown>;
    const tools = args.tools as Record<string, unknown>;
    expect(tools).toBeDefined();
    expect(tools.webSearch).toBeDefined();
  });

  it("uses thinking:disabled (incompatible with $web_search per Moonshot docs)", async () => {
    await handleAgent({
      messages: [{ id: "u", role: "user", parts: [{ type: "text", text: "weather?" }] }],
    });
    const args = mockedStreamText.mock.calls[0][0] as Record<string, unknown>;
    const po = args.providerOptions as { kimi?: { thinking?: { type?: string } } };
    expect(po?.kimi?.thinking?.type).toBe("disabled");
  });

  it("sets stopWhen to stepCountIs(5)", async () => {
    await handleAgent({
      messages: [{ id: "u", role: "user", parts: [{ type: "text", text: "weather?" }] }],
    });
    const args = mockedStreamText.mock.calls[0][0] as Record<string, unknown>;
    const stopWhen = args.stopWhen as { _stepLimit?: number };
    expect(stopWhen?._stepLimit).toBe(5);
  });

  it("uses maxOutputTokens 4096 (room for tool loop)", async () => {
    await handleAgent({
      messages: [{ id: "u", role: "user", parts: [{ type: "text", text: "weather?" }] }],
    });
    const args = mockedStreamText.mock.calls[0][0] as Record<string, unknown>;
    expect(args.maxOutputTokens).toBe(4096);
  });

  it("returns 200 SSE", async () => {
    const resp = await handleAgent({
      messages: [{ id: "u", role: "user", parts: [{ type: "text", text: "weather?" }] }],
    });
    expect(resp.status).toBe(200);
  });

  it("emits X-Kimi-Mode: agent header", async () => {
    const resp = await handleAgent({
      messages: [{ id: "u", role: "user", parts: [{ type: "text", text: "weather?" }] }],
    });
    expect(resp.headers.get("X-Kimi-Mode")).toBe("agent");
  });

  it("returns 401 on missing key", async () => {
    const { buildKimiClient } = await import("@/lib/ai/kimi/shared");
    (buildKimiClient as unknown as ReturnType<typeof vi.fn>).mockRejectedValueOnce(
      Object.assign(new Error("KIMI_API_KEY not configured"), { name: "KimiKeyMissingError" }),
    );
    const resp = await handleAgent({
      messages: [{ id: "u", role: "user", parts: [{ type: "text", text: "weather?" }] }],
    });
    expect(resp.status).toBe(401);
  });

  it("uses temperature 0.7", async () => {
    await handleAgent({
      messages: [{ id: "u", role: "user", parts: [{ type: "text", text: "weather?" }] }],
    });
    const args = mockedStreamText.mock.calls[0][0] as Record<string, unknown>;
    expect(args.temperature).toBe(0.7);
  });
});
```

- [ ] **Step 2: Run tests to verify failure**

Run: `cd src/web && npm test -- tests/kimi/agent.test.ts`
Expected: FAIL.

- [ ] **Step 3: Implement Agent handler**

Replace `src/web/src/lib/ai/kimi/agent.ts`:

```typescript
// src/web/src/lib/ai/kimi/agent.ts
import "server-only";
import { convertToModelMessages, stepCountIs, streamText } from "ai";
import {
  buildKimiClient,
  extractMessagesForKimi,
  formatKimiError,
  KimiKeyMissingError,
  loadKimiPersona,
} from "./shared";
import { webSearchTool } from "@/lib/tools/web-search";
import type { KimiModeRequest } from "./index";

export async function handleAgent(body: KimiModeRequest): Promise<Response> {
  let client;
  try {
    client = await buildKimiClient();
  } catch (err) {
    if (err instanceof KimiKeyMissingError) {
      return new Response(
        `data: ${JSON.stringify({
          type: "kimi-error",
          status: 401,
          message: "Kimi API key missing or invalid",
        })}\n\ndata: [DONE]\n\n`,
        { status: 401, headers: { "Content-Type": "text/event-stream" } },
      );
    }
    return formatKimiError(err);
  }

  try {
    const messages = await convertToModelMessages(
      extractMessagesForKimi(body.messages),
    );
    const system =
      body.system ??
      loadKimiPersona({
        suffix: `You can search the web with the webSearch tool. Use it when the answer requires \
real-time facts (weather, news, prices, today's events). For general knowledge already in your \
training, answer directly without searching.`,
      });

    const result = streamText({
      model: client.model,
      system,
      messages,
      temperature: 0.7,
      maxOutputTokens: 4096,
      tools: { webSearch: webSearchTool },
      stopWhen: stepCountIs(5),
      providerOptions: {
        kimi: {
          // $web_search builtin and `thinking` are mutually exclusive
          // per Moonshot K2.6 docs; we use webSearchTool (DuckDuckGo)
          // and keep thinking disabled.
          thinking: { type: "disabled" },
        },
      },
      onError: (err) => {
        console.error("[kimi-agent] streamText error:", err);
      },
    });

    result.consumeStream();
    return result.toUIMessageStreamResponse({
      headers: { "X-Kimi-Mode": "agent" },
    });
  } catch (err) {
    return formatKimiError(err);
  }
}
```

- [ ] **Step 4: Run tests to verify pass**

Run: `cd src/web && npm test -- tests/kimi/agent.test.ts`
Expected: 8 tests passing.

- [ ] **Step 5: Create KimiToolTrace UI component**

```typescript
// src/web/src/components/chat/kimi-tool-trace.tsx
"use client";
import { Search, FileText, AlertCircle } from "lucide-react";
import { cn } from "@/lib/utils";

export type ToolTraceEntry = {
  id: string;
  toolName: string;
  // Concise summary string (e.g., the search query, or the URL fetched)
  summary: string;
  status: "pending" | "ok" | "error";
  // Optional 1-line preview of the result (e.g., "5 results" or "200 OK 12kb")
  resultSummary?: string;
};

export function KimiToolTrace({ entries }: { entries: ToolTraceEntry[] }) {
  if (entries.length === 0) return null;
  return (
    <div className="mb-3 space-y-1.5">
      {entries.map((e) => (
        <div
          key={e.id}
          className="flex items-center gap-2 rounded-md border border-border/40 bg-muted/20 px-3 py-1.5 text-[12px]"
        >
          {e.status === "error" ? (
            <AlertCircle className="size-3.5 shrink-0 text-destructive/80" />
          ) : e.toolName === "webSearch" ? (
            <Search
              className={cn(
                "size-3.5 shrink-0",
                e.status === "pending" ? "text-primary animate-pulse" : "text-primary",
              )}
            />
          ) : (
            <FileText className="size-3.5 shrink-0 text-muted-foreground" />
          )}
          <span className="flex-1 truncate">
            <span className="font-medium text-foreground/90">{e.toolName}</span>
            <span className="text-muted-foreground"> · {e.summary}</span>
          </span>
          {e.resultSummary && (
            <span className="shrink-0 text-[11px] text-muted-foreground/80">
              {e.resultSummary}
            </span>
          )}
        </div>
      ))}
    </div>
  );
}
```

- [ ] **Step 6: Wire KimiToolTrace into message.tsx**

In `src/web/src/components/chat/message.tsx`, add to the imports:

```typescript
import { KimiToolTrace, type ToolTraceEntry } from "./kimi-tool-trace";
```

Add a helper that materializes `data-kimi-tool-trace` parts AND the standard `tool-call`/`tool-result` parts that `streamText` emits when tools are bound. Both forms should produce the same `ToolTraceEntry` array — bonus from using the standard parts is they work for ALL tool-bound modes, not just K2.6.

After the `kimiReasoningFromMessage` helper, add:

```typescript
function toolTraceFromMessage(parts: UIMessage["parts"]): ToolTraceEntry[] {
  const out: ToolTraceEntry[] = [];
  // First pass: gather call-shape parts. Vercel AI SDK 6 emits these as
  // `tool-{toolName}` (e.g. `tool-webSearch`) parts with `state` and
  // `input`/`output` fields. Treat each part with toolCallId as one entry.
  // Reference: AI SDK 6 docs on UIMessage parts.
  const seenIds = new Set<string>();
  for (const p of parts) {
    if (typeof p !== "object" || p === null) continue;
    const obj = p as Record<string, unknown>;
    const t = obj.type as string | undefined;
    if (!t) continue;

    // Standard SDK shape: type="tool-<name>" with state field.
    if (t.startsWith("tool-")) {
      const toolName = t.slice("tool-".length);
      const id = (obj.toolCallId as string | undefined) ?? `${toolName}-${out.length}`;
      if (seenIds.has(id)) continue;
      seenIds.add(id);
      const state = (obj.state as string | undefined) ?? "input-streaming";
      const input = obj.input as Record<string, unknown> | undefined;
      const output = obj.output as unknown;
      const summary =
        toolName === "webSearch"
          ? (input?.query as string | undefined) ?? "(query…)"
          : JSON.stringify(input ?? {}).slice(0, 80);
      const status: ToolTraceEntry["status"] =
        state === "output-available" || state === "output-error"
          ? state === "output-error"
            ? "error"
            : "ok"
          : "pending";
      const resultSummary =
        status === "ok" && output && toolName === "webSearch"
          ? `${(output as { results?: unknown[] }).results?.length ?? 0} results`
          : undefined;
      out.push({ id, toolName, summary, status, resultSummary });
    }

    // Fallback: custom kimi-tool-trace data part (used if we manually
    // emit tool events from a custom transform — kept for forward-
    // compat with ts2/3 Moonshot $web_search if we add it later).
    if (t === "data-kimi-tool-trace") {
      const id = (obj.id as string | undefined) ?? `custom-${out.length}`;
      if (seenIds.has(id)) continue;
      seenIds.add(id);
      out.push({
        id,
        toolName: (obj.toolName as string) ?? "tool",
        summary: (obj.summary as string) ?? "",
        status: ((obj.status as string) ?? "ok") as ToolTraceEntry["status"],
        resultSummary: obj.resultSummary as string | undefined,
      });
    }
  }
  return out;
}
```

In the Message body, just after `const kimiReasoning = ...`, add:

```typescript
  const toolTrace = toolTraceFromMessage(message.parts);
```

In the assistant render branch, insert KimiToolTrace BELOW KimiReasoning and ABOVE the main message body:

```typescript
        <div className="w-full">
          {kimiReasoning ? (
            <KimiReasoning text={kimiReasoning} streaming={Boolean(isStreaming && !text)} />
          ) : null}
          {toolTrace.length > 0 ? <KimiToolTrace entries={toolTrace} /> : null}
          {reasoning ? (
            <ReasoningBlock reasoning={reasoning} streaming={reasoningStreaming} />
          ) : null}
          {/* …rest unchanged… */}
```

- [ ] **Step 7: Typecheck**

Run: `cd src/web && npx tsc --noEmit`
Expected: zero errors.

- [ ] **Step 8: Live smoke test (manual)**

Run: `cd src/web && KIMI_K2_MODES_ENABLED=1 npm run dev:next`

In the chat UI:
- Select `kimi-k2-agent`
- Send: "what's the weather in Paris right now?"
- Expected: a tool-trace entry appears: "🔍 webSearch · weather in Paris" with "5 results" once complete; then a final answer in the main body.

- [ ] **Step 9: Commit**

```bash
git add src/web/src/lib/ai/kimi/agent.ts src/web/src/components/chat/kimi-tool-trace.tsx src/web/src/components/chat/message.tsx src/web/tests/kimi/agent.test.ts
git commit -m "kimi-modes: Agent handler + KimiToolTrace UI (Task 6)"
```

---

## Task 7: Implement Swarm budget tracker + Swarm handler + KimiSwarmProgress UI

**Files:**
- Create: `src/web/src/lib/ai/kimi/budget.ts`
- Modify: `src/web/src/lib/ai/kimi/swarm.ts`
- Create: `src/web/src/components/chat/kimi-swarm-progress.tsx`
- Modify: `src/web/src/components/chat/message.tsx` (render KimiSwarmProgress on data parts)
- Create: `src/web/tests/kimi/budget.test.ts`
- Create: `src/web/tests/kimi/swarm.test.ts`

Swarm is the most complex mode: decompose → fan out → aggregate. The budget tracker uses Redis INCRBY with daily expiry to enforce per-day spend.

- [ ] **Step 1: Write the budget tests**

```typescript
// src/web/tests/kimi/budget.test.ts
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";

const redisMock = {
  incrbyfloat: vi.fn(),
  expireat: vi.fn(),
  get: vi.fn(),
};
vi.mock("ioredis", () => ({
  default: vi.fn().mockImplementation(() => redisMock),
}));

import { reserveSwarmBudget, recordSwarmSpend } from "@/lib/ai/kimi/budget";

describe("budget guard", () => {
  beforeEach(() => {
    redisMock.incrbyfloat.mockReset();
    redisMock.expireat.mockReset();
    redisMock.get.mockReset();
    process.env.KIMI_SWARM_DAILY_BUDGET_USD = "5";
  });
  afterEach(() => {
    delete process.env.KIMI_SWARM_DAILY_BUDGET_USD;
  });

  it("allows when current spend below budget", async () => {
    redisMock.get.mockResolvedValueOnce("2.50");
    const r = await reserveSwarmBudget(0.06);
    expect(r.ok).toBe(true);
    expect(r.remaining).toBeCloseTo(2.5);
  });

  it("denies when current + estimated would exceed budget", async () => {
    redisMock.get.mockResolvedValueOnce("4.99");
    const r = await reserveSwarmBudget(0.06);
    expect(r.ok).toBe(false);
    expect(r.reason).toMatch(/budget/i);
  });

  it("denies when current already at/over budget", async () => {
    redisMock.get.mockResolvedValueOnce("5.00");
    const r = await reserveSwarmBudget(0.01);
    expect(r.ok).toBe(false);
  });

  it("treats null current as 0", async () => {
    redisMock.get.mockResolvedValueOnce(null);
    const r = await reserveSwarmBudget(0.06);
    expect(r.ok).toBe(true);
    expect(r.remaining).toBeCloseTo(5);
  });

  it("recordSwarmSpend INCRs by actual cost and sets expireat to end-of-day", async () => {
    redisMock.incrbyfloat.mockResolvedValueOnce("1.06");
    await recordSwarmSpend(0.06);
    expect(redisMock.incrbyfloat).toHaveBeenCalledTimes(1);
    expect(redisMock.expireat).toHaveBeenCalledTimes(1);
    // expireat should be a Unix ts at end of UTC day
    const expireTs = redisMock.expireat.mock.calls[0][1] as number;
    const now = Math.floor(Date.now() / 1000);
    expect(expireTs).toBeGreaterThan(now);
    expect(expireTs).toBeLessThan(now + 24 * 60 * 60 + 60);
  });

  it("respects custom KIMI_SWARM_DAILY_BUDGET_USD", async () => {
    process.env.KIMI_SWARM_DAILY_BUDGET_USD = "1.00";
    redisMock.get.mockResolvedValueOnce("0.95");
    const r = await reserveSwarmBudget(0.06);
    expect(r.ok).toBe(false);
  });

  it("falls back to default $5 when env var unset", async () => {
    delete process.env.KIMI_SWARM_DAILY_BUDGET_USD;
    redisMock.get.mockResolvedValueOnce("4.95");
    const r = await reserveSwarmBudget(0.06);
    expect(r.ok).toBe(false);
  });
});
```

- [ ] **Step 2: Run budget tests to verify failure**

Run: `cd src/web && npm test -- tests/kimi/budget.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement budget tracker**

```typescript
// src/web/src/lib/ai/kimi/budget.ts
import "server-only";
import Redis from "ioredis";

let _client: Redis | null = null;
function client(): Redis {
  if (_client) return _client;
  const url = process.env.REDIS_URL ?? "redis://127.0.0.1:6379";
  _client = new Redis(url);
  return _client;
}

const DEFAULT_BUDGET_USD = 5.0;

function dailyKey(): string {
  const d = new Date();
  const yyyy = d.getUTCFullYear();
  const mm = String(d.getUTCMonth() + 1).padStart(2, "0");
  const dd = String(d.getUTCDate()).padStart(2, "0");
  return `kimi:swarm:spend:${yyyy}-${mm}-${dd}`;
}

function endOfUtcDayTs(): number {
  const d = new Date();
  d.setUTCHours(23, 59, 59, 999);
  return Math.floor(d.getTime() / 1000);
}

function dailyBudget(): number {
  const env = process.env.KIMI_SWARM_DAILY_BUDGET_USD;
  if (!env) return DEFAULT_BUDGET_USD;
  const n = Number(env);
  return Number.isFinite(n) && n > 0 ? n : DEFAULT_BUDGET_USD;
}

export async function reserveSwarmBudget(
  estimatedCostUsd: number,
): Promise<{ ok: true; remaining: number } | { ok: false; reason: string; remaining: number }> {
  const c = client();
  const budget = dailyBudget();
  const raw = await c.get(dailyKey());
  const current = raw ? Number(raw) : 0;
  const remaining = Math.max(0, budget - current);
  if (current + estimatedCostUsd > budget) {
    return {
      ok: false,
      reason: `Per-day Swarm budget ($${budget.toFixed(2)}) reached. Current spend: $${current.toFixed(2)}.`,
      remaining,
    };
  }
  return { ok: true, remaining };
}

export async function recordSwarmSpend(actualCostUsd: number): Promise<void> {
  const c = client();
  const key = dailyKey();
  await c.incrbyfloat(key, actualCostUsd);
  await c.expireat(key, endOfUtcDayTs());
}
```

- [ ] **Step 4: Run budget tests to verify pass**

Run: `cd src/web && npm test -- tests/kimi/budget.test.ts`
Expected: 7 tests passing.

- [ ] **Step 5: Write the swarm tests**

```typescript
// src/web/tests/kimi/swarm.test.ts
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";

vi.mock("ai", async () => {
  const actual = await vi.importActual<typeof import("ai")>("ai");
  return {
    ...actual,
    streamText: vi.fn(),
    generateText: vi.fn(),
    generateObject: vi.fn(),
  };
});

vi.mock("@/lib/ai/kimi/shared", async () => {
  const actual = await vi.importActual<typeof import("@/lib/ai/kimi/shared")>(
    "@/lib/ai/kimi/shared",
  );
  return {
    ...actual,
    buildKimiClient: vi.fn(async () => ({
      model: { _mock: "kimi-k2.6" },
      apiKey: "test-key",
      baseURL: "https://api.moonshot.ai/v1",
    })),
  };
});

vi.mock("@/lib/ai/kimi/budget", () => ({
  reserveSwarmBudget: vi.fn(async () => ({ ok: true, remaining: 5 })),
  recordSwarmSpend: vi.fn(async () => undefined),
}));

import { streamText, generateText, generateObject } from "ai";
import { handleSwarm } from "@/lib/ai/kimi/swarm";
import { reserveSwarmBudget } from "@/lib/ai/kimi/budget";

const mockedStream = streamText as unknown as ReturnType<typeof vi.fn>;
const mockedGenText = generateText as unknown as ReturnType<typeof vi.fn>;
const mockedGenObj = generateObject as unknown as ReturnType<typeof vi.fn>;
const mockedReserve = reserveSwarmBudget as unknown as ReturnType<typeof vi.fn>;

function fakeStreamResult() {
  return {
    toUIMessageStreamResponse: () =>
      new Response("ok", { status: 200, headers: { "Content-Type": "text/event-stream" } }),
    consumeStream: () => undefined,
  };
}

describe("handleSwarm", () => {
  beforeEach(() => {
    mockedStream.mockReset();
    mockedGenText.mockReset();
    mockedGenObj.mockReset();
    mockedReserve.mockReset();
    mockedReserve.mockResolvedValue({ ok: true, remaining: 5 });
    mockedStream.mockReturnValue(fakeStreamResult());
    mockedGenText.mockResolvedValue({
      text: "sub-agent reply",
      usage: { inputTokens: 100, outputTokens: 50 },
    });
    mockedGenObj.mockResolvedValue({
      object: {
        subtasks: [
          { role: "researcher-A", prompt: "research aspect A" },
          { role: "researcher-B", prompt: "research aspect B" },
          { role: "researcher-C", prompt: "research aspect C" },
        ],
      },
    });
    process.env.KIMI_API_KEY = "test-key";
  });

  it("calls generateObject to decompose first", async () => {
    await handleSwarm({
      messages: [{ id: "u", role: "user", parts: [{ type: "text", text: "compare frameworks" }] }],
    });
    expect(mockedGenObj).toHaveBeenCalledOnce();
  });

  it("fans out generateText calls — one per subtask", async () => {
    await handleSwarm({
      messages: [{ id: "u", role: "user", parts: [{ type: "text", text: "compare frameworks" }] }],
    });
    expect(mockedGenText).toHaveBeenCalledTimes(3);
  });

  it("passes prompt_cache_key for shared input cache", async () => {
    await handleSwarm({
      messages: [{ id: "u", role: "user", parts: [{ type: "text", text: "compare frameworks" }] }],
    });
    const firstCall = mockedGenText.mock.calls[0][0] as Record<string, unknown>;
    const po = firstCall.providerOptions as { kimi?: { prompt_cache_key?: string } };
    expect(po?.kimi?.prompt_cache_key).toMatch(/^swarm-/);
  });

  it("calls streamText to aggregate after fan-out", async () => {
    await handleSwarm({
      messages: [{ id: "u", role: "user", parts: [{ type: "text", text: "compare frameworks" }] }],
    });
    expect(mockedStream).toHaveBeenCalledOnce();
  });

  it("falls back to Instant when decompose returns empty subtasks", async () => {
    mockedGenObj.mockResolvedValueOnce({ object: { subtasks: [] } });
    await handleSwarm({
      messages: [{ id: "u", role: "user", parts: [{ type: "text", text: "trivial question" }] }],
    });
    expect(mockedGenText).not.toHaveBeenCalled();
    expect(mockedStream).toHaveBeenCalledOnce();
    const args = mockedStream.mock.calls[0][0] as Record<string, unknown>;
    const po = args.providerOptions as { kimi?: { thinking?: { type?: string } } };
    expect(po?.kimi?.thinking?.type).toBe("disabled");
  });

  it("survives when one sub-agent rejects (Promise.allSettled)", async () => {
    mockedGenText
      .mockResolvedValueOnce({ text: "result A", usage: { inputTokens: 50, outputTokens: 25 } })
      .mockRejectedValueOnce(new Error("transient API blip"))
      .mockResolvedValueOnce({ text: "result C", usage: { inputTokens: 50, outputTokens: 25 } });
    await handleSwarm({
      messages: [{ id: "u", role: "user", parts: [{ type: "text", text: "compare frameworks" }] }],
    });
    expect(mockedStream).toHaveBeenCalledOnce();
    const args = mockedStream.mock.calls[0][0] as Record<string, unknown>;
    const messages = args.messages as Array<{ content?: unknown }>;
    const aggInput = JSON.stringify(messages);
    expect(aggInput).toContain("result A");
    expect(aggInput).toContain("result C");
    expect(aggInput).toMatch(/sub-agent failed/i);
  });

  it("returns budget-exceeded SSE when reserveSwarmBudget denies", async () => {
    mockedReserve.mockResolvedValueOnce({
      ok: false,
      reason: "Per-day Swarm budget ($5.00) reached. Current spend: $5.01.",
      remaining: 0,
    });
    const resp = await handleSwarm({
      messages: [{ id: "u", role: "user", parts: [{ type: "text", text: "compare frameworks" }] }],
    });
    expect(resp.status).toBe(429);
    const body = await resp.text();
    expect(body).toMatch(/budget/i);
  });

  it("emits X-Kimi-Mode: swarm response header", async () => {
    const resp = await handleSwarm({
      messages: [{ id: "u", role: "user", parts: [{ type: "text", text: "compare frameworks" }] }],
    });
    expect(resp.headers.get("X-Kimi-Mode")).toBe("swarm");
  });

  it("returns 401 on missing key", async () => {
    const { buildKimiClient } = await import("@/lib/ai/kimi/shared");
    (buildKimiClient as unknown as ReturnType<typeof vi.fn>).mockRejectedValueOnce(
      Object.assign(new Error("KIMI_API_KEY not configured"), { name: "KimiKeyMissingError" }),
    );
    const resp = await handleSwarm({
      messages: [{ id: "u", role: "user", parts: [{ type: "text", text: "compare frameworks" }] }],
    });
    expect(resp.status).toBe(401);
  });

  it("aggregator system prompt instructs synthesis only from sources", async () => {
    await handleSwarm({
      messages: [{ id: "u", role: "user", parts: [{ type: "text", text: "compare frameworks" }] }],
    });
    const args = mockedStream.mock.calls[0][0] as Record<string, unknown>;
    expect(args.system).toMatch(/synthesize.*these.*sources/i);
  });
});
```

- [ ] **Step 6: Run swarm tests to verify failure**

Run: `cd src/web && npm test -- tests/kimi/swarm.test.ts`
Expected: FAIL.

- [ ] **Step 7: Implement Swarm handler**

Replace `src/web/src/lib/ai/kimi/swarm.ts`:

```typescript
// src/web/src/lib/ai/kimi/swarm.ts
import "server-only";
import {
  convertToModelMessages,
  generateObject,
  generateText,
  streamText,
} from "ai";
import { z } from "zod";
import {
  buildKimiClient,
  extractMessagesForKimi,
  formatKimiError,
  KimiKeyMissingError,
  loadKimiPersona,
} from "./shared";
import { reserveSwarmBudget, recordSwarmSpend } from "./budget";
import type { KimiModeRequest } from "./index";

const SwarmPlanSchema = z.object({
  subtasks: z
    .array(
      z.object({
        role: z.string().describe("Short role name, e.g. 'researcher-pricing'"),
        prompt: z
          .string()
          .describe("Self-contained instruction for this sub-agent"),
      }),
    )
    .max(5),
});

// Rough K2.6 pricing (per the public Moonshot price page; verify before launch).
// Conservative estimate: $0.0005/1K input, $0.005/1K output.
const PRICE_INPUT_PER_1K = 0.0005;
const PRICE_OUTPUT_PER_1K = 0.005;

// Pre-flight estimate: 5 subtasks × (~500 input tokens + ~500 output tokens)
// = 5 × ($0.00025 + $0.0025) = ~$0.014. Aggregator: ~2K input + ~500 output
// = ~$0.0035. Total estimated: ~$0.018. Round up for safety.
const SWARM_ESTIMATED_COST_USD = 0.06;

type SubResult = { role: string; text: string; failed?: boolean; error?: string };

export async function handleSwarm(body: KimiModeRequest): Promise<Response> {
  let client;
  try {
    client = await buildKimiClient();
  } catch (err) {
    if (err instanceof KimiKeyMissingError) {
      return new Response(
        `data: ${JSON.stringify({
          type: "kimi-error",
          status: 401,
          message: "Kimi API key missing or invalid",
        })}\n\ndata: [DONE]\n\n`,
        { status: 401, headers: { "Content-Type": "text/event-stream" } },
      );
    }
    return formatKimiError(err);
  }

  // Budget gate: refuse early if today's spend would push over the limit.
  const reservation = await reserveSwarmBudget(SWARM_ESTIMATED_COST_USD);
  if (!reservation.ok) {
    const body = `data: ${JSON.stringify({
      type: "kimi-error",
      status: 429,
      message: reservation.reason,
      mode: "swarm",
    })}\n\ndata: [DONE]\n\n`;
    return new Response(body, {
      status: 429,
      headers: {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-store",
      },
    });
  }

  try {
    const messages = await convertToModelMessages(
      extractMessagesForKimi(body.messages),
    );
    const userPrompt = messages
      .filter((m) => m.role === "user")
      .map((m) => {
        if (typeof m.content === "string") return m.content;
        return (m.content as Array<{ type: string; text?: string }>)
          .filter((p) => p.type === "text")
          .map((p) => p.text ?? "")
          .join("");
      })
      .join("\n\n");

    // Step 1 — Decompose.
    const decompose = await generateObject({
      model: client.model,
      schema: SwarmPlanSchema,
      system: `You are a planner. Break the user's request into 3-5 parallel \
sub-agent tasks, each with a focused role and a complete, self-contained prompt. \
If the request is too simple to benefit from parallelism, return an empty subtasks array.`,
      prompt: userPrompt,
      providerOptions: {
        kimi: { thinking: { type: "disabled" } },
      },
    });

    const plan = decompose.object;
    const sessionId = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    const cacheKey = `swarm-${sessionId}`;

    // Step 2a — Empty plan: fall back to Instant single call.
    if (!plan.subtasks || plan.subtasks.length === 0) {
      const fallback = streamText({
        model: client.model,
        system: loadKimiPersona(),
        messages,
        temperature: 0.6,
        maxOutputTokens: 1024,
        providerOptions: {
          kimi: { thinking: { type: "disabled" } },
        },
      });
      fallback.consumeStream();
      return fallback.toUIMessageStreamResponse({
        headers: {
          "X-Kimi-Mode": "swarm",
          "X-Kimi-Swarm-Fallback": "instant-empty-plan",
        },
      });
    }

    // Step 2b — Fan out.
    const subPromises = plan.subtasks.map((t) =>
      generateText({
        model: client.model,
        system: `You are a sub-agent with role "${t.role}". Answer only your task.`,
        prompt: t.prompt,
        temperature: 0.7,
        maxOutputTokens: 800,
        providerOptions: {
          kimi: {
            thinking: { type: "disabled" },
            prompt_cache_key: cacheKey,
          },
        },
      }),
    );

    const settled = await Promise.allSettled(subPromises);
    let totalInput = 0;
    let totalOutput = 0;
    const subResults: SubResult[] = settled.map((r, i) => {
      const role = plan.subtasks[i].role;
      if (r.status === "fulfilled") {
        totalInput += r.value.usage?.inputTokens ?? 0;
        totalOutput += r.value.usage?.outputTokens ?? 0;
        return { role, text: r.value.text };
      }
      return {
        role,
        text: `(this sub-agent failed: ${(r.reason as Error)?.message ?? "unknown"})`,
        failed: true,
        error: (r.reason as Error)?.message,
      };
    });

    // Step 3 — Aggregate (streamed).
    const aggregatorPrompt = subResults
      .map((r) => `## ${r.role}\n${r.text}`)
      .join("\n\n");

    const aggregator = streamText({
      model: client.model,
      system: `You are JARVIS. Synthesize the sub-agent results below into ONE coherent \
reply for the user. Use ONLY the information present in these sources — do not invent \
facts. If sources contradict, mention the disagreement. Keep it focused and well-structured.`,
      messages: [
        ...messages,
        {
          role: "user",
          content: `Sub-agent results:\n\n${aggregatorPrompt}\n\nNow write the synthesized reply.`,
        },
      ],
      temperature: 0.6,
      maxOutputTokens: 4096,
      providerOptions: {
        kimi: { thinking: { type: "disabled" } },
      },
      onFinish: async ({ totalUsage }) => {
        const aggInput = totalUsage?.inputTokens ?? 0;
        const aggOutput = totalUsage?.outputTokens ?? 0;
        const cost =
          ((totalInput + aggInput) * PRICE_INPUT_PER_1K) / 1000 +
          ((totalOutput + aggOutput) * PRICE_OUTPUT_PER_1K) / 1000;
        try {
          await recordSwarmSpend(cost);
        } catch (err) {
          console.warn("[kimi-swarm] recordSwarmSpend failed:", err);
        }
      },
      onError: (err) => {
        console.error("[kimi-swarm] aggregator streamText error:", err);
      },
    });

    aggregator.consumeStream();
    return aggregator.toUIMessageStreamResponse({
      headers: {
        "X-Kimi-Mode": "swarm",
        "X-Kimi-Swarm-Subagents": String(subResults.length),
        "X-Kimi-Swarm-Failures": String(subResults.filter((r) => r.failed).length),
      },
    });
  } catch (err) {
    return formatKimiError(err);
  }
}
```

- [ ] **Step 8: Run swarm tests to verify pass**

Run: `cd src/web && npm test -- tests/kimi/swarm.test.ts`
Expected: 10 tests passing.

If a test fails because the aggregator's `messages` shape doesn't match the assertion (the test stringifies messages and looks for "result A"/"result C"/"sub-agent failed"), check that the aggregator content text is appended as a `user` message — not concatenated into a system string — so the substrings appear in the JSON.

- [ ] **Step 9: Create KimiSwarmProgress UI component**

```typescript
// src/web/src/components/chat/kimi-swarm-progress.tsx
"use client";
import { Network, CheckCircle2 } from "lucide-react";
import { cn } from "@/lib/utils";

export function KimiSwarmProgress({
  total,
  completed,
  current,
  done,
}: {
  total: number;
  completed: number;
  current?: string;
  // True once the aggregator started streaming (replace card with text)
  done: boolean;
}) {
  if (done && completed >= total) return null;
  if (total === 0) return null;
  const pct = Math.round((completed / total) * 100);
  return (
    <div className="mb-3 rounded-lg border border-border/40 bg-muted/20 px-3 py-2.5">
      <div className="flex items-center gap-2 text-[12px]">
        {completed >= total ? (
          <CheckCircle2 className="size-3.5 shrink-0 text-primary" />
        ) : (
          <Network className="size-3.5 shrink-0 text-primary animate-pulse" />
        )}
        <span className="flex-1 font-medium text-foreground/90">
          {completed >= total
            ? "Synthesizing…"
            : `Coordinating ${total} agents`}
        </span>
        <span className="text-[11px] text-muted-foreground">
          {completed}/{total}
        </span>
      </div>
      <div className="mt-2 h-1 w-full overflow-hidden rounded-full bg-muted/40">
        <div
          className={cn("h-full bg-primary transition-all duration-300")}
          style={{ width: `${pct}%` }}
        />
      </div>
      {current && (
        <div className="mt-1.5 truncate text-[11px] text-muted-foreground">
          Latest: {current}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 10: Wire KimiSwarmProgress into message.tsx**

In `src/web/src/components/chat/message.tsx`, add to imports:

```typescript
import { KimiSwarmProgress } from "./kimi-swarm-progress";
```

After the `toolTraceFromMessage` helper, add:

```typescript
function swarmStatusFromMessage(
  parts: UIMessage["parts"],
): { total: number; completed: number; current?: string } | null {
  let last: { total: number; completed: number; current?: string } | null = null;
  for (const p of parts) {
    if (typeof p !== "object" || p === null) continue;
    const obj = p as Record<string, unknown>;
    if (obj.type === "data-kimi-swarm-status") {
      last = {
        total: Number(obj.total ?? 0),
        completed: Number(obj.completed ?? 0),
        current: obj.current as string | undefined,
      };
    }
  }
  return last;
}
```

In the Message body, just after `const toolTrace = ...`, add:

```typescript
  const swarmStatus = swarmStatusFromMessage(message.parts);
```

In the assistant render branch, insert KimiSwarmProgress BELOW KimiToolTrace and ABOVE the main message body:

```typescript
        <div className="w-full">
          {kimiReasoning ? (
            <KimiReasoning text={kimiReasoning} streaming={Boolean(isStreaming && !text)} />
          ) : null}
          {toolTrace.length > 0 ? <KimiToolTrace entries={toolTrace} /> : null}
          {swarmStatus ? (
            <KimiSwarmProgress
              total={swarmStatus.total}
              completed={swarmStatus.completed}
              current={swarmStatus.current}
              done={Boolean(text)}
            />
          ) : null}
          {/* …rest unchanged… */}
```

- [ ] **Step 11: Add data-part emission in swarm.ts**

The Swarm handler currently doesn't emit `kimi-swarm-status` data parts because the SDK's `streamText` only owns the aggregator stream — sub-agent fan-out happens in `Promise.allSettled` BEFORE the response stream starts. To surface progress, prepend a custom prefix stream.

Update `src/web/src/lib/ai/kimi/swarm.ts` Step 2b/3 — switch from "wait for all then stream aggregator" to "emit progress data parts as a prefix, then stream aggregator". Use a manually-constructed `ReadableStream` that writes the swarm-status SSE events first, then pipes the aggregator's body through.

Replace the aggregator return block (everything from `const aggregator = streamText(...)` to `return aggregator.toUIMessageStreamResponse(...)`) with:

```typescript
    // Build a composite SSE stream:
    //   1. emit kimi-swarm-status data parts as sub-agents complete
    //   2. then stream the aggregator's UI message body
    const encoder = new TextEncoder();
    const composite = new ReadableStream({
      async start(controller) {
        // Emit initial status
        controller.enqueue(
          encoder.encode(
            `data: ${JSON.stringify({
              type: "data-kimi-swarm-status",
              total: plan.subtasks.length,
              completed: 0,
            })}\n\n`,
          ),
        );
        // Emit per-completion updates
        for (let i = 0; i < subResults.length; i++) {
          controller.enqueue(
            encoder.encode(
              `data: ${JSON.stringify({
                type: "data-kimi-swarm-status",
                total: plan.subtasks.length,
                completed: i + 1,
                current: subResults[i].role,
              })}\n\n`,
            ),
          );
        }
        // Pipe aggregator stream through
        const aggResp = aggregator.toUIMessageStreamResponse();
        const reader = aggResp.body?.getReader();
        if (!reader) {
          controller.close();
          return;
        }
        try {
          while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            controller.enqueue(value);
          }
        } finally {
          reader.releaseLock();
          controller.close();
        }
      },
    });

    aggregator.consumeStream();
    return new Response(composite, {
      status: 200,
      headers: {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-store",
        "X-Kimi-Mode": "swarm",
        "X-Kimi-Swarm-Subagents": String(subResults.length),
        "X-Kimi-Swarm-Failures": String(subResults.filter((r) => r.failed).length),
      },
    });
```

Note: this collapses progress into a "before aggregator" prefix because the Promise.allSettled already returned. To get true "while streaming" progress (each sub-agent firing its own update as it lands), refactor fan-out to use a shared mutable `completedCount` + emit on each `then`. That's more complex; deferred to v2 if user feedback wants live counter.

For v1, the progress card shows briefly between "Coordinating 3 agents" → "Synthesizing…" → final text. Acceptable as a first pass.

- [ ] **Step 12: Re-run swarm tests**

Run: `cd src/web && npm test -- tests/kimi/swarm.test.ts`
Expected: 10 tests passing. Adjust the "uses streamText" test if Step 11's refactor changed how the aggregator response shape is asserted. The mock should still see `streamText` called once (for the aggregator).

- [ ] **Step 13: Live smoke test (manual)**

Run: `cd src/web && KIMI_K2_MODES_ENABLED=1 npm run dev:next`

In the chat UI:
- Select `kimi-k2-swarm`
- Send: "compare React, Vue, and Svelte for a small startup MVP — performance, ecosystem, and learning curve"
- Expected: a "Coordinating 3 agents 0/3" → 1/3 → 2/3 → 3/3 progress card appears, then a synthesized comparison streams in. Total time ~10-15s.

Repeat with a trivial query like "hi" — expected: empty-plan fallback fires, fast Instant-style answer with a `X-Kimi-Swarm-Fallback: instant-empty-plan` response header.

- [ ] **Step 14: Typecheck**

Run: `cd src/web && npx tsc --noEmit`
Expected: zero new errors.

- [ ] **Step 15: Commit**

```bash
git add src/web/src/lib/ai/kimi/budget.ts src/web/src/lib/ai/kimi/swarm.ts src/web/src/components/chat/kimi-swarm-progress.tsx src/web/src/components/chat/message.tsx src/web/tests/kimi/budget.test.ts src/web/tests/kimi/swarm.test.ts
git commit -m "kimi-modes: Swarm handler + budget guard + progress UI (Task 7)"
```

---

## Task 8: Integration tests (E2E with MSW)

**Files:**
- Create: `src/web/tests/_msw/server.ts` — MSW server setup
- Create: `src/web/tests/_msw/handlers.ts` — Moonshot endpoint mock handlers
- Create: `src/web/tests/kimi/e2e.test.ts` — 6 integration scenarios

Each scenario stubs the Moonshot `/v1/chat/completions` endpoint with a representative response, then drives the handler end-to-end and asserts on the streamed SSE output.

- [ ] **Step 1: Create MSW server**

```typescript
// src/web/tests/_msw/server.ts
import { setupServer } from "msw/node";
export const server = setupServer();
```

- [ ] **Step 2: Create handlers stub**

```typescript
// src/web/tests/_msw/handlers.ts
import { http, HttpResponse } from "msw";

// Helper: format a chunk as Moonshot's OpenAI-compatible SSE.
function chatChunk(delta: { content?: string; reasoning_content?: string; tool_calls?: unknown[] }) {
  return `data: ${JSON.stringify({
    id: "chatcmpl-test",
    object: "chat.completion.chunk",
    model: "kimi-k2.6",
    choices: [{ index: 0, delta, finish_reason: null }],
  })}\n\n`;
}

function chatDone() {
  return `data: [DONE]\n\n`;
}

export function instantSimpleAnswer() {
  return http.post("https://api.moonshot.ai/v1/chat/completions", () => {
    const body =
      chatChunk({ content: "4" }) +
      chatChunk({ content: "" }) +
      chatDone();
    return new HttpResponse(body, {
      status: 200,
      headers: { "Content-Type": "text/event-stream" },
    });
  });
}

export function thinkingWithReasoning() {
  return http.post("https://api.moonshot.ai/v1/chat/completions", () => {
    const body =
      chatChunk({ reasoning_content: "Let me compute 17*23..." }) +
      chatChunk({ reasoning_content: "20*23 = 460, minus 3*23 = 69, so 391." }) +
      chatChunk({ content: "391" }) +
      chatDone();
    return new HttpResponse(body, {
      status: 200,
      headers: { "Content-Type": "text/event-stream" },
    });
  });
}

export function agentWithToolCall() {
  let callCount = 0;
  return http.post("https://api.moonshot.ai/v1/chat/completions", () => {
    callCount++;
    if (callCount === 1) {
      // First call: model emits a tool_call
      const body =
        chatChunk({
          tool_calls: [
            {
              index: 0,
              id: "call-1",
              type: "function",
              function: {
                name: "webSearch",
                arguments: JSON.stringify({ query: "weather Paris" }),
              },
            },
          ],
        }) +
        `data: ${JSON.stringify({
          id: "chatcmpl-test",
          object: "chat.completion.chunk",
          model: "kimi-k2.6",
          choices: [{ index: 0, delta: {}, finish_reason: "tool_calls" }],
        })}\n\n` +
        chatDone();
      return new HttpResponse(body, {
        status: 200,
        headers: { "Content-Type": "text/event-stream" },
      });
    }
    // Second call: model produces final text using the tool result
    const body =
      chatChunk({ content: "It's 18°C and partly cloudy in Paris." }) +
      chatDone();
    return new HttpResponse(body, {
      status: 200,
      headers: { "Content-Type": "text/event-stream" },
    });
  });
}

export function moonshotDown() {
  return http.post("https://api.moonshot.ai/v1/chat/completions", () => {
    return new HttpResponse(JSON.stringify({ error: { message: "upstream timeout" } }), {
      status: 502,
      headers: { "Content-Type": "application/json" },
    });
  });
}
```

- [ ] **Step 3: Write E2E tests**

```typescript
// src/web/tests/kimi/e2e.test.ts
import { describe, it, expect, beforeAll, afterAll, afterEach, beforeEach } from "vitest";
import { server } from "../_msw/server";
import {
  instantSimpleAnswer,
  thinkingWithReasoning,
  agentWithToolCall,
  moonshotDown,
} from "../_msw/handlers";

beforeAll(() => server.listen({ onUnhandledRequest: "bypass" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

beforeEach(() => {
  process.env.KIMI_API_KEY = "test-key-e2e";
  process.env.KIMI_K2_MODES_ENABLED = "1";
});

async function readSse(resp: Response): Promise<string> {
  const text = await resp.text();
  return text;
}

describe("E2E: Instant", () => {
  it("returns the answer in SSE format", async () => {
    server.use(instantSimpleAnswer());
    const { handleInstant } = await import("@/lib/ai/kimi/instant");
    const resp = await handleInstant({
      messages: [{ id: "u", role: "user", parts: [{ type: "text", text: "what's 2+2?" }] }],
    });
    expect(resp.status).toBe(200);
    const body = await readSse(resp);
    expect(body).toContain("4");
  });
});

describe("E2E: Thinking", () => {
  it("surfaces reasoning_content separately from content", async () => {
    server.use(thinkingWithReasoning());
    const { handleThinking } = await import("@/lib/ai/kimi/thinking");
    const resp = await handleThinking({
      messages: [{ id: "u", role: "user", parts: [{ type: "text", text: "what's 17*23?" }] }],
    });
    expect(resp.status).toBe(200);
    const body = await readSse(resp);
    // The body should contain BOTH the reasoning content (somewhere)
    // and the final answer "391".
    expect(body).toContain("391");
    // Reasoning surfaces depend on whether the SDK forwards it as a
    // reasoning-* part or as a custom kimi-reasoning data part. Look for
    // either signal.
    expect(body).toMatch(/reasoning|Let me compute/i);
  });
});

describe("E2E: Agent", () => {
  it("performs a tool call and streams the final answer", async () => {
    server.use(agentWithToolCall());
    const { handleAgent } = await import("@/lib/ai/kimi/agent");
    const resp = await handleAgent({
      messages: [{ id: "u", role: "user", parts: [{ type: "text", text: "weather in Paris" }] }],
    });
    expect(resp.status).toBe(200);
    const body = await readSse(resp);
    expect(body).toContain("Paris");
    // tool-call event should have surfaced as either a tool-* part or a
    // kimi-tool-trace data part
    expect(body).toMatch(/webSearch|tool/i);
  });
});

describe("E2E: Swarm", () => {
  it("decomposes, fans out, and aggregates", async () => {
    // Three responses needed: 1 for generateObject (decompose),
    // N for the sub-agents, 1 for the aggregator streamText.
    let callIdx = 0;
    server.use(
      // Mock the decompose JSON-mode call: returns a JSON object with subtasks.
      // generateObject under the hood uses chat completions with json_schema, so
      // the mocked endpoint must return the JSON in the message content.
      // We respond differently per call index.
      // eslint-disable-next-line @typescript-eslint/no-require-imports
      // (use simple counter-based handler)
      // Using msw's http.post with an in-handler counter:
      // We re-register a handler that branches on callIdx.
      ...[
        // Re-use Moonshot URL for everything; differentiate by callIdx
      ],
    );
    server.use(
      // Single handler that branches by call count
      // eslint-disable-next-line @typescript-eslint/no-require-imports
      require("msw").http.post(
        "https://api.moonshot.ai/v1/chat/completions",
        () => {
          callIdx++;
          if (callIdx === 1) {
            // Decompose response
            const body = `data: ${JSON.stringify({
              id: "chatcmpl-decompose",
              object: "chat.completion.chunk",
              model: "kimi-k2.6",
              choices: [
                {
                  index: 0,
                  delta: {
                    content: JSON.stringify({
                      subtasks: [
                        { role: "perf", prompt: "compare performance" },
                        { role: "ecosystem", prompt: "compare ecosystem" },
                        { role: "learn", prompt: "compare learning curve" },
                      ],
                    }),
                  },
                  finish_reason: "stop",
                },
              ],
            })}\n\ndata: [DONE]\n\n`;
            // eslint-disable-next-line @typescript-eslint/no-require-imports
            const HttpResponse = require("msw").HttpResponse;
            return new HttpResponse(body, {
              status: 200,
              headers: { "Content-Type": "text/event-stream" },
            });
          }
          if (callIdx <= 4) {
            // Sub-agent responses
            const body = `data: ${JSON.stringify({
              id: `chatcmpl-sub-${callIdx}`,
              object: "chat.completion.chunk",
              model: "kimi-k2.6",
              choices: [
                {
                  index: 0,
                  delta: { content: `sub-result-${callIdx}` },
                  finish_reason: "stop",
                },
              ],
            })}\n\ndata: [DONE]\n\n`;
            // eslint-disable-next-line @typescript-eslint/no-require-imports
            const HttpResponse = require("msw").HttpResponse;
            return new HttpResponse(body, {
              status: 200,
              headers: { "Content-Type": "text/event-stream" },
            });
          }
          // Aggregator
          const body = `data: ${JSON.stringify({
            id: "chatcmpl-agg",
            object: "chat.completion.chunk",
            model: "kimi-k2.6",
            choices: [
              {
                index: 0,
                delta: { content: "Synthesized comparison." },
                finish_reason: "stop",
              },
            ],
          })}\n\ndata: [DONE]\n\n`;
          // eslint-disable-next-line @typescript-eslint/no-require-imports
          const HttpResponse = require("msw").HttpResponse;
          return new HttpResponse(body, {
            status: 200,
            headers: { "Content-Type": "text/event-stream" },
          });
        },
      ),
    );
    const { handleSwarm } = await import("@/lib/ai/kimi/swarm");
    const resp = await handleSwarm({
      messages: [
        { id: "u", role: "user", parts: [{ type: "text", text: "compare React, Vue, Svelte" }] },
      ],
    });
    expect(resp.status).toBe(200);
    expect(resp.headers.get("X-Kimi-Mode")).toBe("swarm");
    expect(resp.headers.get("X-Kimi-Swarm-Subagents")).toBe("3");
    const body = await readSse(resp);
    expect(body).toContain("Synthesized comparison");
  });
});

describe("E2E: error fallback", () => {
  it("returns 502 SSE when Moonshot is down (Instant)", async () => {
    server.use(moonshotDown());
    const { handleInstant } = await import("@/lib/ai/kimi/instant");
    const resp = await handleInstant({
      messages: [{ id: "u", role: "user", parts: [{ type: "text", text: "hi" }] }],
    });
    // Either 502 directly OR streamText surfaced it via onError + emitted
    // a kimi-error part inside the stream. Either is acceptable; the
    // user-visible UX is the same.
    expect([200, 502]).toContain(resp.status);
    const body = await readSse(resp);
    expect(body.toLowerCase()).toMatch(/error|fail|timeout/);
  });
});

describe("E2E: mode switch preserves history", () => {
  it("Instant call, then Thinking call with the same message history", async () => {
    server.use(instantSimpleAnswer());
    const { handleInstant } = await import("@/lib/ai/kimi/instant");
    const r1 = await handleInstant({
      messages: [{ id: "u1", role: "user", parts: [{ type: "text", text: "hi" }] }],
    });
    expect(r1.status).toBe(200);
    await r1.text();

    server.resetHandlers();
    server.use(thinkingWithReasoning());
    const { handleThinking } = await import("@/lib/ai/kimi/thinking");
    const r2 = await handleThinking({
      messages: [
        { id: "u1", role: "user", parts: [{ type: "text", text: "hi" }] },
        { id: "a1", role: "assistant", parts: [{ type: "text", text: "Hello!" }] },
        { id: "u2", role: "user", parts: [{ type: "text", text: "what's 17*23?" }] },
      ],
    });
    expect(r2.status).toBe(200);
    const body = await r2.text();
    expect(body).toContain("391");
  });
});
```

- [ ] **Step 4: Run E2E tests**

Run: `cd src/web && npm test -- tests/kimi/e2e.test.ts`
Expected: 6 scenarios pass.

If a test fails because the Moonshot SSE format the mock uses doesn't match what the openai-compatible provider expects, inspect the `@ai-sdk/openai-compatible` source under `node_modules/@ai-sdk/openai-compatible/dist/` to align the chunk shape. The provider expects standard OpenAI Chat Completions chunks; the mocks above match that shape.

The `eslint-disable-next-line` requires inside the swarm E2E test are ugly; refactor them to top-level imports (`import { http, HttpResponse } from "msw"`) — they were inlined to make the structure readable in one diff. Top-level is cleaner.

- [ ] **Step 5: Run the full kimi test suite**

Run: `cd src/web && npm test -- tests/kimi/`
Expected: shared (11) + dispatch (5) + instant (5) + thinking (7) + agent (8) + budget (7) + swarm (10) + e2e (6) = 59 tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/web/tests/_msw src/web/tests/kimi/e2e.test.ts
git commit -m "kimi-modes: E2E integration tests with MSW (Task 8)"
```

---

## Task 9: Update env.local.example + docs

**Files:**
- Modify: `src/web/.env.local.example` (or `src/web/.env.example` — whichever exists; create the file if neither exists)

- [ ] **Step 1: Check which env example file exists**

Run: `ls -la src/web/.env* 2>/dev/null`

If `.env.local.example` exists, modify it. If only `.env` or nothing exists, create `.env.local.example`.

- [ ] **Step 2: Append K2.6 configuration block**

Append to the env example file (create if missing):

```bash
# ── Kimi K2.6 modes (web chat) ──────────────────────────────────────────────
# Off by default. When set to "1", kimi-k2-{instant,thinking,agent,swarm}
# model selections route through src/lib/ai/kimi/ instead of the legacy
# single-call path. See docs/superpowers/specs/2026-05-05-kimi-k2-modes-web-design.md
KIMI_K2_MODES_ENABLED=0

# Per-day cost ceiling (USD) for the Swarm mode. Default $5.00.
# Counted by Redis key kimi:swarm:spend:YYYY-MM-DD with end-of-UTC-day expiry.
KIMI_SWARM_DAILY_BUDGET_USD=5.00

# Required for any kimi-k2-* model (set in Settings UI or here).
# KIMI_API_KEY=
```

- [ ] **Step 3: Commit**

```bash
git add src/web/.env.local.example
git commit -m "kimi-modes: document KIMI_K2_MODES_ENABLED + KIMI_SWARM_DAILY_BUDGET_USD env vars (Task 9)"
```

---

## Task 10: Soak — full kimi suite + lint + typecheck + manual smoke

**Files:** none (verification only)

- [ ] **Step 1: Run the full kimi test suite**

Run: `cd src/web && npm test -- tests/kimi/`
Expected: ~59 tests passing.

- [ ] **Step 2: Run typecheck**

Run: `cd src/web && npx tsc --noEmit`
Expected: zero errors.

- [ ] **Step 3: Run lint**

Run: `cd src/web && npm run lint`
Expected: zero new errors. Only acceptable failures are pre-existing ones unrelated to `src/lib/ai/kimi/` or `src/components/chat/kimi-*`.

- [ ] **Step 4: Manual soak — 4-mode walk**

Run: `cd src/web && KIMI_K2_MODES_ENABLED=1 npm run dev:next`

In the chat UI, run these 8 turns and observe:

1. `kimi-k2-instant` — "hi" → fast (<2s) one-line reply, no reasoning, no tool trace.
2. `kimi-k2-instant` — "list 3 prime numbers" → fast structured reply.
3. `kimi-k2-thinking` — "what's 23 × 47, show your work" → reasoning pill expands, final answer 1081 in body.
4. `kimi-k2-thinking` — "explain why the sky is blue" → reasoning pill, paragraph answer.
5. `kimi-k2-agent` — "what's the weather in Paris right now" → tool-trace card "🔍 webSearch", then weather answer.
6. `kimi-k2-agent` — "what's 2+2" → no tool call (model decides direct), short answer.
7. `kimi-k2-swarm` — "compare React, Vue, Svelte for an MVP" → progress card 0/3 → 3/3 → synthesis.
8. `kimi-k2-swarm` — "hi" → empty-plan fallback, fast Instant-style reply.

For each turn, also confirm:
- Server log shows `[kimi-{mode}]` lines
- No 5xx responses
- No regressions in non-K2.6 chat paths (try one Anthropic and one Groq turn afterwards)

- [ ] **Step 5: If any soak step fails, fix in place + recommit before promotion**

Don't proceed to Task 11 with broken modes.

- [ ] **Step 6: Commit any soak-found fixes (if any)**

```bash
git add -p   # selective stage
git commit -m "kimi-modes: soak fixes ($specific_issue)"
```

---

## Task 11: Promote — set KIMI_K2_MODES_ENABLED=1 in dev default

**Files:**
- Modify: `src/web/next.config.ts` (or `.env.local` if user prefers env-only) to default the flag on for dev

- [ ] **Step 1: Decision point**

The flag stays in env vars; we don't bake the default into next.config. Instead, document the flip in the project README (or main CLAUDE.md) so future devs know to add `KIMI_K2_MODES_ENABLED=1` to their `.env.local`.

- [ ] **Step 2: Update CLAUDE.md or AGENTS.md** (whichever exists at the root of `src/web/`)

Run: `ls src/web/CLAUDE.md src/web/AGENTS.md 2>/dev/null`

Append to the existing file:

```markdown

## K2.6 modes
The four `kimi-k2-{instant,thinking,agent,swarm}` model entries use the per-mode handler dispatcher in `src/lib/ai/kimi/`. To enable in dev, add to `.env.local`:

    KIMI_K2_MODES_ENABLED=1

Spec: `docs/superpowers/specs/2026-05-05-kimi-k2-modes-web-design.md`
Plan: `docs/superpowers/plans/2026-05-05-kimi-k2-modes-web.md`
```

- [ ] **Step 3: Commit**

```bash
git add src/web/AGENTS.md   # or CLAUDE.md, whichever was modified
git commit -m "kimi-modes: doc the KIMI_K2_MODES_ENABLED flag in web AGENTS.md (Task 11)"
```

- [ ] **Step 4: Final summary**

Verify the file layout matches the plan's promised structure:

Run: `find src/web/src/lib/ai/kimi src/web/src/components/chat/kimi-*.tsx src/web/tests/kimi -type f | sort`

Expected output (paths exactly):

```
src/web/src/components/chat/kimi-reasoning.tsx
src/web/src/components/chat/kimi-swarm-progress.tsx
src/web/src/components/chat/kimi-tool-trace.tsx
src/web/src/lib/ai/kimi/agent.ts
src/web/src/lib/ai/kimi/budget.ts
src/web/src/lib/ai/kimi/index.ts
src/web/src/lib/ai/kimi/instant.ts
src/web/src/lib/ai/kimi/shared.ts
src/web/src/lib/ai/kimi/swarm.ts
src/web/src/lib/ai/kimi/thinking.ts
src/web/tests/kimi/agent.test.ts
src/web/tests/kimi/budget.test.ts
src/web/tests/kimi/dispatch.test.ts
src/web/tests/kimi/e2e.test.ts
src/web/tests/kimi/instant.test.ts
src/web/tests/kimi/shared.test.ts
src/web/tests/kimi/swarm.test.ts
src/web/tests/kimi/thinking.test.ts
```

Done.

---

## Self-review (per writing-plans skill)

**1. Spec coverage:**
- ✅ G1 (Instant fast/minimal) → Task 4
- ✅ G2 (Thinking surfaces reasoning) → Task 5
- ✅ G3 (Agent uses native primitives) → Task 6
- ✅ G4 (Swarm actually swarms) → Task 7
- ✅ G5 (graceful degradation) → Task 4 (KimiKeyMissing 401), Task 5 (max-tokens fallback), Task 7 (empty-plan fallback, Promise.allSettled, budget guard)
- ✅ G6 (independently testable) → Tasks 4-7 each ship colocated unit tests; Task 8 ships E2E
- ✅ NG1-6 explicitly preserved (no CLI/voice/vision changes; route hook is the only chat-route edit)
- ✅ All §6 data flows mapped to handler implementations
- ✅ §7 error matrix covered: Tasks 4 (401), 5 (max-tokens retry), 6 (tool loop limit via stepCountIs), 7 (empty plan, partial failure, budget)
- ✅ §8 testing coverage: ~59 tests across unit + E2E
- ✅ §9 migration: KIMI_K2_MODES_ENABLED gates the entire path (Task 3 chat-route hook)
- ✅ §11 risks: discovery-and-fallback for `providerOptions.kimi.thinking` is encoded in Task 5 Step 5

**2. Placeholder scan:** searched for "TBD", "TODO", "implement later", "fill in details" — none in the plan. The Task 5 Step 5 transform code is conditional ("add only if live test shows the SDK doesn't split") but documented with the decision criterion and pseudocode is shown — engineer can implement based on observation, not guess.

**3. Type consistency:**
- `KimiUIPart` discriminator types declared once in shared.ts and re-exported (Task 2). Used consistently as `type: "kimi-reasoning"` etc. across handlers.
- `KimiModeRequest` defined in index.ts, consumed by all four handler files (Task 3 Step 4 swap).
- `KimiKeyMissingError` thrown by `buildKimiClient`, caught in all four handlers (Task 4-7).
- `reserveSwarmBudget` / `recordSwarmSpend` signatures match between budget.ts (Task 7 Step 3) and swarm.ts (Task 7 Step 7) — both take a number, return the documented shape.
- `ToolTraceEntry` type exported from kimi-tool-trace.tsx and consumed by message.tsx helper (Task 6).
- The aggregator's `messages` push uses `{ role: "user", content: string }` shape — this is the AI SDK's `ModelMessage` shape after `convertToModelMessages`. Verified consistent.

No drift detected.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-05-kimi-k2-modes-web.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
