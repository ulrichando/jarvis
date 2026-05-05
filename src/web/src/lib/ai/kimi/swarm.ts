import "server-only";
import {
  convertToModelMessages,
  generateObject,
  generateText,
  streamText,
} from "ai";
import { z } from "zod";
import {
  buildKimiClient,
  extractMessagesForKimi,
  formatKimiError,
  KimiKeyMissingError,
  loadKimiPersona,
} from "./shared";
import { reserveSwarmBudget, recordSwarmSpend } from "./budget";
import type { KimiModeRequest } from "./index";

const SwarmPlanSchema = z.object({
  subtasks: z
    .array(
      z.object({
        role: z.string().describe("Short role name, e.g. 'researcher-pricing'"),
        prompt: z
          .string()
          .describe("Self-contained instruction for this sub-agent"),
      }),
    )
    .max(5),
});

// Rough K2.6 pricing (per the public Moonshot price page; verify before launch).
// Conservative estimate: $0.0005/1K input, $0.005/1K output.
const PRICE_INPUT_PER_1K = 0.0005;
const PRICE_OUTPUT_PER_1K = 0.005;

// Pre-flight estimate: 5 subtasks × (~500 input tokens + ~500 output tokens)
// = 5 × ($0.00025 + $0.0025) = ~$0.014. Aggregator: ~2K input + ~500 output
// = ~$0.0035. Total estimated: ~$0.018. Round up for safety.
const SWARM_ESTIMATED_COST_USD = 0.06;

type SubResult = {
  role: string;
  text: string;
  failed?: boolean;
  error?: string;
};

