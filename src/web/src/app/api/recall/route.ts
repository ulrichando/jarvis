import { resolveAuth, resolveServiceUserId } from "@/lib/voice-service-auth";

export const runtime = "nodejs";

// /api/recall — honcho semantic recall for the LiveKit voice agent (Phase 2 of
// cloud voice memory, next to /api/voice-memory + /api/memory).
//
// The web app is the ONLY thing that talks to honcho: the honcho v3 REST API
// (api :8000) is co-located on the VPS, internal-network only, never exposed.
// The voice worker calls THIS route; this route calls honcho.
//
// POST, two operations distinguished by body.mode:
//   { mode: "query", user_id?, query }
//     → honcho dialectic chat on the user's peer (deep semantic recall,
//       multi-second) → { text } capped at RECALL_TEXT_CAP chars. The voice
//       agent exposes this as an LLM-CHOSEN `recall` tool, never per-turn.
//   { mode: "sync", user_id?, role, text, session_id? }
//     → honcho add_messages (turn ingestion for the deriver). Fire-and-forget
//       from the voice worker's post-turn path — off the turn path.
//
// Honcho keying (verified against plastic-labs/honcho@v3.0.9):
//   workspace  "default"            (SDK default; HONCHO_WORKSPACE_ID override)
//   user peer  `user-{user_id}`     (peer-scoped deriver representation is the
//                                    per-user isolation boundary)
//   agent peer "jarvis"             (shared assistant peer)
//   session    `web-{user_id}-{session_id}` (user-namespaced so generic
//                                    LiveKit room names never collide)
// Workspace/peer/session creates are idempotent get-or-create; results are
// cached in-process so steady-state sync is a single honcho call.
//
// FAIL SOFT: if HONCHO_BASE_URL is unset, or honcho is unreachable/errors,
// query returns { text: "" } and sync no-ops — always HTTP 200, so the voice
// path never breaks when honcho isn't deployed yet. 4xx is reserved for
// auth / validation / bad user_id (the same contract as the sibling routes).
//
// Auth: the shared dual-auth contract in @/lib/voice-service-auth
// (better-auth session OR the voice-agent proxy-JWT service token,
// sub === "voice-agent" mandatory — other subs 403; neither → 401).
// proxy.ts allowlists this path in SELF_AUTH_POST_PATTERNS (method-scoped).

const RECALL_TEXT_CAP = 8000;
const AGENT_PEER_ID = "jarvis";
// Ensures are cheap get-or-create upserts — kept short so worst-case first
// contact (up to 4 ensures + the honcho op) fits inside the voice worker's
// client budgets (recall tool 30s / sync 15s) instead of burning them on
// ensure timeouts.
const ENSURE_TIMEOUT_MS = 5_000;
const SYNC_TIMEOUT_MS = 10_000;
// Dialectic chat is multi-second by design (it runs an LLM server-side).
const QUERY_TIMEOUT_MS = 30_000;

type HonchoConfig = { baseUrl: string; apiKey: string; workspace: string };

/** null ⇒ honcho not configured ⇒ every operation fails soft. */
function honchoConfig(): HonchoConfig | null {
  const baseUrl = (process.env.HONCHO_BASE_URL ?? "").trim().replace(/\/+$/, "");
  if (!baseUrl) return null;
  return {
    baseUrl,
    // With AUTH_USE_AUTH=false (JARVIS's deploy) the server ignores the
    // header entirely; "local" is the same placeholder the voice plugin sends.
    apiKey: (process.env.HONCHO_API_KEY ?? "local").trim() || "local",
    workspace: (process.env.HONCHO_WORKSPACE_ID ?? "").trim() || "default",
  };
}

async function honchoPost(
  cfg: HonchoConfig,
  path: string,
  body: unknown,
  timeoutMs: number,
): Promise<Response> {
  return fetch(`${cfg.baseUrl}${path}`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      authorization: `Bearer ${cfg.apiKey}`,
    },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(timeoutMs),
  });
}

// Idempotent get-or-create results, cached per process so steady-state sync
// is ONE honcho call instead of five. Keyed by base URL too, so flipping
// HONCHO_BASE_URL between restarts can't serve a stale "ensured" claim.
const ensured = new Set<string>();

async function ensure(
  cfg: HonchoConfig,
  cacheKey: string,
  path: string,
  id: string,
): Promise<void> {
  const key = `${cfg.baseUrl}|${cfg.workspace}|${cacheKey}:${id}`;
  if (ensured.has(key)) return;
  const res = await honchoPost(cfg, path, { id }, ENSURE_TIMEOUT_MS);
  if (!res.ok) throw new Error(`honcho ensure ${path} → ${res.status}`);
  ensured.add(key);
}

