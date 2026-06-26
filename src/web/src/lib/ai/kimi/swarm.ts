import "server-only";
import { randomUUID } from "node:crypto";
import {
  convertToModelMessages,
  createUIMessageStream,
  createUIMessageStreamResponse,
  generateObject,
  generateText,
  streamText,
} from "ai";
import { z } from "zod";
import {
  buildKimiClient,
  extractMessagesForKimi,
  formatKimiError,
  KIMI_TEMPERATURE,
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
  // Returned as a 429 BEFORE we open the stream so the client can show a
  // proper toast instead of a half-open SSE.
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

  // Open the response stream IMMEDIATELY. The chat client has a 60s
  // first-byte watchdog; before this restructure, decompose+fan-out
  // (~9-15s) blocked the first byte and pushed complex queries into
  // toast-warning territory. Now we emit an initial swarm-status data
  // part within ~100ms so the UI can show "Coordinating agents…" while
  // the long async work happens inside `execute`.
  const sessionId = `${Date.now()}-${randomUUID().slice(0, 8)}`;
  const cacheKey = `swarm-${sessionId}`;

  const composite = createUIMessageStream({
    execute: async ({ writer }) => {
      // Step 0 — Emit "planning" status synchronously so the client
      // gets bytes within milliseconds. total=0 because we don't know
      // the subtask count yet; UI shows an indeterminate "Planning…".
      writer.write({
        type: "data-kimi-swarm-status",
        data: { total: 0, completed: 0, current: "Planning…" },
      });

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

      // Step 2a — Empty plan: fall back to Instant single call inside
      // the same stream. Client sees: planning status → instant text.
      if (!plan.subtasks || plan.subtasks.length === 0) {
        writer.write({
          type: "data-kimi-swarm-status",
          data: { total: 1, completed: 0, current: "Single response (trivial query)" },
        });
        const fallback = streamText({
          model: client.model,
          system: loadKimiPersona(),
          messages,
          temperature: KIMI_TEMPERATURE,
          maxOutputTokens: 1024,
          providerOptions: {
            kimi: { thinking: { type: "disabled" } },
          },
        });
        writer.merge(fallback.toUIMessageStream());
        return;
      }

      // Step 2b — Plan ready: announce subtask count.
      writer.write({
        type: "data-kimi-swarm-status",
        data: {
          total: plan.subtasks.length,
          completed: 0,
          current: "Coordinating agents…",
        },
      });

      // Step 2c — Fan out. Each sub-agent emits its own progress event
      // when it lands, so the UI can show "1/3 → 2/3 → 3/3" live
      // instead of snapping from 0/N to N/N.
      let completed = 0;
      const wrapped = plan.subtasks.map(async (t) => {
        try {
          const r = await generateText({
            model: client.model,
            system: `You are a sub-agent with role "${t.role}". Answer only your task.`,
            prompt: t.prompt,
            temperature: KIMI_TEMPERATURE,
            maxOutputTokens: 800,
            providerOptions: {
              kimi: {
                thinking: { type: "disabled" },
                prompt_cache_key: cacheKey,
              },
            },
          });
          completed += 1;
          writer.write({
            type: "data-kimi-swarm-status",
            data: {
              total: plan.subtasks.length,
              completed,
              current: t.role,
            },
          });
          return { ok: true as const, role: t.role, value: r };
        } catch (err) {
          completed += 1;
          writer.write({
            type: "data-kimi-swarm-status",
            data: {
              total: plan.subtasks.length,
              completed,
              current: `${t.role} (failed)`,
            },
          });
          return { ok: false as const, role: t.role, error: err as Error };
        }
      });

      const settled = await Promise.all(wrapped);
      let totalInput = 0;
      let totalOutput = 0;
      // Known limitation (I2): failed sub-agent token usage is not
      // counted (no `usage` object on rejection). Small ledger drift on
      // partial failures — acceptable; failed calls produce minimal
      // output. Not user-facing.
      const subResults: SubResult[] = settled.map((r) => {
        if (r.ok) {
          totalInput += r.value.usage?.inputTokens ?? 0;
          totalOutput += r.value.usage?.outputTokens ?? 0;
          return { role: r.role, text: r.value.text };
        }
        return {
          role: r.role,
          text: `(this sub-agent failed: ${r.error?.message ?? "unknown"})`,
          failed: true,
          error: r.error?.message,
        };
      });

      // Step 3 — Aggregate (streamed into the same composite).
      writer.write({
        type: "data-kimi-swarm-status",
        data: {
          total: plan.subtasks.length,
          completed: plan.subtasks.length,
          current: "Synthesizing…",
        },
      });

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
        temperature: KIMI_TEMPERATURE,
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

      writer.merge(aggregator.toUIMessageStream());
    },
    onError: (err) => {
      console.error("[kimi-swarm] composite stream error:", err);
      return (err as Error)?.message ?? "swarm composite failed";
    },
  });

  return createUIMessageStreamResponse({
    stream: composite,
    headers: {
      "X-Kimi-Mode": "swarm",
    },
  });
}