export async function handleSwarm(body: KimiModeRequest): Promise<Response> {
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

  // Budget gate: refuse early if today's spend would push over the limit.
  const reservation = await reserveSwarmBudget(SWARM_ESTIMATED_COST_USD);
  if (!reservation.ok) {
    const errBody = `data: ${JSON.stringify({
      type: "kimi-error",
      status: 429,
      message: reservation.reason,
      mode: "swarm",
    })}\n\ndata: [DONE]\n\n`;
    return new Response(errBody, {
      status: 429,
      headers: {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-store",
      },
    });
  }

  try {
    const messages = await convertToModelMessages(
      extractMessagesForKimi(body.messages),
    );
    const userPrompt = messages
      .filter((m) => m.role === "user")
      .map((m) => {
        if (typeof m.content === "string") return m.content;
        return (m.content as Array<{ type: string; text?: string }>)
          .filter((p) => p.type === "text")
          .map((p) => p.text ?? "")
          .join("");
      })
      .join("\n\n");

    // Step 1 — Decompose.
    const decompose = await generateObject({
      model: client.model,
      schema: SwarmPlanSchema,
      system: `You are a planner. Break the user's request into 3-5 parallel \
sub-agent tasks, each with a focused role and a complete, self-contained prompt. \
If the request is too simple to benefit from parallelism, return an empty subtasks array.`,
      prompt: userPrompt,
      providerOptions: {
        kimi: { thinking: { type: "disabled" } },
      },
    });

    const plan = decompose.object;
    const sessionId = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    const cacheKey = `swarm-${sessionId}`;

    // Step 2a — Empty plan: fall back to Instant single call.
    if (!plan.subtasks || plan.subtasks.length === 0) {
      const fallback = streamText({
        model: client.model,
        system: loadKimiPersona(),
        messages,
        temperature: 0.6,
        maxOutputTokens: 1024,
        providerOptions: {
          kimi: { thinking: { type: "disabled" } },
        },
      });
      fallback.consumeStream();
      return fallback.toUIMessageStreamResponse({
        headers: {
          "X-Kimi-Mode": "swarm",
          "X-Kimi-Swarm-Fallback": "instant-empty-plan",
        },
      });
    }

    // Step 2b — Fan out.
    const subPromises = plan.subtasks.map((t) =>
      generateText({
        model: client.model,
        system: `You are a sub-agent with role "${t.role}". Answer only your task.`,
        prompt: t.prompt,
        temperature: 0.7,
        maxOutputTokens: 800,
        providerOptions: {
          kimi: {
            thinking: { type: "disabled" },
            prompt_cache_key: cacheKey,
          },
        },
      }),
    );

    const settled = await Promise.allSettled(subPromises);
    let totalInput = 0;
    let totalOutput = 0;
    const subResults: SubResult[] = settled.map((r, i) => {
      const role = plan.subtasks[i].role;
      if (r.status === "fulfilled") {
        totalInput += r.value.usage?.inputTokens ?? 0;
        totalOutput += r.value.usage?.outputTokens ?? 0;
        return { role, text: r.value.text };
      }
      return {
        role,
        text: `(this sub-agent failed: ${(r.reason as Error)?.message ?? "unknown"})`,
        failed: true,
        error: (r.reason as Error)?.message,
      };
    });

    // Step 3 — Aggregate (streamed).
    const aggregatorPrompt = subResults
      .map((r) => `## ${r.role}\n${r.text}`)
      .join("\n\n");

    const aggregator = streamText({
      model: client.model,
      system: `You are JARVIS. Synthesize the sub-agent results below into ONE coherent \
reply for the user. Use ONLY the information present in these sources — do not invent \
facts. If sources contradict, mention the disagreement. Keep it focused and well-structured.`,
      messages: [
        ...messages,
        {
          role: "user",
          content: `Sub-agent results:\n\n${aggregatorPrompt}\n\nNow write the synthesized reply.`,
        },
      ],
      temperature: 0.6,
      maxOutputTokens: 4096,
      providerOptions: {
        kimi: { thinking: { type: "disabled" } },
      },
      onFinish: async ({ totalUsage }) => {
        const aggInput = totalUsage?.inputTokens ?? 0;
        const aggOutput = totalUsage?.outputTokens ?? 0;
        const cost =
          ((totalInput + aggInput) * PRICE_INPUT_PER_1K) / 1000 +
          ((totalOutput + aggOutput) * PRICE_OUTPUT_PER_1K) / 1000;
        try {
          await recordSwarmSpend(cost);
        } catch (err) {
          console.warn("[kimi-swarm] recordSwarmSpend failed:", err);
        }
      },
      onError: (err) => {
        console.error("[kimi-swarm] aggregator streamText error:", err);
      },
    });

    // Build a composite SSE stream:
    //   1. emit kimi-swarm-status data parts as a prefix (sub-agents have
    //      already settled — Promise.allSettled returned by now)
    //   2. then pipe the aggregator's UI-message stream body through
    //
    // True "live counter" updates would require refactoring fan-out to fire
    // its own status as each sub-agent lands; deferred to v2 (see plan §11).
    const encoder = new TextEncoder();
    const composite = new ReadableStream({
      async start(controller) {
        // Initial status — "0/N coordinating".
        controller.enqueue(
          encoder.encode(
            `data: ${JSON.stringify({
              type: "data-kimi-swarm-status",
              total: plan.subtasks.length,
              completed: 0,
            })}\n\n`,
          ),
        );
        // One per-completion update (replays the order the sub-agents
        // returned, so the UI can show "Latest: <role>").
        for (let i = 0; i < subResults.length; i++) {
          controller.enqueue(
            encoder.encode(
              `data: ${JSON.stringify({
                type: "data-kimi-swarm-status",
                total: plan.subtasks.length,
                completed: i + 1,
                current: subResults[i].role,
              })}\n\n`,
            ),
          );
        }
        // Pipe aggregator stream through.
        const aggResp = aggregator.toUIMessageStreamResponse();
        const reader = aggResp.body?.getReader();
        if (!reader) {
          controller.close();
          return;
        }
        try {
          while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            controller.enqueue(value);
          }
        } finally {
          reader.releaseLock();
          controller.close();
        }
      },
    });

    aggregator.consumeStream();
    return new Response(composite, {
      status: 200,
      headers: {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-store",
        "X-Kimi-Mode": "swarm",
        "X-Kimi-Swarm-Subagents": String(subResults.length),
        "X-Kimi-Swarm-Failures": String(subResults.filter((r) => r.failed).length),
      },
    });
  } catch (err) {
    return formatKimiError(err);
  }
}
