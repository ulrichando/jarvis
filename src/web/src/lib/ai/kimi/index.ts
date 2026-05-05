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
