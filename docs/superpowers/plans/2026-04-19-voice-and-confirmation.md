# Misty Scone — Plan 4: Voice I/O + Approval Queue

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add TTS and STT proxy endpoints to misty-core, plus a pending-approval queue that lets high-risk tool calls be confirmed asynchronously instead of auto-denied. Sets up the voice infrastructure so Plan 5's HUD/desktop client and Plan 6's wake-word daemon can drive real voice interaction.

**Scope vs "voice":** The daemon doesn't own the microphone or speakers — those are client concerns. Plan 4 ships three pieces: (a) `POST /api/speak` that accepts text and returns audio bytes via Groq Orpheus, (b) `POST /api/transcribe` that accepts an audio file and returns a transcript via Groq Whisper, (c) a confirmation queue where the agent loop pauses on high-risk tool calls until a client resolves them via `POST /api/confirmation/:id`. Actual voice interaction loops live in later plans.

**Tech Stack:** Bun + TypeScript (existing). Reuses the `@anthropic-ai/sdk` pattern for Groq (since Groq exposes both TTS and Whisper under its API). Minor extension to the agent loop (optional `confirm` callback). No new npm deps.

**Architecture:** Three new endpoints in `bridge/server.ts`, two new modules (`voice/tts.ts`, `voice/stt.ts`, `voice/confirmations.ts`). The agent loop gains an optional `confirm: (req) => Promise<boolean>` param; when set, high-risk tool calls call `confirm` instead of auto-denying. The bridge's `/api/think` handler creates a confirmation queue per request when the client opts into interactive mode via a query param.

**Spec reference:** `~/.claude/plans/i-want-to-build-misty-scone.md` — Plan 4 implements `voice/stt.ts`, `voice/tts.ts`, `permissions/voiceConfirm.ts` (here: `voice/confirmations.ts`), and the approval-queue part of `permissions/interactive.ts`. Out of scope: wake word (Plan 5), HUD widget (Plan 6), proactive controller (Plan 7).

**Depends on:** `plan-3-hyprland-screen` branch.

---

## File Structure

All new/modified files under `src/os/desktop/`.

```
src/os/desktop/
├── voice/
│   ├── tts.ts                NEW — Groq Orpheus TTS
│   ├── stt.ts                NEW — Groq Whisper STT
│   └── confirmations.ts      NEW — in-memory confirmation queue
├── bridge/
│   └── server.ts             MODIFIED — adds /api/speak, /api/transcribe, /api/confirmation/:id
├── agent/
│   └── loop.ts               MODIFIED — optional confirm callback threading through gate
├── risk/
│   └── gate.ts               MODIFIED — async gate with optional confirm; structured reasons
├── config/
│   ├── schema.ts             MODIFIED — add TTS voice default
│   └── load.ts               MODIFIED — load JARVIS_TTS_VOICE env
├── .env.example              MODIFIED — document TTS/STT-specific env
└── test/
    ├── confirmations.test.ts NEW
    ├── gate.test.ts          MODIFIED — tests for async gate with confirm
    ├── loop.test.ts          MODIFIED — test confirm path through loop
    └── bridge.test.ts        MODIFIED — TTS/STT/confirmation endpoint tests
```

**Boundary rules:**
- `voice/tts.ts` and `voice/stt.ts` are pure functions: input (text or audio bytes) → output (audio bytes or transcript). No HTTP, no queue. Testable in isolation.
- `voice/confirmations.ts` owns the queue. Pure data structure + mutex-like async resolution. No HTTP.
- `bridge/server.ts` is the only file that knows about HTTP transport.
- `risk/gate.ts` becomes async but remains deterministic when no `confirm` callback is passed (default = current behavior, deny).
- `agent/loop.ts` threads the `confirm` optional param; doesn't know about queues or HTTP.

---

## Behavior Contract

### `POST /api/speak`

Request:
```json
{ "text": "hello world", "voice": "daniel" }
```
Response: `200` with `Content-Type: audio/wav` and raw WAV bytes. On failure, `500` with `{"error": "..."}`.

Voices: `autumn | diana | hannah | austin | daniel | troy` (Orpheus defaults). Defaults to `$JARVIS_TTS_VOICE` or `daniel`.

### `POST /api/transcribe`

Request: multipart form-data with field `audio` (wav/webm/mp3/etc).
Response:
```json
{ "text": "the transcribed utterance" }
```

### `POST /api/think?interactive=1`

Same as `POST /api/think`, but when the agent encounters a high-risk tool call, instead of auto-denying it returns:

```json
{
  "status": "pending_confirmation",
  "confirmation_id": "c_abc123",
  "tool": "bash",
  "input": { "command": "sudo pacman -Syu" },
  "reason": "sudo privilege escalation",
  "prompt_text": "Run `sudo pacman -Syu`? This requires root."
}
```

The client then POSTs `POST /api/confirmation/c_abc123 { decision: "allow" | "deny" }`. Misty-core completes the agent loop and returns the final transcript on a follow-up GET `GET /api/think/:request_id` or by the initial POST's response stream.

