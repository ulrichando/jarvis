import { resolveScheduledCaller } from "@/lib/scheduled/caller-auth";
import { runScheduledChat } from "@/lib/scheduled/run";
import { listScheduledChats } from "@/lib/scheduled/store";

export const runtime = "nodejs";

// "Run now" — executes the task immediately. Returns the conversation id AND
// the generated text so the JARVIS voice `scheduled` tool can read it aloud
// without a second fetch. Accepts a browser session OR the shared voice token.
export async function POST(
  req: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  const userId = await resolveScheduledCaller(req);
  if (!userId) return new Response("Unauthorized", { status: 401 });
  const { id } = await params;
  const task = listScheduledChats().find((t) => t.id === id);
  if (!task) return Response.json({ error: "not found" }, { status: 404 });
  try {
    // Manual run-now: don't queue a voice reminder — the caller (voice tool /
    // web UI) already surfaces the text, so a poller pull would double-speak.
    const { conversationId, text } = await runScheduledChat(task, userId, false);
    return Response.json({ conversation_id: conversationId, text });
  } catch (err) {
    return Response.json(
      { error: err instanceof Error ? err.message : "run failed" },
      { status: 502 },
    );
  }
}
