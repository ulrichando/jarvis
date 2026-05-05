import { http, HttpResponse } from "msw";

// SSE chunk helpers shaped like Moonshot's OpenAI-compatible
// /v1/chat/completions streaming response.
function chatChunk(delta: {
  content?: string;
  reasoning_content?: string;
  tool_calls?: unknown[];
}) {
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

function sseResponse(body: string) {
  return new HttpResponse(body, {
    status: 200,
    headers: { "Content-Type": "text/event-stream" },
  });
}

// Each export is a Handler factory that returns an `http.post(...)` handler.
// Tests register them with `server.use(handlerFactory())`.

export function instantSimpleAnswer() {
  return http.post("https://api.moonshot.ai/v1/chat/completions", () => {
    const body =
      chatChunk({ content: "4" }) + chatChunk({ content: "" }) + chatDone();
    return sseResponse(body);
  });
}

export function thinkingWithReasoning() {
  return http.post("https://api.moonshot.ai/v1/chat/completions", () => {
    const body =
      chatChunk({ reasoning_content: "Let me compute 17*23..." }) +
      chatChunk({ reasoning_content: "20*23 = 460, minus 3*23 = 69, so 391." }) +
      chatChunk({ content: "391" }) +
      chatDone();
    return sseResponse(body);
  });
}

export function agentWithToolCall() {
  let callCount = 0;
  return http.post("https://api.moonshot.ai/v1/chat/completions", () => {
    callCount++;
    if (callCount === 1) {
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
      return sseResponse(body);
    }
    const body =
      chatChunk({ content: "It's 18°C and partly cloudy in Paris." }) +
      chatDone();
    return sseResponse(body);
  });
}

export function moonshotDown() {
  return http.post("https://api.moonshot.ai/v1/chat/completions", () => {
    return new HttpResponse(
      JSON.stringify({ error: { message: "upstream timeout" } }),
      {
        status: 502,
        headers: { "Content-Type": "application/json" },
      },
    );
  });
}