**For Plan 4 simplicity:** The initial `POST /api/think?interactive=1` returns immediately with `pending_confirmation`. The client separately resolves, and the caller retrieves the final transcript by a second call `POST /api/think?continue=c_abc123`. This avoids long-lived HTTP connections in Plan 4; Plans 5+ can switch to SSE/WebSocket.

### `POST /api/confirmation/:id`

Request:
```json
{ "decision": "allow" | "deny" }
```
Response: `{ "ok": true }` (or `404` if the id is unknown or already resolved).

---

## Task 1: TTS module

**Files:**
- Create: `src/os/desktop/voice/tts.ts`
- Create: `src/os/desktop/test/tts.test.ts`

**Groq Orpheus endpoint:** `POST https://api.groq.com/openai/v1/audio/speech` with `Authorization: Bearer $GROQ_API_KEY` and JSON body `{ model, voice, input, response_format }`. Returns audio bytes.

- [ ] **Step 1: Write `voice/tts.ts`**

File: `src/os/desktop/voice/tts.ts`

```typescript
// Text-to-speech via Groq Orpheus (Groq's OpenAI-compatible audio endpoint).

export type TTSOpts = {
  apiKey: string;
  text: string;
  voice?: string;             // autumn | diana | hannah | austin | daniel | troy
  model?: string;             // default: canopylabs/orpheus-v1-english
  format?: "wav" | "mp3" | "flac" | "ogg"; // default: wav
  /** For tests: override fetch. */
  fetchFn?: typeof fetch;
};

const DEFAULT_MODEL = "canopylabs/orpheus-v1-english";
const DEFAULT_VOICE = "daniel";

export async function synthesize(opts: TTSOpts): Promise<Uint8Array> {
  const f = opts.fetchFn ?? fetch;
  const resp = await f("https://api.groq.com/openai/v1/audio/speech", {
    method: "POST",
    headers: {
      "content-type": "application/json",
      authorization: `Bearer ${opts.apiKey}`,
    },
    body: JSON.stringify({
      model: opts.model ?? DEFAULT_MODEL,
      voice: opts.voice ?? DEFAULT_VOICE,
      input: opts.text,
      response_format: opts.format ?? "wav",
    }),
  });
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`TTS failed (${resp.status}): ${text.slice(0, 500)}`);
  }
  const buf = await resp.arrayBuffer();
  return new Uint8Array(buf);
}
```

- [ ] **Step 2: Tests**

File: `src/os/desktop/test/tts.test.ts`

```typescript
import { test, expect } from "bun:test";
import { synthesize } from "../voice/tts.ts";

function stubFetch(response: Response): typeof fetch {
  return (async () => response) as unknown as typeof fetch;
}

test("synthesize posts JSON to Groq and returns audio bytes", async () => {
  let capturedUrl = "";
  let capturedBody = "";
  let capturedAuth = "";
  const bytes = new Uint8Array([0x52, 0x49, 0x46, 0x46]); // RIFF header
  const fetchFn = (async (url: string, init?: RequestInit) => {
    capturedUrl = String(url);
    capturedBody = String(init?.body);
    capturedAuth = String((init?.headers as Record<string, string>)?.authorization ?? "");
    return new Response(bytes, { status: 200 });
  }) as unknown as typeof fetch;

  const result = await synthesize({ apiKey: "k", text: "hello", fetchFn });
  expect(capturedUrl).toContain("groq.com");
  expect(capturedBody).toContain('"input":"hello"');
  expect(capturedAuth).toBe("Bearer k");
  expect(result[0]).toBe(0x52);
});

test("synthesize uses default voice when none provided", async () => {
  let capturedBody = "";
  const fetchFn = (async (_url: string, init?: RequestInit) => {
    capturedBody = String(init?.body);
    return new Response(new Uint8Array(), { status: 200 });
  }) as unknown as typeof fetch;
  await synthesize({ apiKey: "k", text: "t", fetchFn });
  expect(capturedBody).toContain('"voice":"daniel"');
});

test("synthesize throws on non-2xx", async () => {
  const fetchFn = stubFetch(new Response("bad request body", { status: 400 }));
  await expect(synthesize({ apiKey: "k", text: "t", fetchFn })).rejects.toThrow(/TTS failed \(400\)/);
});
```

- [ ] **Step 3: Verify + Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/os/desktop
bun run typecheck && bun test test/tts.test.ts
cd /home/ulrich/Documents/Projects/jarvis
git add src/os/desktop/voice/tts.ts src/os/desktop/test/tts.test.ts
git commit -m "feat(os/desktop): Groq Orpheus TTS module"
```

No `Co-Authored-By:`.

---

## Task 2: STT module

**Files:**
- Create: `src/os/desktop/voice/stt.ts`
- Create: `src/os/desktop/test/stt.test.ts`

**Groq Whisper endpoint:** `POST https://api.groq.com/openai/v1/audio/transcriptions` with multipart form: `file` (audio bytes), `model` (e.g. `whisper-large-v3`), optional `language`.

