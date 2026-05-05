import "server-only";
import type { KimiModeRequest } from "./index";

export async function handleSwarm(_body: KimiModeRequest): Promise<Response> {
  return new Response(
    `data: ${JSON.stringify({
      type: "kimi-error",
      status: 501,
      message: "Swarm mode not yet implemented (Task 7)",
    })}\n\ndata: [DONE]\n\n`,
    { status: 501, headers: { "Content-Type": "text/event-stream" } },
  );
}
