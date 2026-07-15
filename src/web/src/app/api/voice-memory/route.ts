import type { UIMessage } from "ai";
import { and, asc, desc, eq, inArray } from "drizzle-orm";
import { db, schema } from "@/lib/db";
import { ensureWebSchema } from "@/lib/db/ensure-schema";
import { extractText } from "@/lib/chat/persist";
import { resolveAuth, resolveServiceUserId } from "@/lib/voice-service-auth";

export const runtime = "nodejs";

// /api/voice-memory — cloud conversation memory for the LiveKit voice agent.
//
// The voice agent stores its rolling turn history here instead of an on-disk
// JSONL, reusing the web chat tables (web.conversations / web.messages) so
// voice turns also render in the web chat UI. Each user has ONE continuous
// conversation with kind='voice' (find-or-create).
//
// Auth (both handlers): the shared dual-auth contract in
// @/lib/voice-service-auth (better-auth session OR the voice-agent proxy-JWT
// service token, sub === "voice-agent" mandatory — other subs 403; neither →
// 401). Service-path user_id is validated as a UUID (400) that exists in
// web.users (404) via resolveServiceUserId.
//
// proxy.ts allowlists this path in SELF_AUTH_POST_PATTERNS +
// SELF_AUTH_GET_PATTERNS (method-scoped, like /api/scheduled/voice-pending)
// so the bearer reaches this in-handler check instead of the shared-token gate.

const DEFAULT_LIMIT = 40;
const MAX_LIMIT = 200;
const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** The user's single continuous voice conversation, or null. */
async function findVoiceConversation(
  userId: string,
): Promise<{ id: string } | null> {
  const rows = await db!
    .select({ id: schema.conversations.id })
    .from(schema.conversations)
    .where(
      and(
        eq(schema.conversations.userId, userId),
        eq(schema.conversations.kind, "voice"),
      ),
    )
    // Deterministic winner if a duplicate ever slips in despite the partial
    // unique index: the oldest conversation is always the one.
    .orderBy(asc(schema.conversations.createdAt))
    .limit(1);
  return rows[0] ?? null;
}

export async function GET(req: Request) {
  const auth = await resolveAuth(req);
  if (auth.kind === "forbidden") return new Response("Forbidden", { status: 403 });
  if (auth.kind === "none") return new Response("Unauthorized", { status: 401 });

  // After auth (mirrors POST): 'neither auth → 401' holds even with
  // persistence disabled.
  if (!db) return Response.json({ turns: [] });
  await ensureWebSchema();

  const url = new URL(req.url);
  let userId: string;
  if (auth.kind === "session") {
    userId = auth.userId;
  } else {
    const resolved = await resolveServiceUserId(url.searchParams.get("user_id"));
    if ("error" in resolved) return resolved.error;
    userId = resolved.userId;
  }

  const rawLimit = Number.parseInt(url.searchParams.get("limit") ?? "", 10);
  const limit = Number.isFinite(rawLimit)
    ? Math.min(Math.max(rawLimit, 1), MAX_LIMIT)
    : DEFAULT_LIMIT;

  // Optional: load a SPECIFIC conversation — the chat the phone opened voice
  // from (#15), so the agent continues THAT thread instead of the standalone
  // voice conversation. Scoped to (id, userId) so a forged conversation_id on
  // the service path can't read another user's chat. Absent → the continuous
  // voice thread (default behaviour).
  const convParam = url.searchParams.get("conversation_id")?.trim();
  let convo: { id: string } | null;
  if (convParam) {
    if (!UUID_RE.test(convParam)) {
      return Response.json(
        { error: "conversation_id must be a valid UUID" },
        { status: 400 },
      );
    }
    const owned = await db
      .select({ id: schema.conversations.id })
      .from(schema.conversations)
      .where(
        and(
          eq(schema.conversations.id, convParam),
          eq(schema.conversations.userId, userId),
        ),
      )
      .limit(1);
    convo = owned[0] ?? null;
  } else {
    convo = await findVoiceConversation(userId);
  }
  if (!convo) return Response.json({ turns: [] });

  // Newest N by created_at, then reverse to chronological. Over-fetch: the
  // shared voice conversation may pick up web-chat rows whose parts carry no
  // text, and those must not consume limit slots.
  const rows = await db
    .select({ role: schema.messages.role, content: schema.messages.content })
    .from(schema.messages)
    .where(
      and(
        eq(schema.messages.conversationId, convo.id),
        inArray(schema.messages.role, ["user", "assistant"]),
      ),
    )
    .orderBy(desc(schema.messages.createdAt))
    .limit(Math.min(limit * 2, 400));

  const turns = rows
    .reverse()
    .map((r) => ({
      role: r.role as "user" | "assistant",
      text: Array.isArray(r.content)
        ? extractText(r.content as UIMessage["parts"])
        : "",
    }))
    .filter((t) => t.text.length > 0)
    .slice(-limit);

  return Response.json({ turns });
}

export async function POST(req: Request) {
  if (!db) {
    return Response.json({ error: "persistence disabled" }, { status: 503 });
  }
  await ensureWebSchema();

  const auth = await resolveAuth(req);
  if (auth.kind === "forbidden") return new Response("Forbidden", { status: 403 });
  if (auth.kind === "none") return new Response("Unauthorized", { status: 401 });

  let body: { role?: unknown; text?: unknown; user_id?: unknown };
  try {
    body = await req.json();
  } catch {
    return Response.json({ error: "invalid JSON body" }, { status: 400 });
  }

  const role = body.role;
  if (role !== "user" && role !== "assistant") {
    return Response.json(
      { error: "role must be 'user' or 'assistant'" },
      { status: 400 },
    );
  }
  const text = typeof body.text === "string" ? body.text : "";
  if (!text.trim()) {
    return Response.json(
      { error: "text must be a non-empty string" },
      { status: 400 },
    );
  }

  let userId: string;
  if (auth.kind === "session") {
    userId = auth.userId;
  } else {
    const resolved = await resolveServiceUserId(
      typeof body.user_id === "string" ? body.user_id : undefined,
    );
    if ("error" in resolved) return resolved.error;
    userId = resolved.userId;
  }

  // Find-or-create the single continuous voice conversation. The INSERT
  // tolerates losing a find-or-create race: conversations_voice_user_uniq
  // (partial unique index, ensureWebSchema) makes the duplicate a no-op
  // conflict, and we re-SELECT the winner.
  let convo = await findVoiceConversation(userId);
  if (!convo) {
    const [created] = await db
      .insert(schema.conversations)
      .values({ userId, kind: "voice", title: "Voice conversation" })
      .onConflictDoNothing()
      .returning({ id: schema.conversations.id });
    convo = created ?? (await findVoiceConversation(userId));
    if (!convo) {
      return Response.json(
        { error: "failed to create voice conversation" },
        { status: 500 },
      );
    }
  }

  // Same shape persist.ts writes for chat turns — [{type:"text",text}] parts —
  // so the web chat UI renders voice turns without translation.
  await db.insert(schema.messages).values({
    conversationId: convo.id,
    role,
    content: [{ type: "text", text }],
  });
  await db
    .update(schema.conversations)
    .set({ updatedAt: new Date() })
    .where(eq(schema.conversations.id, convo.id));

  return Response.json({ conversationId: convo.id });
}