- [ ] **Step 1: Write `voice/stt.ts`**

File: `src/os/desktop/voice/stt.ts`

```typescript
// Speech-to-text via Groq Whisper.

export type STTOpts = {
  apiKey: string;
  audio: Uint8Array | ArrayBuffer;
  filename?: string;         // e.g. "input.wav" — affects server-side format detection
  model?: string;            // default: whisper-large-v3
  language?: string;         // ISO-639-1 code; defaults to server auto-detect
  /** For tests: override fetch. */
  fetchFn?: typeof fetch;
};

const DEFAULT_MODEL = "whisper-large-v3";

export async function transcribe(opts: STTOpts): Promise<string> {
  const f = opts.fetchFn ?? fetch;
  const form = new FormData();
  const blob = new Blob([opts.audio]);
  form.append("file", blob, opts.filename ?? "input.wav");
  form.append("model", opts.model ?? DEFAULT_MODEL);
  if (opts.language) form.append("language", opts.language);

  const resp = await f("https://api.groq.com/openai/v1/audio/transcriptions", {
    method: "POST",
    headers: {
      authorization: `Bearer ${opts.apiKey}`,
      // Content-Type is set automatically by the fetch impl to include the multipart boundary.
    },
    body: form,
  });
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`STT failed (${resp.status}): ${text.slice(0, 500)}`);
  }
  const body = (await resp.json()) as { text?: string };
  if (typeof body.text !== "string") {
    throw new Error(`STT response missing 'text' field: ${JSON.stringify(body).slice(0, 200)}`);
  }
  return body.text;
}
```

- [ ] **Step 2: Tests**

File: `src/os/desktop/test/stt.test.ts`

```typescript
import { test, expect } from "bun:test";
import { transcribe } from "../voice/stt.ts";

test("transcribe posts multipart form-data and returns text", async () => {
  let capturedUrl = "";
  let contentType = "";
  const fetchFn = (async (url: string, init?: RequestInit) => {
    capturedUrl = String(url);
    contentType = String((init?.headers as Record<string, string>)?.["content-type"] ?? "");
    // Under Bun, fetch auto-sets content-type for FormData bodies.
    return new Response(JSON.stringify({ text: "hello from audio" }), {
      status: 200, headers: { "content-type": "application/json" },
    });
  }) as unknown as typeof fetch;

  const audio = new Uint8Array([0x52, 0x49, 0x46, 0x46]);
  const result = await transcribe({ apiKey: "k", audio, fetchFn });
  expect(capturedUrl).toContain("audio/transcriptions");
  expect(result).toBe("hello from audio");
  void contentType; // we don't assert — fetch may set different MIME under Bun
});

test("transcribe throws on non-2xx", async () => {
  const fetchFn = (async () => new Response("bad audio", { status: 400 })) as unknown as typeof fetch;
  await expect(transcribe({
    apiKey: "k", audio: new Uint8Array(), fetchFn,
  })).rejects.toThrow(/STT failed \(400\)/);
});

test("transcribe throws if response body missing text", async () => {
  const fetchFn = (async () => new Response(JSON.stringify({}), {
    status: 200, headers: { "content-type": "application/json" },
  })) as unknown as typeof fetch;
  await expect(transcribe({
    apiKey: "k", audio: new Uint8Array(), fetchFn,
  })).rejects.toThrow(/missing 'text' field/);
});
```

- [ ] **Step 3: Verify + Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/os/desktop
bun run typecheck && bun test test/stt.test.ts
cd /home/ulrich/Documents/Projects/jarvis
git add src/os/desktop/voice/stt.ts src/os/desktop/test/stt.test.ts
git commit -m "feat(os/desktop): Groq Whisper STT module"
```

---

## Task 3: Confirmation queue

**Files:**
- Create: `src/os/desktop/voice/confirmations.ts`
- Create: `src/os/desktop/test/confirmations.test.ts`

The queue is in-memory (process-local). A confirmation request has an id, a tool/input description, and a promise that resolves when a client POSTs a decision. Requests time out after a configurable duration (default 5 min).

- [ ] **Step 1: Write `voice/confirmations.ts`**

File: `src/os/desktop/voice/confirmations.ts`

```typescript
// In-memory confirmation queue. Not durable across restarts — intentional for Plan 4.

export type ConfirmationRequest = {
  id: string;
  tool: string;
  input: unknown;
  reason: string;
  promptText: string;
  createdAt: number;
};

export type Decision = "allow" | "deny";

type PendingEntry = {
  request: ConfirmationRequest;
  resolve: (decision: Decision) => void;
  reject: (err: Error) => void;
  timer: ReturnType<typeof setTimeout>;
};

const DEFAULT_TIMEOUT_MS = 5 * 60 * 1000; // 5 minutes

export class ConfirmationQueue {
  private pending = new Map<string, PendingEntry>();
  private counter = 0;

  constructor(private readonly timeoutMs: number = DEFAULT_TIMEOUT_MS) {}

