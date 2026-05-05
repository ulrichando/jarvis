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
