import "server-only";
import { convertToModelMessages, streamText } from "ai";
import {
  buildKimiClient,
  extractMessagesForKimi,
  formatKimiError,
  KIMI_TEMPERATURE_THINKING,
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
    temperature: KIMI_TEMPERATURE_THINKING,
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
    // Match the Instant handler's KimiKeyMissingError handling — both
    // instanceof and duck-typed name check, since vi.mock breaks the
    // module boundary for instanceof.
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
