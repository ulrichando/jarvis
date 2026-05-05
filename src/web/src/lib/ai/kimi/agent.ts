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
    if (
      err instanceof KimiKeyMissingError ||
      (err instanceof Error && err.name === "KimiKeyMissingError")
    ) {
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