  /** Open a new confirmation request; returns its id and a promise that resolves when a client calls `resolve`. */
  open(opts: { tool: string; input: unknown; reason: string; promptText: string }): {
    id: string;
    wait: Promise<Decision>;
  } {
    const id = `c_${++this.counter}_${Date.now().toString(36)}`;
    const request: ConfirmationRequest = {
      id,
      tool: opts.tool,
      input: opts.input,
      reason: opts.reason,
      promptText: opts.promptText,
      createdAt: Date.now(),
    };
    let resolveOuter: (d: Decision) => void;
    let rejectOuter: (e: Error) => void;
    const wait = new Promise<Decision>((res, rej) => {
      resolveOuter = res;
      rejectOuter = rej;
    });
    const timer = setTimeout(() => {
      const entry = this.pending.get(id);
      if (!entry) return;
      this.pending.delete(id);
      entry.reject(new Error(`confirmation ${id} timed out after ${this.timeoutMs}ms`));
    }, this.timeoutMs);
    this.pending.set(id, { request, resolve: resolveOuter!, reject: rejectOuter!, timer });
    return { id, wait };
  }

  /** Resolve a pending confirmation. Returns true if resolved, false if unknown or already resolved. */
  resolve(id: string, decision: Decision): boolean {
    const entry = this.pending.get(id);
    if (!entry) return false;
    this.pending.delete(id);
    clearTimeout(entry.timer);
    entry.resolve(decision);
    return true;
  }

  /** Read a pending request's metadata without resolving it. */
  get(id: string): ConfirmationRequest | undefined {
    return this.pending.get(id)?.request;
  }

  /** List all pending requests. */
  list(): ConfirmationRequest[] {
    return Array.from(this.pending.values()).map((e) => e.request);
  }

  /** Shut down: reject all pending with an error. For tests and graceful shutdown. */
  shutdown(): void {
    for (const [id, entry] of this.pending) {
      clearTimeout(entry.timer);
      entry.reject(new Error(`confirmation queue shutting down; ${id} abandoned`));
    }
    this.pending.clear();
  }
}
```

- [ ] **Step 2: Tests**

File: `src/os/desktop/test/confirmations.test.ts`

```typescript
import { test, expect } from "bun:test";
import { ConfirmationQueue } from "../voice/confirmations.ts";

test("open returns a unique id and a pending promise", () => {
  const q = new ConfirmationQueue();
  const a = q.open({ tool: "bash", input: {}, reason: "r", promptText: "p" });
  const b = q.open({ tool: "bash", input: {}, reason: "r", promptText: "p" });
  expect(a.id).not.toBe(b.id);
  q.shutdown();
});

test("resolve with 'allow' settles the promise with 'allow'", async () => {
  const q = new ConfirmationQueue();
  const { id, wait } = q.open({ tool: "bash", input: {}, reason: "r", promptText: "p" });
  expect(q.resolve(id, "allow")).toBe(true);
  expect(await wait).toBe("allow");
});

test("resolve with 'deny' settles with 'deny'", async () => {
  const q = new ConfirmationQueue();
  const { id, wait } = q.open({ tool: "bash", input: {}, reason: "r", promptText: "p" });
  q.resolve(id, "deny");
  expect(await wait).toBe("deny");
});

test("resolve returns false for unknown id", () => {
  const q = new ConfirmationQueue();
  expect(q.resolve("bogus", "allow")).toBe(false);
});

test("resolve returns false for already-resolved id", () => {
  const q = new ConfirmationQueue();
  const { id } = q.open({ tool: "bash", input: {}, reason: "r", promptText: "p" });
  q.resolve(id, "allow");
  expect(q.resolve(id, "allow")).toBe(false);
});

test("list returns all pending requests", () => {
  const q = new ConfirmationQueue();
  const a = q.open({ tool: "bash", input: { cmd: "ls" }, reason: "r1", promptText: "p1" });
  const b = q.open({ tool: "bash", input: { cmd: "pwd" }, reason: "r2", promptText: "p2" });
  expect(q.list()).toHaveLength(2);
  q.resolve(a.id, "allow");
  expect(q.list()).toHaveLength(1);
  q.resolve(b.id, "deny");
  expect(q.list()).toHaveLength(0);
});

test("timeout rejects the pending promise", async () => {
  const q = new ConfirmationQueue(50); // 50ms timeout
  const { wait } = q.open({ tool: "bash", input: {}, reason: "r", promptText: "p" });
  await expect(wait).rejects.toThrow(/timed out after 50ms/);
});

test("shutdown rejects all pending", async () => {
  const q = new ConfirmationQueue();
  const { wait } = q.open({ tool: "bash", input: {}, reason: "r", promptText: "p" });
  q.shutdown();
  await expect(wait).rejects.toThrow(/shutting down/);
});
```

- [ ] **Step 3: Verify + Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/os/desktop
bun run typecheck && bun test test/confirmations.test.ts
cd /home/ulrich/Documents/Projects/jarvis
git add src/os/desktop/voice/confirmations.ts src/os/desktop/test/confirmations.test.ts
git commit -m "feat(os/desktop): in-memory confirmation queue with timeout + shutdown"
```

