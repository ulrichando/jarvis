import { inArray } from "drizzle-orm";
import { db, schema } from "@/lib/db";
import { ensureWebSchema } from "@/lib/db/ensure-schema";
import { requireUserIdOrSharedLocal, Unauthenticated } from "@/lib/auth-helpers";
import {
  upsertConversations,
  upsertMessages,
  type SyncConversationInput,
  type SyncMessageInput,
} from "@/lib/chat/persist";

export const runtime = "nodejs";

// Two-way mobile sync — the phone→web push leg. The Android SyncWorker POSTs
// its dirty conversations + finalized messages here with a per-user bridge
// token (jbr_, resolved by requireUserIdOrSharedLocal; route is on proxy.ts
// SELF_AUTH_POST). Every write is idempotent (conversations LWW by updatedAt,
// messages ON CONFLICT id DO NOTHING) so a retry/re-push is always a no-op.
export async function POST(req: Request) {
  if (!db) return new Response("Persistence disabled", { status: 503 });
  await ensureWebSchema();

  let userId: string;
  try {
    userId = await requireUserIdOrSharedLocal(req.headers);
  } catch (e) {
    if (e instanceof Unauthenticated) return new Response("Unauthorized", { status: 401 });
    throw e;
  }

  const body = (await req.json().catch(() => null)) as {
    conversations?: SyncConversationInput[];
    messages?: SyncMessageInput[];
  } | null;
  if (!body) return new Response("Bad JSON", { status: 400 });

  const conversations = Array.isArray(body.conversations) ? body.conversations : [];
  const messages = Array.isArray(body.messages) ? body.messages : [];
  // Sanity cap — the phone batches ≤200 msgs/push; reject a runaway payload.
  if (conversations.length > 500 || messages.length > 2000) {
    return new Response("Batch too large", { status: 413 });
  }

  // IDOR guard: reject the whole batch if any referenced conversation id is
  // already owned by a different user (mirrors the projectId check in PATCH).
  const referencedConvIds = [
    ...new Set([
      ...conversations.map((c) => c?.id).filter(Boolean),
      ...messages.map((m) => m?.conversationId).filter(Boolean),
    ]),
  ] as string[];
  if (referencedConvIds.length > 0) {
    const existing = await db
      .select({ id: schema.conversations.id, userId: schema.conversations.userId })
      .from(schema.conversations)
      .where(inArray(schema.conversations.id, referencedConvIds));
    if (existing.some((r) => r.userId !== userId)) {
      return new Response("Forbidden", { status: 403 });
    }
  }

  // ponytail: no explicit transaction — every write is idempotent, so a partial
  // failure just gets re-pushed next cycle with no dupes. Add a tx if concurrent
  // multi-device pushes ever race on the same conversation.
  const convResults = await upsertConversations(userId, conversations);
  const msgApplied = await upsertMessages(userId, messages);

  return Response.json({
    conversations: convResults,
    messages: msgApplied.map((id) => ({ id, applied: true })),
  });
}
