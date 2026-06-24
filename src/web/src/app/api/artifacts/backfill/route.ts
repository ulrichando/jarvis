import { db } from "@/lib/db";
import { getUserId } from "@/lib/auth-helpers";
import { backfillArtifactsForUser } from "@/lib/artifacts/store";

export const runtime = "nodejs";
// Scanning the full history can take a moment on large accounts.
export const maxDuration = 120;

// POST → one-time (idempotent) scan of the user's chat history to populate
// the artifacts gallery from past conversations, the way claude.ai's library
// aggregates artifacts across all chats.
export async function POST(req: Request) {
  if (!db) return new Response("Persistence disabled", { status: 503 });
  const userId = await getUserId(req.headers);
  const result = await backfillArtifactsForUser(userId);
  return Response.json(result);
}
