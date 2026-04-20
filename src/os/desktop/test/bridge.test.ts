import { test, expect, beforeEach, afterEach } from "bun:test";
import type { LLMClient } from "../providers/types.ts";
import { startBridge } from "../bridge/server.ts";
import { defaultTools } from "../agent/tools/index.ts";

const PORT = 18766;
let server: ReturnType<typeof Bun.serve> | undefined;

function fakeClient(): LLMClient {
  return {
    name: "fake",
    async complete({ messages }) {
      const last = messages.at(-1);
      const txt = typeof last?.content === "string" ? last.content : "";
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
    apiKey: "test-key",
    ttsVoice: "daniel",
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

test("POST /api/speak rejects empty text", async () => {
  const r = await fetch(`http://127.0.0.1:${PORT}/api/speak`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ text: "" }),
  });
  expect(r.status).toBe(400);
});

test("POST /api/speak rejects missing text", async () => {
  const r = await fetch(`http://127.0.0.1:${PORT}/api/speak`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({}),
  });
  expect(r.status).toBe(400);
});

test("POST /api/transcribe rejects missing audio field", async () => {
  const form = new FormData();
  form.append("wrong", new Blob(["data"]), "x.wav");
  const r = await fetch(`http://127.0.0.1:${PORT}/api/transcribe`, {
    method: "POST",
    body: form,
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

test("GET /api/confirmation returns pending list", async () => {
  const r = await fetch(`http://127.0.0.1:${PORT}/api/confirmation`);
  expect(r.status).toBe(200);
  const body = (await r.json()) as { pending: unknown[] };
  expect(Array.isArray(body.pending)).toBe(true);
});

test("POST /api/panel opens a panel, GET lists it, DELETE /:id closes it", async () => {
  const open = await fetch(`http://127.0.0.1:${PORT}/api/panel`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ kind: "browser", src: "https://example.com" }),
  });
  expect(open.status).toBe(200);
  const spec = (await open.json()) as { id: string; kind: string };
  expect(spec.kind).toBe("browser");

  const list = await fetch(`http://127.0.0.1:${PORT}/api/panel`);
  const body = (await list.json()) as { panels: { id: string }[] };
  expect(body.panels.map((p) => p.id)).toContain(spec.id);

  const close = await fetch(`http://127.0.0.1:${PORT}/api/panel/${encodeURIComponent(spec.id)}`, { method: "DELETE" });
  expect(close.status).toBe(200);

  const list2 = await fetch(`http://127.0.0.1:${PORT}/api/panel`);
  const body2 = (await list2.json()) as { panels: unknown[] };
  expect(body2.panels).toHaveLength(0);
});

test("POST /api/panel with kind=text and no content returns 400", async () => {
  const r = await fetch(`http://127.0.0.1:${PORT}/api/panel`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ kind: "text" }),
  });
  expect(r.status).toBe(400);
});

test("POST /api/panel with invalid kind returns 400", async () => {
  const r = await fetch(`http://127.0.0.1:${PORT}/api/panel`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ kind: "nope", src: "x" }),
  });
  expect(r.status).toBe(400);
});

test("DELETE /api/panel/:id with unknown id returns 404", async () => {
  const r = await fetch(`http://127.0.0.1:${PORT}/api/panel/does-not-exist`, { method: "DELETE" });
  expect(r.status).toBe(404);
});
