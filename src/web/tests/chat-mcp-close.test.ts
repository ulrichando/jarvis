import { describe, expect, test, vi, beforeEach } from "vitest";

// Finding #8: the chat route connects MCP servers per plain-chat request but
// only awaited `mcpClose` inside streamText's onFinish. The forced-image early
// return (Image toggle / hasImageIntent) bypasses streamText entirely, and a
// provider error surfaced via onError skips onFinish — both leaked the open
// MCP client + its transport socket. The fix routes every post-connect exit
// through a single `closeMcp` helper. This test drives the forced-image path
// (the one a unit test can reach without a live model stream) and asserts the
// MCP client's `close` fires before the route returns.

// The MCP client `close` spy — shared so both the mock and the assertions see
// the same reference across the dynamic route import.
const mcpCloseSpy = vi.fn(async () => {});

// --- Mock the heavy route dependencies so importing POST executes only the
//     controlled forced-image path (which returns before streamText). ---

vi.mock("@/lib/auth-helpers", async (orig) => {
  const actual = await orig<typeof import("@/lib/auth-helpers")>();
  return { ...actual, requireUserId: vi.fn(async () => "user-test") };
});

vi.mock("@/lib/settings/store", () => ({
  loadSettings: vi.fn(async () => ({
    defaults: {
      model: "deepseek-v4-flash",
      imageModel: "gemini-3.1-flash-image-preview",
      temperature: 0.7,
      systemPrompt: undefined,
    },
    user: {},
  })),
}));

vi.mock("@/lib/ai/models", () => ({
  getModel: vi.fn(async () => ({ meta: { provider: "deepseek" }, model: {} })),
  MissingApiKeyError: class MissingApiKeyError extends Error {},
}));

// Return an enabled MCP server so the connect branch fires, and a loader whose
// `close` is our spy — that's the connection the route must release.
vi.mock("@/lib/mcp/store", () => ({
  listMcpServers: vi.fn(async () => [
    { name: "test-server", url: "http://127.0.0.1:9/mcp", enabled: true },
  ]),
}));
vi.mock("@/lib/mcp/client", () => ({
  loadMcpTools: vi.fn(async () => ({ tools: {}, close: mcpCloseSpy })),
}));

// Image generation is available, and the "generate" call succeeds → the route
// takes the forced-image early return (its whole reason to bypass the model).
vi.mock("@/lib/ai/image", () => ({
  imageGenAvailable: vi.fn(async () => true),
  generateChatImage: vi.fn(async () => ({
    url: "/api/media/deadbeef.png",
    mediaType: "image/png",
    prompt: "a boat",
    modelLabel: "Test Image Model",
  })),
}));
vi.mock("@/lib/tools/generate-image", () => ({
  createGenerateImageTool: vi.fn(() => ({})),
}));

// Persistence + knowledge blocks: no-op stubs so the path doesn't touch a DB.
vi.mock("@/lib/chat/persist", async (orig) => {
  const actual = await orig<typeof import("@/lib/chat/persist")>();
  return {
    ...actual,
    ensureConversation: vi.fn(async () => ({ id: "conv-1", projectId: null })),
    saveUserMessage: vi.fn(async () => {}),
    saveAssistantMessage: vi.fn(async () => "msg-1"),
    maybeUpdateLastAssistantMessage: vi.fn(async () => {}),
    recordUsageEvent: vi.fn(async () => {}),
  };
});
vi.mock("@/lib/workspace/knowledge", () => ({ readKnowledgeBlock: vi.fn(async () => "") }));
vi.mock("@/lib/knowledge/store", () => ({ readGlobalKnowledgeBlock: vi.fn(async () => "") }));

function imageRequest() {
  return new Request("http://127.0.0.1:3000/api/chat", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      id: "conv-1",
      // Explicit Image toggle → deterministic forced-image path.
      image: true,
      messages: [
        {
          id: "m1",
          role: "user",
          parts: [{ type: "text", text: "generate a boat" }],
        },
      ],
    }),
  });
}

beforeEach(() => {
  mcpCloseSpy.mockClear();
  process.env.JARVIS_WEB_ARTIFACTS_ENABLED = "0";
});

describe("chat route — MCP client cleanup on forced-image path (finding #8)", () => {
  test("closes the MCP client before returning the forced-image response", async () => {
    const { POST } = await import("@/app/api/chat/route");
    const res = await POST(imageRequest());

    // Forced-image path returns an SSE stream, not a JSON error.
    expect(res.status).toBe(200);
    expect(res.headers.get("Content-Type")).toBe("text/event-stream");

    // The leak this test guards: the connected MCP client MUST be closed
    // even though streamText (and thus onFinish) never ran.
    expect(mcpCloseSpy).toHaveBeenCalledTimes(1);
  });
});
