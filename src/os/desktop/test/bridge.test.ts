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
