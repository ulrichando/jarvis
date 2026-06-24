import { db } from "@/lib/db";
import { getUserId } from "@/lib/auth-helpers";
import { listArtifacts, getConversationArtifacts } from "@/lib/artifacts/store";

export const runtime = "nodejs";

// GET /api/artifacts            → gallery list (metadata, newest first)
// GET /api/artifacts?conversationId=X → that conversation's artifacts WITH
//                                       versions (hydrates the in-chat panel)
export async function GET(req: Request) {
  if (!db) return Response.json({ artifacts: [] });
  const userId = await getUserId(req.headers);
  const conversationId = new URL(req.url).searchParams.get("conversationId");
  if (conversationId) {
    return Response.json({
      artifacts: await getConversationArtifacts(conversationId, userId),
    });
  }
  return Response.json({ artifacts: await listArtifacts(userId) });
}
