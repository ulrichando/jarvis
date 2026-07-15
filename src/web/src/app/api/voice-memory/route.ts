import type { UIMessage } from "ai";
import { and, asc, desc, eq, inArray } from "drizzle-orm";
import { db, schema } from "@/lib/db";
import { ensureWebSchema } from "@/lib/db/ensure-schema";
import { getUserId } from "@/lib/auth-helpers";
import { extractText } from "@/lib/chat/persist";
import { verifyProxyToken } from "@/lib/bridge/proxyJwt";
import { getOrCreateProxyJwtSecret } from "@/lib/bridge/proxySecret";

export const runtime = "nodejs";

// /api/voice-memory — cloud conversation memory for the LiveKit voice agent.
//
// The voice agent stores its rolling turn history here instead of an on-disk
// JSONL, reusing the web chat tables (web.conversations / web.messages) so
// voice turns also render in the web chat UI. Each user has ONE continuous
// conversation with kind='voice' (find-or-create).
//
// Auth (both handlers), in order:
//   1. better-auth session cookie (getUserId) → user_id = the session user.
//   2. The voice-agent SERVICE token: a proxy JWT (signProxyToken /
//      verifyProxyToken, HS256 with the shared keys.env secret) whose
//      sub === "voice-agent". MANDATORY sub check — a valid proxy JWT minted
//      for any OTHER sub (e.g. a per-user gateway token) is 403, never a
//      user-impersonation path. For the service path user_id comes from the
//      request (query param on GET, body on POST) and is validated as a UUID
//      (400) that exists in web.users (404).
//   3. Neither → 401.
//
// proxy.ts allowlists this path in SELF_AUTH_POST_PATTERNS +
// SELF_AUTH_GET_PATTERNS (method-scoped, like /api/scheduled/voice-pending)
// so the bearer reaches this in-handler check instead of the shared-token gate.

const SERVICE_SUB = "voice-agent";
const DEFAULT_LIMIT = 40;
const MAX_LIMIT = 200;
const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

type AuthResult =
  | { kind: "session"; userId: string }
  | { kind: "service" }
  | { kind: "forbidden" }
  | { kind: "none" };

async function resolveAuth(req: Request): Promise<AuthResult> {
  const sessionUserId = await getUserId(req.headers);
  if (sessionUserId) return { kind: "session", userId: sessionUserId };
  const authz = req.headers.get("authorization") ?? "";
  const bearer = /^bearer /i.test(authz) ? authz.slice(7).trim() : "";
  if (bearer) {
    const result = verifyProxyToken(bearer, getOrCreateProxyJwtSecret());
    if (result.ok) {
      // Valid signature but wrong principal — explicitly forbidden (403),
      // NOT a fall-through to 401: per-user gateway tokens must never reach
      // the arbitrary-user_id service path.
      if (result.claims.sub !== SERVICE_SUB) return { kind: "forbidden" };
      return { kind: "service" };
    }
  }
  return { kind: "none" };
}

/** Service-path user_id: syntactic UUID check (400) + existence (404). */
async function resolveServiceUserId(
  raw: string | null | undefined,
): Promise<{ userId: string } | { error: Response }> {
  const candidate = typeof raw === "string" ? raw.trim() : "";
  if (!candidate || !UUID_RE.test(candidate)) {
    return {
      error: Response.json(
        { error: "user_id must be a valid UUID" },
        { status: 400 },
      ),
    };
  }
  const rows = await db!
    .select({ id: schema.users.id })
    .from(schema.users)
    .where(eq(schema.users.id, candidate))
    .limit(1);
  if (rows.length === 0) {
    return {
      error: Response.json({ error: "unknown user_id" }, { status: 404 }),
    };
  }
  return { userId: candidate };
}

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

  const convo = await findVoiceConversation(userId);
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
