import { db } from "@/lib/db";
import { requireUserId, Unauthenticated } from "@/lib/auth-helpers";
import { listArtifacts, getConversationArtifacts } from "@/lib/artifacts/store";

export const runtime = "nodejs";

// GET /api/artifacts            → gallery list (metadata, newest first)
// GET /api/artifacts?conversationId=X → that conversation's artifacts WITH
//                                       versions (hydrates the in-chat panel)
export async function GET(req: Request) {
  if (!db) return Response.json({ artifacts: [] });
  let userId: string;
  try {
    userId = await requireUserId(req.headers);
  } catch (e) {
    if (e instanceof Unauthenticated) return new Response("Unauthorized", { status: 401 });
    throw e;
  }
  const conversationId = new URL(req.url).searchParams.get("conversationId");
  if (conversationId) {
    return Response.json({
      artifacts: await getConversationArtifacts(conversationId, userId),
    });
  }
  return Response.json({ artifacts: await listArtifacts(userId) });
}
