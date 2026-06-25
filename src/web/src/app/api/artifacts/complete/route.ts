import { generateText } from "ai";
import { getModel } from "@/lib/ai/models";
import { loadSettings } from "@/lib/settings/store";
import { withUser } from "@/lib/auth-route";

export const runtime = "nodejs";
export const maxDuration = 60;

// Runtime LLM call for AI-powered artifacts. An artifact's JS calls
// `window.jarvis.complete(prompt)` → the parent page (authed) POSTs here →
// a single completion on the user's default model. Mirrors claude.ai's
// `window.claude.complete`. Authed via the same-origin session (the iframe
// never calls this directly — the parent does), so a logged-out public
// viewer can't invoke it. Prompt + output are capped to bound cost.
export async function POST(req: Request) {
  return withUser(req, async () => {
    let body: { prompt?: unknown; system?: unknown } = {};
    try {
      body = await req.json();
    } catch {
      return Response.json({ error: "invalid body" }, { status: 400 });
    }
    const prompt = typeof body.prompt === "string" ? body.prompt.slice(0, 20000) : "";
    if (!prompt.trim()) {
      return Response.json({ error: "missing prompt" }, { status: 400 });
    }
    const system =
      typeof body.system === "string" ? body.system.slice(0, 8000) : undefined;
    try {
      const settings = await loadSettings();
      const { model } = await getModel(settings.defaults.model);
      const { text } = await generateText({
        model,
        system,
        prompt,
        maxOutputTokens: 2048,
      });
      return Response.json({ text });
    } catch (e) {
      return Response.json(
        { error: String((e as Error)?.message ?? e) },
        { status: 500 },
      );
    }
  });
}