const ensureWorkspace = (cfg: HonchoConfig) =>
  ensure(cfg, "workspace", "/v3/workspaces", cfg.workspace);
const ensurePeer = (cfg: HonchoConfig, peerId: string) =>
  ensure(cfg, "peer", `/v3/workspaces/${encodeURIComponent(cfg.workspace)}/peers`, peerId);
const ensureSession = (cfg: HonchoConfig, sessionId: string) =>
  ensure(cfg, "session", `/v3/workspaces/${encodeURIComponent(cfg.workspace)}/sessions`, sessionId);

const userPeerId = (userId: string) => `user-${userId}`;

/** User-namespaced honcho session id from an untrusted room/session name. */
function honchoSessionId(raw: unknown, userId: string): string {
  const base = typeof raw === "string" && raw.trim() ? raw.trim() : "voice";
  const safe = base.replace(/[^a-zA-Z0-9._-]/g, "-").slice(0, 96);
  return `web-${userId}-${safe}`;
}

export async function POST(req: Request) {
  const auth = await resolveAuth(req);
  if (auth.kind === "forbidden") return new Response("Forbidden", { status: 403 });
  if (auth.kind === "none") return new Response("Unauthorized", { status: 401 });

  let body: {
    mode?: unknown;
    user_id?: unknown;
    query?: unknown;
    role?: unknown;
    text?: unknown;
    session_id?: unknown;
  };
  try {
    body = await req.json();
  } catch {
    return Response.json({ error: "invalid JSON body" }, { status: 400 });
  }

  const mode = body.mode;
  if (mode !== "query" && mode !== "sync") {
    return Response.json(
      { error: "mode must be 'query' or 'sync'" },
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

  const cfg = honchoConfig();

  if (mode === "query") {
    const query = typeof body.query === "string" ? body.query.trim() : "";
    if (!query) {
      return Response.json(
        { error: "query must be a non-empty string" },
        { status: 400 },
      );
    }
    // Honcho not deployed → soft empty recall, never a voice-path error.
    if (!cfg) return Response.json({ text: "" });

    try {
      await ensureWorkspace(cfg);
      const peerId = userPeerId(userId);
      await ensurePeer(cfg, peerId);
      const res = await honchoPost(
        cfg,
        `/v3/workspaces/${encodeURIComponent(cfg.workspace)}/peers/${encodeURIComponent(peerId)}/chat`,
        { query, stream: false, reasoning_level: "low" },
        QUERY_TIMEOUT_MS,
      );
      if (!res.ok) throw new Error(`honcho dialectic chat → ${res.status}`);
      const data = (await res.json()) as { content?: unknown };
      const text = typeof data?.content === "string" ? data.content : "";
      return Response.json({ text: text.slice(0, RECALL_TEXT_CAP) });
    } catch (err) {
      // Unreachable/erroring honcho fails SOFT with the reason surfaced for
      // observability — the recall tool reads text, logs error.
      // Drop cached ensures: if honcho's Postgres was wiped while this
      // process stayed up, the next request re-runs get-or-create and
      // self-heals instead of 404ing until a web restart.
      ensured.clear();
      console.error("[/api/recall] query failed soft:", err);
      return Response.json({ text: "", error: "recall backend unavailable" });
    }
  }

  // mode === "sync"
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
  // Honcho not deployed → the sync is a documented no-op.
  if (!cfg) return Response.json({ ok: true, skipped: true });

  try {
    const peerId = role === "user" ? userPeerId(userId) : AGENT_PEER_ID;
    const sessionId = honchoSessionId(body.session_id, userId);
    await ensureWorkspace(cfg);
    // Both peers up front: honcho sessions/messages reference peers by id,
    // and the deriver models the user peer against the jarvis peer.
    await ensurePeer(cfg, userPeerId(userId));
    await ensurePeer(cfg, AGENT_PEER_ID);
    await ensureSession(cfg, sessionId);
    const res = await honchoPost(
      cfg,
      `/v3/workspaces/${encodeURIComponent(cfg.workspace)}/sessions/${encodeURIComponent(sessionId)}/messages`,
      { messages: [{ content: text, peer_id: peerId }] },
      SYNC_TIMEOUT_MS,
    );
    if (!res.ok) throw new Error(`honcho add_messages → ${res.status}`);
    return Response.json({ ok: true });
  } catch (err) {
    // The voice worker fires-and-forgets; a failed sync must never surface
    // as a turn failure. 200 + ok:false keeps it observable, non-breaking.
    // Drop cached ensures so a wiped honcho self-heals on the next request
    // (see the query catch above).
    ensured.clear();
    console.error("[/api/recall] sync failed soft:", err);
    return Response.json({ ok: false, error: "recall backend unavailable" });
  }
}