---

## Task 4: Make gate async + threadable confirm callback

**Files:**
- Modify: `src/os/desktop/risk/gate.ts`
- Modify: `src/os/desktop/agent/loop.ts`
- Modify: `src/os/desktop/test/gate.test.ts`
- Modify: `src/os/desktop/test/loop.test.ts`

This is the architectural change: gate becomes async and accepts an optional `confirm` callback. When present, high-risk calls ask the callback instead of auto-denying.

- [ ] **Step 1: Rewrite `risk/gate.ts`**

Replace `src/os/desktop/risk/gate.ts` with:

```typescript
import { classify } from "./tiers.ts";

export type GateDecision = { allow: true } | { allow: false; reason: string };

export type ConfirmCallback = (req: {
  tool: string;
  input: unknown;
  reason: string;
  promptText: string;
}) => Promise<"allow" | "deny">;

export type GateOpts = {
  /** If provided, high-risk tool calls ask this callback instead of auto-denying. */
  confirm?: ConfirmCallback;
};

export async function gate(
  toolName: string,
  input: unknown,
  opts: GateOpts = {},
): Promise<GateDecision> {
  const tier = classify(toolName, input);
  if (tier === "low") return { allow: true };

  // High-risk path.
  const summary = summarize(input);
  const reason = `high-risk ${toolName} call (${summary})`;
  const promptText = buildPrompt(toolName, input);

  if (opts.confirm) {
    const decision = await opts.confirm({ tool: toolName, input, reason, promptText });
    if (decision === "allow") return { allow: true };
    return { allow: false, reason: `user denied: ${reason}` };
  }

  return { allow: false, reason: `${reason}; no approval UI attached (pass confirm callback to allow)` };
}

function summarize(input: unknown): string {
  try {
    const s = JSON.stringify(input);
    return s.length > 200 ? s.slice(0, 200) + "…" : s;
  } catch {
    return "<unserializable input>";
  }
}

function buildPrompt(tool: string, input: unknown): string {
  if (tool === "bash") {
    const cmd = (input as { command?: string })?.command ?? "";
    return `Run \`${cmd.slice(0, 200)}\`? This was flagged as high-risk.`;
  }
  return `Proceed with ${tool} (${summarize(input)})? This was flagged as high-risk.`;
}
```

- [ ] **Step 2: Update `agent/loop.ts`**

Find the imports and RunOpts type near the top:

```typescript
import type { LLMClient, Message, ContentBlock } from "../providers/types.ts";
import type { ToolRegistry } from "./types.ts";
import { gate } from "../risk/gate.ts";
```

Update to:

```typescript
import type { LLMClient, Message, ContentBlock } from "../providers/types.ts";
import type { ToolRegistry } from "./types.ts";
import { gate, type ConfirmCallback } from "../risk/gate.ts";
```

Find the `RunOpts` type and extend it:

```typescript
export type RunOpts = {
  client: LLMClient;
  model: string;
  messages: Message[];
  tools: ToolRegistry;
  system?: string;
};
```

Replace with:

```typescript
export type RunOpts = {
  client: LLMClient;
  model: string;
  messages: Message[];
  tools: ToolRegistry;
  system?: string;
  confirm?: ConfirmCallback;
};
```

Find the gate call inside the loop:

```typescript
      const decision = gate(use.name, use.input);
```

Replace with:

```typescript
      const decision = await gate(use.name, use.input, { confirm: opts.confirm });
```

(The surrounding code already handles `decision.allow === false` correctly.)

- [ ] **Step 3: Update gate tests**

The existing tests call `gate(name, input)` synchronously. Update them to `await gate(name, input)` and add new tests for the confirm path.

Find:

```typescript
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

Replace with:

```typescript
test("gate allows low-risk", async () => {
  const r = await gate("bash", { command: "ls" });
  expect(r.allow).toBe(true);
});

test("gate denies high-risk without a confirm callback", async () => {
  const r = await gate("bash", { command: "sudo rm -rf /" });
  expect(r.allow).toBe(false);
  if (r.allow === false) {
    expect(r.reason).toContain("high-risk");
    expect(r.reason).toContain("bash");
    expect(r.reason).toContain("no approval UI");
  }
});

test("gate allows high-risk when confirm callback returns 'allow'", async () => {
  let seen = false;
  const r = await gate("bash", { command: "sudo ls" }, {
    confirm: async () => {
      seen = true;
      return "allow";
    },
  });
  expect(seen).toBe(true);
  expect(r.allow).toBe(true);
});

test("gate denies high-risk when confirm callback returns 'deny'", async () => {
  const r = await gate("bash", { command: "sudo ls" }, {
    confirm: async () => "deny",
  });
  expect(r.allow).toBe(false);
  if (r.allow === false) {
    expect(r.reason).toContain("user denied");
  }
});

test("gate passes promptText with the command to the confirm callback", async () => {
  let capturedPrompt = "";
  await gate("bash", { command: "sudo pacman -Syu" }, {
    confirm: async (req) => {
      capturedPrompt = req.promptText;
      return "deny";
    },
  });
  expect(capturedPrompt).toContain("pacman -Syu");
});
```

