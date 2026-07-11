import { requireUserId, Unauthenticated } from "@/lib/auth-helpers";
import { runScheduledChat } from "@/lib/scheduled/run";
import { listScheduledChats } from "@/lib/scheduled/store";

export const runtime = "nodejs";

// "Run now" — executes the task immediately and returns the conversation
// the run landed in so the client can open it.
export async function POST(
  req: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  let userId: string;
  try {
    userId = await requireUserId(req.headers);
  } catch (e) {
    if (e instanceof Unauthenticated)
      return new Response("Unauthorized", { status: 401 });
    throw e;
  }
  const { id } = await params;
  const task = listScheduledChats().find((t) => t.id === id);
  if (!task) return Response.json({ error: "not found" }, { status: 404 });
  try {
    const { conversationId } = await runScheduledChat(task, userId);
    return Response.json({ conversation_id: conversationId });
  } catch (err) {
    return Response.json(
      { error: err instanceof Error ? err.message : "run failed" },
      { status: 502 },
    );
  }
}
