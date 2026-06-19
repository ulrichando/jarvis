import { generateText } from "ai";
import { z } from "zod";
import { getModel, MissingApiKeyError } from "@/lib/ai/models";
import type { Provider } from "@/lib/ai/models-meta";

export const runtime = "nodejs";
export const maxDuration = 20;

const PROBE: Record<Provider, string> = {
  anthropic: "claude-haiku-4-5",
  openai: "gpt-5-mini",
  google: "gemini-2.5-flash",
  deepseek: "deepseek-chat",
  groq: "llama-3.3-70b",
  kimi: "moonshot-v1-128k",
  ollama: "ollama-qwen3-30b-a3b",
};

const bodySchema = z.object({
  provider: z.enum([
    "anthropic",
    "openai",
    "google",
    "deepseek",
    "groq",
    "kimi",
  ]),
});

export async function POST(req: Request) {
  const parsed = bodySchema.safeParse(await req.json());
  if (!parsed.success) {
    return Response.json({ ok: false, error: "invalid request" }, { status: 400 });
  }

  const probe = PROBE[parsed.data.provider];

  try {
    const { model } = await getModel(probe);
    const started = Date.now();
    const { text } = await generateText({
      model,
      prompt: "Reply with the single word: ok",
      maxOutputTokens: 8,
    });
    return Response.json({
      ok: true,
      latencyMs: Date.now() - started,
      reply: text.trim(),
    });
  } catch (err) {
    if (err instanceof MissingApiKeyError) {
      return Response.json(
        { ok: false, error: "No API key set." },
        { status: 400 },
      );
    }
    const message = err instanceof Error ? err.message : "Unknown error";
    return Response.json({ ok: false, error: message }, { status: 502 });
  }
}