- [ ] **Step 4: Update loop tests**

The existing loop test for the "blocks high-risk bash" path needs the new phrasing. Find:

```typescript
test("runAgent blocks high-risk bash via gate", async () => {
  const { bashTool } = await import("../agent/tools/bash.ts");
  ...
});
```

Keep it mostly as-is; the test doesn't care about the exact reason text. But verify it still passes after the gate change. If it references "Plan 3" in an assertion, remove that line.

Add a new loop test:

```typescript
test("runAgent allows high-risk when confirm callback returns allow", async () => {
  const { bashTool } = await import("../agent/tools/bash.ts");
  const tools: ToolRegistry = { bash: bashTool };
  const client = stubClient([
    { content: [{ type: "tool_use", id: "t1", name: "bash", input: { command: "sudo ls /etc" } }], stop_reason: "tool_use" },
    { content: [{ type: "text", text: "Done." }], stop_reason: "end_turn" },
  ]);
  const result = await runAgent({
    client,
    model: "m",
    messages: [{ role: "user", content: "list /etc with sudo" }],
    tools,
    confirm: async () => "allow",
  });
  expect(result.blocked).toHaveLength(0);  // not blocked
  expect(result.stop_reason).toBe("end_turn");
});
```

- [ ] **Step 5: Verify**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/os/desktop
bun run typecheck && bun test
```

All existing tests should still pass; 5 new gate tests and 1 new loop test.

- [ ] **Step 6: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/os/desktop/risk/gate.ts \
        src/os/desktop/agent/loop.ts \
        src/os/desktop/test/gate.test.ts \
        src/os/desktop/test/loop.test.ts
git commit -m "feat(os/desktop): async gate with optional confirm callback + loop threading"
```

---

## Task 5: Bridge endpoints for /api/speak, /api/transcribe, /api/confirmation

**Files:**
- Modify: `src/os/desktop/bridge/server.ts`
- Modify: `src/os/desktop/daemon.ts`
- Modify: `src/os/desktop/config/schema.ts` (add `ttsVoice`)
- Modify: `src/os/desktop/config/load.ts` (read `JARVIS_TTS_VOICE`)
- Modify: `src/os/desktop/.env.example`
- Modify: `src/os/desktop/test/bridge.test.ts`

**Design decisions:**
- The bridge owns a shared `ConfirmationQueue` instance at daemon startup.
- `POST /api/think?interactive=1` creates a confirm callback that `queue.open()`s a confirmation per high-risk tool call and awaits the resolution.
- `POST /api/confirmation/:id { decision }` resolves a pending one.
- To keep Plan 4 simple, `?interactive=1` still holds the HTTP connection open until the agent loop completes — clients pay for long connections with short request timeouts on their side, or use the timeout. SSE / streaming is a Plan 5 topic.
- `POST /api/speak` and `POST /api/transcribe` are straightforward proxies to the TTS/STT modules using the primary provider's `apiKey` (which for the Groq default is the Groq API key, also valid for audio endpoints).

- [ ] **Step 1: Extend `config/schema.ts`**

Add `ttsVoice` field:

```typescript
export type Config = {
  // ... existing fields ...
  ttsVoice: string;
};
```

- [ ] **Step 2: Extend `config/load.ts`**

Within `loadConfig`, after the vision vars:

```typescript
  const ttsVoice = env.JARVIS_TTS_VOICE ?? "daniel";
```

And include it in the returned object.

- [ ] **Step 3: Append to `.env.example`**

```
# TTS voice (autumn | diana | hannah | austin | daniel | troy). Default: daniel.
JARVIS_TTS_VOICE=daniel
```

- [ ] **Step 4: Rewrite `bridge/server.ts`**

Replace contents with:

