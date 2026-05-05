import {
  describe,
  it,
  expect,
  beforeAll,
  afterAll,
  afterEach,
  beforeEach,
} from "vitest";
import { http, HttpResponse } from "msw";
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
  return await resp.text();
}

describe("E2E: Instant", () => {
  it("returns the answer in SSE format", async () => {
    server.use(instantSimpleAnswer());
    const { handleInstant } = await import("@/lib/ai/kimi/instant");
    const resp = await handleInstant({
      messages: [
        { id: "u", role: "user", parts: [{ type: "text", text: "what's 2+2?" }] },
      ],
    });
    expect(resp.status).toBe(200);
    const body = await readSse(resp);
    expect(body).toContain("4");
  });
});

describe("E2E: Thinking", () => {
  it("surfaces reasoning_content alongside content in the stream body", async () => {
    server.use(thinkingWithReasoning());
    const { handleThinking } = await import("@/lib/ai/kimi/thinking");
    const resp = await handleThinking({
      messages: [
        {
          id: "u",
          role: "user",
          parts: [{ type: "text", text: "what's 17*23?" }],
        },
      ],
    });
    expect(resp.status).toBe(200);
    const body = await readSse(resp);
    expect(body).toContain("391");
    expect(body).toMatch(/reasoning|Let me compute/i);
  });
});

describe("E2E: Agent", () => {
  it("performs a tool call and streams the final answer", async () => {
    server.use(agentWithToolCall());
    const { handleAgent } = await import("@/lib/ai/kimi/agent");
    const resp = await handleAgent({
      messages: [
        {
          id: "u",
          role: "user",
          parts: [{ type: "text", text: "weather in Paris" }],
        },
      ],
    });
    expect(resp.status).toBe(200);
    const body = await readSse(resp);
    expect(body).toContain("Paris");
    expect(body).toMatch(/webSearch|tool/i);
  });
});

describe("E2E: Swarm", () => {
  it("decomposes, fans out, and aggregates", async () => {
    let callIdx = 0;
    server.use(
      http.post("https://api.moonshot.ai/v1/chat/completions", async ({ request }) => {
        callIdx++;
        // generateObject + generateText fire NON-streaming completions
        // (single JSON body), while streamText sends `stream: true`. The
        // body shape is different in each case, so branch off the request.
        const reqBody = (await request.clone().json().catch(() => ({}))) as {
          stream?: boolean;
        };
        const isStream = reqBody.stream === true;

        if (callIdx === 1) {
          // Decompose (generateObject) → non-streaming JSON completion.
          // Returns the schema-conformant object as the message content.
          const json = {
            id: "chatcmpl-decompose",
            object: "chat.completion",
            model: "kimi-k2.6",
            choices: [
              {
                index: 0,
                message: {
                  role: "assistant",
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
            usage: { prompt_tokens: 50, completion_tokens: 80, total_tokens: 130 },
          };
          return HttpResponse.json(json);
        }
        if (callIdx <= 4) {
          // 3 sub-agents (calls 2, 3, 4) → generateText, non-streaming.
          const json = {
            id: `chatcmpl-sub-${callIdx}`,
            object: "chat.completion",
            model: "kimi-k2.6",
            choices: [
              {
                index: 0,
                message: {
                  role: "assistant",
                  content: `sub-result-${callIdx}`,
                },
                finish_reason: "stop",
              },
            ],
            usage: { prompt_tokens: 30, completion_tokens: 10, total_tokens: 40 },
          };
          return HttpResponse.json(json);
        }
        // Aggregator (call 5) → streamText, SSE.
        if (isStream) {
          const body =
            `data: ${JSON.stringify({
              id: "chatcmpl-agg",
              object: "chat.completion.chunk",
              model: "kimi-k2.6",
              choices: [
                {
                  index: 0,
                  delta: { content: "Synthesized comparison." },
                  finish_reason: null,
                },
              ],
            })}\n\n` +
            `data: ${JSON.stringify({
              id: "chatcmpl-agg",
              object: "chat.completion.chunk",
              model: "kimi-k2.6",
              choices: [{ index: 0, delta: {}, finish_reason: "stop" }],
              usage: { prompt_tokens: 200, completion_tokens: 20, total_tokens: 220 },
            })}\n\n` +
            `data: [DONE]\n\n`;
          return new HttpResponse(body, {
            status: 200,
            headers: { "Content-Type": "text/event-stream" },
          });
        }
        // Fallback: aggregator-shape JSON if it ever calls non-stream.
        return HttpResponse.json({
          id: "chatcmpl-agg",
          object: "chat.completion",
          model: "kimi-k2.6",
          choices: [
            {
              index: 0,
              message: { role: "assistant", content: "Synthesized comparison." },
              finish_reason: "stop",
            },
          ],
          usage: { prompt_tokens: 200, completion_tokens: 20, total_tokens: 220 },
        });
      }),
    );
    const { handleSwarm } = await import("@/lib/ai/kimi/swarm");
    const resp = await handleSwarm({
      messages: [
        {
          id: "u",
          role: "user",
          parts: [
            { type: "text", text: "compare React, Vue, Svelte" },
          ],
        },
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
  it(
    "returns SSE with error indication when Moonshot is down (Instant)",
    async () => {
      server.use(moonshotDown());
      const { handleInstant } = await import("@/lib/ai/kimi/instant");
      const resp = await handleInstant({
        messages: [
          { id: "u", role: "user", parts: [{ type: "text", text: "hi" }] },
        ],
      });
      // Either 502 directly OR 200 with the SDK surfacing the error
      // through onError → kimi-error part. Both are acceptable; user
      // experience is the same (toast + retry).
      expect([200, 502]).toContain(resp.status);
      const body = await resp.text();
      expect(body.toLowerCase()).toMatch(
        /error|fail|timeout|upstream|api.?call/,
      );
    },
    15000,
  );
});

describe("E2E: mode switch preserves history", () => {
  it("Instant call, then Thinking call with the same message history", async () => {
    server.use(instantSimpleAnswer());
    const { handleInstant } = await import("@/lib/ai/kimi/instant");
    const r1 = await handleInstant({
      messages: [
        { id: "u1", role: "user", parts: [{ type: "text", text: "hi" }] },
      ],
    });
    expect(r1.status).toBe(200);
    await r1.text();

    server.resetHandlers();
    server.use(thinkingWithReasoning());
    const { handleThinking } = await import("@/lib/ai/kimi/thinking");
    const r2 = await handleThinking({
      messages: [
        { id: "u1", role: "user", parts: [{ type: "text", text: "hi" }] },
        {
          id: "a1",
          role: "assistant",
          parts: [{ type: "text", text: "Hello!" }],
        },
        {
          id: "u2",
          role: "user",
          parts: [{ type: "text", text: "what's 17*23?" }],
        },
      ],
    });
    expect(r2.status).toBe(200);
    const body = await r2.text();
    expect(body).toContain("391");
  });
});