```typescript
import type { LLMClient, Message } from "../providers/types.ts";
import type { ToolRegistry } from "../agent/types.ts";
import { runAgent } from "../agent/loop.ts";
import { synthesize } from "../voice/tts.ts";
import { transcribe } from "../voice/stt.ts";
import { ConfirmationQueue } from "../voice/confirmations.ts";

export type BridgeOpts = {
  host: string;
  port: number;
  client: LLMClient;
  defaultModel: string;
  tools: ToolRegistry;
  apiKey: string;         // for TTS/STT proxy calls (same key as the text provider when Groq is primary)
  ttsVoice: string;
  queue?: ConfirmationQueue;  // for tests; daemon injects a shared instance
};

export function startBridge(opts: BridgeOpts) {
  const queue = opts.queue ?? new ConfirmationQueue();

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

      if (url.pathname === "/api/speak" && req.method === "POST") {
        try {
          const body = (await req.json()) as { text?: string; voice?: string; format?: "wav" | "mp3" | "flac" | "ogg" };
          if (typeof body.text !== "string" || body.text.length === 0) {
            return Response.json({ error: "text is required" }, { status: 400 });
          }
          const audio = await synthesize({
            apiKey: opts.apiKey,
            text: body.text,
            voice: body.voice ?? opts.ttsVoice,
            format: body.format ?? "wav",
          });
          return new Response(audio, {
            status: 200,
            headers: { "content-type": `audio/${body.format ?? "wav"}` },
          });
        } catch (err) {
          console.error("[misty-core] /api/speak error:", err);
          return Response.json({ error: err instanceof Error ? err.message : String(err) }, { status: 500 });
        }
      }

      if (url.pathname === "/api/transcribe" && req.method === "POST") {
        try {
          const form = await req.formData();
          const file = form.get("audio");
          if (!(file instanceof Blob)) {
            return Response.json({ error: "multipart field 'audio' (Blob) required" }, { status: 400 });
          }
          const buf = new Uint8Array(await file.arrayBuffer());
          const text = await transcribe({ apiKey: opts.apiKey, audio: buf });
          return Response.json({ text });
        } catch (err) {
          console.error("[misty-core] /api/transcribe error:", err);
          return Response.json({ error: err instanceof Error ? err.message : String(err) }, { status: 500 });
        }
      }

      const confirmMatch = url.pathname.match(/^\/api\/confirmation\/([^/]+)$/);
      if (confirmMatch && req.method === "POST") {
        const id = confirmMatch[1]!;
        let body: { decision?: "allow" | "deny" };
        try {
          body = await req.json();
        } catch {
          return Response.json({ error: "invalid JSON body" }, { status: 400 });
        }
        if (body.decision !== "allow" && body.decision !== "deny") {
          return Response.json({ error: "decision must be 'allow' or 'deny'" }, { status: 400 });
        }
        const ok = queue.resolve(id, body.decision);
        if (!ok) return Response.json({ error: "unknown or already-resolved confirmation id" }, { status: 404 });
        return Response.json({ ok: true });
      }

      if (url.pathname === "/api/confirmation" && req.method === "GET") {
        return Response.json({ pending: queue.list() });
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
        const interactive = url.searchParams.get("interactive") === "1";
        try {
          const result = await runAgent({
            client: opts.client,
            model: body.model ?? opts.defaultModel,
            messages: body.messages,
            tools: opts.tools,
            system: body.system,
            confirm: interactive
              ? async (req) => {
                  const { id, wait } = queue.open(req);
                  // The client will discover this id by polling GET /api/confirmation or via the returned transcript.
                  console.log(`[misty-core] awaiting confirmation ${id} for ${req.tool}`);
                  return wait;
                }
              : undefined,
          });
          return Response.json(result);
        } catch (err) {
          const message = err instanceof Error ? err.message : String(err);
          console.error("[misty-core] /api/think error:", err);
          return Response.json({ error: message }, { status: 500 });
        }
      }

      return new Response("not found", { status: 404 });
    },
  });
}
```

- [ ] **Step 5: Update `daemon.ts` to pass apiKey + ttsVoice + queue**

Replace `src/os/desktop/daemon.ts` with:

```typescript
import { loadConfig } from "./config/load.ts";
import { startBridge } from "./bridge/server.ts";
import { createClient } from "./providers/registry.ts";
import { createVisionClient } from "./providers/vision.ts";
import { defaultTools } from "./agent/tools/index.ts";
import { ConfirmationQueue } from "./voice/confirmations.ts";

const cfg = loadConfig();
const client = createClient(cfg);

let visionClient;
try {
  visionClient = createVisionClient(cfg);
} catch {
  visionClient = undefined;
}

const tools = defaultTools({ visionClient });
const queue = new ConfirmationQueue();

startBridge({
  host: cfg.host,
  port: cfg.port,
  client,
  defaultModel: cfg.model,
  tools,
  apiKey: cfg.apiKey,
  ttsVoice: cfg.ttsVoice,
  queue,
});
console.log(`[misty-core] listening on http://${cfg.host}:${cfg.port} (provider=${cfg.provider} model=${cfg.model}, vision=${visionClient?.name ?? "disabled"}, tts_voice=${cfg.ttsVoice})`);
```

- [ ] **Step 6: Update `bridge.test.ts`**

Append new tests:

```typescript
test("POST /api/speak returns audio bytes", async () => {
  // This test needs a live Groq key, so it's skipped unless GROQ_API_KEY is set in the env.
  if (!process.env.GROQ_API_KEY) {
    console.log("skipping live /api/speak test — set GROQ_API_KEY to run");
    return;
  }
  const r = await fetch(`http://127.0.0.1:${PORT}/api/speak`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ text: "test" }),
  });
  expect(r.status).toBe(200);
  expect(r.headers.get("content-type")).toContain("audio");
});

test("POST /api/speak rejects empty text", async () => {
  const r = await fetch(`http://127.0.0.1:${PORT}/api/speak`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ text: "" }),
  });
  expect(r.status).toBe(400);
});

test("POST /api/confirmation/:id with unknown id returns 404", async () => {
  const r = await fetch(`http://127.0.0.1:${PORT}/api/confirmation/nonexistent`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ decision: "allow" }),
  });
  expect(r.status).toBe(404);
});

test("POST /api/confirmation/:id with invalid decision returns 400", async () => {
  const r = await fetch(`http://127.0.0.1:${PORT}/api/confirmation/foo`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ decision: "maybe" }),
  });
  expect(r.status).toBe(400);
});

test("GET /api/confirmation returns the pending list", async () => {
  const r = await fetch(`http://127.0.0.1:${PORT}/api/confirmation`);
  expect(r.status).toBe(200);
  const body = (await r.json()) as { pending: unknown[] };
  expect(Array.isArray(body.pending)).toBe(true);
});
```

Also update the existing `beforeEach` / bridge init to pass the new required BridgeOpts fields (apiKey, ttsVoice). For test purposes, apiKey can be `"test-key"` and ttsVoice `"daniel"`. The live /api/speak test will skip if the real key isn't set.

- [ ] **Step 7: Verify**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/os/desktop
bun run typecheck && bun test
```

- [ ] **Step 8: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/os/desktop/bridge/server.ts \
        src/os/desktop/daemon.ts \
        src/os/desktop/config/ \
        src/os/desktop/.env.example \
        src/os/desktop/test/bridge.test.ts
git commit -m "feat(os/desktop): /api/speak + /api/transcribe + /api/confirmation endpoints"
```

---

## Task 6: README + architecture doc

**Files:**
- Modify: `src/os/desktop/README.md`

- [ ] **Step 1: Update the README**

In the `## What it does` section, add the new endpoints:

- `POST /api/speak { text, voice? }` → audio bytes (Groq Orpheus TTS)
- `POST /api/transcribe` (multipart `audio`) → `{ text }` (Groq Whisper STT)
- `POST /api/think?interactive=1` — high-risk tool calls pause and open a confirmation request
- `POST /api/confirmation/:id { decision: "allow" | "deny" }` — resolve a pending confirmation
- `GET /api/confirmation` — list pending confirmations

In the `Code layout` section, add:

```
voice/       TTS (Groq Orpheus), STT (Groq Whisper), confirmation queue
```

In the `What's next` section, remove "voice" from the deferred list and add:

> Plan 4 lands server-side voice infrastructure and the confirmation queue. Plans 5+ add a client (HUD or desktop app) that uses these endpoints to implement real voice-driven approval flows and the wake-word daemon.

- [ ] **Step 2: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/os/desktop/README.md
git commit -m "docs(os/desktop): README covers voice endpoints + confirmation queue"
```

---

## Task 7: Manual dry-run (no commit)

- [ ] `bun run typecheck` → clean.
- [ ] `bun test` → all pass.
- [ ] With `GROQ_API_KEY` set: `curl -X POST http://127.0.0.1:8765/api/speak -H 'content-type: application/json' -d '{"text":"hello world"}' --output speech.wav` → file plays audio when opened.
- [ ] With `GROQ_API_KEY` set: `curl -X POST http://127.0.0.1:8765/api/transcribe -F 'audio=@speech.wav'` → returns transcription.
- [ ] Send a `POST /api/think?interactive=1` asking misty to run `sudo ls`:
  - Response: `{ status: "pending_confirmation", confirmation_id: "c_..." }` OR the final transcript containing a tool_result that used the confirmation (depending on how the loop threads it).
  - In another shell: `curl -X POST http://127.0.0.1:8765/api/confirmation/c_... -H 'content-type: application/json' -d '{"decision":"allow"}'` resolves it.

**Note:** The current design has `/api/think?interactive=1` block until the confirmation resolves (within the queue timeout). If that blocks a curl too long, the design needs SSE/WebSocket (Plan 5+).

---

## Self-Review

**Spec coverage:**
- `voice/tts.ts` + `voice/stt.ts` (Task 1-2) ✓
- `permissions/voiceConfirm.ts` (here: `voice/confirmations.ts` — same idea, named by queue responsibility) ✓
- Async gate with confirm callback (Task 4) ✓
- `/api/speak`, `/api/transcribe`, `/api/confirmation` (Task 5) ✓
- Autonomy profile B (high-risk confirms instead of denies, given a callback) ✓

Out-of-scope for Plan 4 (correctly deferred): wake word (Plan 5), HUD (Plan 6), client-side audio loop, SSE/WebSocket streaming for long-running interactive requests, the Tauri desktop integration.

**Placeholder scan:** No TBD/TODO in code. Manual-dry-run Task 7 has a note about blocking behavior; addressed in Plan 5+ rather than a placeholder fix.

**Type consistency:** `ConfirmCallback`, `GateOpts`, `Decision`, `ConfirmationRequest`, `ConfirmationQueue`, `RunOpts.confirm`, `BridgeOpts.queue` — all defined once and flow through cleanly. Existing test updates follow the new async-gate signature consistently.

---

## Execution Handoff

**Same options as Plans 1-3.**

**1. Subagent-Driven (recommended)**

**2. Inline Execution**

**Which approach?**
